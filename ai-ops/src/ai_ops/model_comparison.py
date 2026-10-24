"""정제 비교의 비공개 미리보기·일회 승인·고정 예산·보관을 관리한다."""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time

from ai_ops.contracts import _utc_datetime, canonical_json_bytes, sha256_json
from ai_ops.model_projection import ModelView, Projection, parse_projection
from ai_ops.path_policy import PERSONAL_ROOT, permitted_path
from ai_ops.projected_model_engine import (CallPlan, PROMPT_VERSION, ProjectionModelFailure,
    ProjectionAnalysisReport, generate_once, request_templates, validate_projected_report)


DEFAULT_ROOT = PERSONAL_ROOT / "model-comparisons"


def utc_text(value):
    return _utc_datetime(value).isoformat().replace("+00:00", "Z")


def rules_projection(view: ModelView) -> ProjectionAnalysisReport:
    """같은 view의 관측값만 설명하고 서비스 정상·정확한 원인을 추정하지 않는다."""
    facts = []
    for item in view.items:
        if item.payload is not None:
            facts.append({"statement": "관측 " + item.kind + ": " +
                          canonical_json_bytes(item.payload.model_dump(mode="json")).decode(),
                          "evidence_ids": [item.evidence_id]})
    value = {"input_hash": sha256_json(view.model_dump(mode="json")), "analysis_status": "partial",
        "diagnosis_status": "insufficient_evidence", "current_state": "unknown", "facts": facts,
        "hypotheses": [], "limitations": ["정제된 과거 관측이며 로그·이벤트·배포·CI 원문은 제외됐다.",
                                           "scrape 품질의 원래 표본 수는 source snapshot에 보관되지 않아 확인하지 못했다.",
                                           "현재 사용자 경로·서비스 정상·복구 완료는 확인하지 않았다."],
        "suggested_actions": []}
    return validate_projected_report(value, view)


class ComparisonStore:
    """동일 OS 사용자 신뢰 안에서 해시 교체·중복 사용을 검사한다. OS sandbox는 아니다."""

    def __init__(self, root=DEFAULT_ROOT, *, now=None, tick=None):
        self.root = permitted_path(Path(root), "store")
        # 경로 정규화로 링크를 숨기지 않고 생성 전에 각 부모를 검사한다.
        for p in (Path(root), *Path(root).parents):
            if p.is_symlink():
                raise ValueError("comparison_symlink_rejected")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_uid != os.getuid() or self.root.stat().st_mode & 0o077:
            raise ValueError("comparison_private_directory_required")
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.tick = tick or time.monotonic
        with self.locked():
            key = self.root / ".key"
            if not key.exists():
                fd = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(secrets.token_bytes(32)); f.flush(); os.fsync(f.fileno())
            self.key = self._bytes(key, 32)
            if len(self.key) != 32:
                raise ValueError("comparison_key_rejected")

    @contextlib.contextmanager
    def locked(self):
        path = self.root / ".lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        if os.fstat(fd).st_mode & 0o077:
            os.close(fd)
            raise ValueError("comparison_lock_permissions")
        with os.fdopen(fd, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def _bytes(self, path, limit=1024 * 1024):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            info = os.fstat(f.fileno())
            import stat
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > limit):
                raise ValueError("comparison_file_rejected")
            raw = f.read(limit+1)
            if len(raw) > limit:
                raise ValueError("comparison_file_too_large")
            return raw

    def _path(self, identifier):
        if not re.fullmatch(r"cmp-[0-9a-f]{32}", identifier):
            raise ValueError("comparison_id_rejected")
        return self.root / (identifier + ".json")

    def _sign(self, data):
        return hmac.new(self.key, canonical_json_bytes(data), hashlib.sha256).hexdigest()

    def _write(self, path, data):
        if path.is_symlink():
            raise ValueError("comparison_symlink_rejected")
        envelope = {"data": data, "hmac": self._sign(data)}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".pending-", delete=False) as f:
                temporary = Path(f.name); os.fchmod(f.fileno(), 0o600)
                f.write(canonical_json_bytes(envelope)); f.flush(); os.fsync(f.fileno())
            os.replace(temporary, path)
            fd = os.open(self.root, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _read(self, path):
        envelope = json.loads(self._bytes(path))
        if (set(envelope) != {"data", "hmac"} or not isinstance(envelope["hmac"], str)
                or not hmac.compare_digest(self._sign(envelope["data"]), envelope["hmac"])):
            raise ValueError("comparison_integrity_rejected")
        return envelope["data"]

    def _validated(self, identifier):
        record = self._read(self._path(identifier))
        if record["id"] != identifier or record["state"] not in {
                "prepared", "approved", "sending", "failed", "needs_review", "purged"}:
            raise ValueError("comparison_record_rejected")
        if record["state"] == "purged":
            raise ValueError("comparison_purged")
        projection = parse_projection(canonical_json_bytes(record["projection"]))
        plan = CallPlan.model_validate_json(canonical_json_bytes(record["plan"]))
        templates = request_templates(projection.view, plan)
        if sha256_json(templates) != record["templates_hash"]:
            raise ValueError("comparison_code_or_input_changed")
        bound = {k: record[k] for k in ("projection", "plan", "templates_hash", "implementation_hash", "prompt_version")}
        if sha256_json(bound) != record["binding_hash"] or record["implementation_hash"] != implementation_hash():
            raise ValueError("comparison_code_or_input_changed")
        if record.get("rules") != rules_projection(projection.view).model_dump(mode="json"):
            raise ValueError("comparison_rules_changed")
        if "model_report" in record:
            validate_projected_report(record["model_report"], projection.view)
        return record, projection, plan, templates

    def prepare(self, projection: Projection, plan: CallPlan):
        projection = parse_projection(canonical_json_bytes(projection.model_dump(mode="json")))
        plan = CallPlan.model_validate_json(plan.model_dump_json())
        if not any(x.source_status == "available" and x.payload is not None for x in projection.view.items):
            raise ValueError("projection_no_available_evidence")
        templates = request_templates(projection.view, plan)
        bound = {"projection": projection.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
                 "templates_hash": sha256_json(templates), "implementation_hash": implementation_hash(),
                 "prompt_version": PROMPT_VERSION}
        digest = sha256_json(bound); identifier = "cmp-" + digest[:32]
        now = _utc_datetime(self.now())
        record = {**bound, "id": identifier, "binding_hash": digest, "state": "prepared",
                  "created_at": utc_text(now), "expires_at": utc_text(now + timedelta(days=7)),
                  "rules": rules_projection(projection.view).model_dump(mode="json")}
        with self.locked():
            path = self._path(identifier)
            if path.exists():
                previous = self._read(path)
                if previous.get("binding_hash") != digest:
                    raise ValueError("comparison_identity_conflict")
                return identifier
            self._write(path, record)
        return identifier

    def preview(self, identifier):
        with self.locked():
            record, projection, plan, templates = self._validated(identifier)
            return {"id": identifier, "state": record["state"], "binding_hash": record["binding_hash"],
                    "view_hash": projection.view_hash, "plan": plan.model_dump(mode="json"),
                    "external_requests": templates,
                    "retention": {"local_days": 7, "budget_days": 90,
                                  "provider_store_false_is_not_zero_retention": True,
                                  "provider_abuse_monitoring_standard_days": 30,
                                  "account_optional_data_sharing_checked_automatically": False,
                                  "notice": "store=false는 공급자 보관 전체를 없애지 않습니다. 계정 선택적 데이터 공유 설정은 운영자가 확인해야 합니다."}}

    def approve(self, identifier, *, binding_hash, actor, acknowledge_provider_retention=False,
                confirm_optional_data_sharing_off=False):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", actor):
            raise ValueError("comparison_actor_rejected")
        with self.locked():
            record, _, plan, _ = self._validated(identifier)
            if plan.execution_mode == "live" and acknowledge_provider_retention is not True:
                raise ValueError("comparison_provider_retention_ack_required")
            if plan.execution_mode == "live" and confirm_optional_data_sharing_off is not True:
                raise ValueError("comparison_account_data_controls_confirmation_required")
            now = _utc_datetime(self.now())
            if (record["state"] != "prepared" or binding_hash != record["binding_hash"]
                    or not _utc_datetime(record["created_at"]) <= now < _utc_datetime(record["expires_at"])):
                raise ValueError("comparison_approval_rejected")
            price_day = datetime.fromisoformat(plan.price.checked_on).date()
            if not 0 <= (now.date()-price_day).days <= 7:
                raise ValueError("comparison_price_check_expired")
            record.update(state="approved", approval={"actor": actor, "binding_hash": binding_hash,
                "provider_retention_acknowledged": acknowledge_provider_retention,
                "optional_data_sharing_off_confirmed_by_operator": confirm_optional_data_sharing_off,
                "approved_at": utc_text(now), "expires_at": utc_text(min(now+timedelta(minutes=30),
                                                      _utc_datetime(record["expires_at"])))})
            self._write(self._path(identifier), record)

    def write_preview(self, identifier):
        """실제 두 요청을 이스케이프한 비공개 HTML로 저장하고 본문 보관 기한에 연결한다."""
        import html
        preview = self.preview(identifier)
        payload = html.escape(json.dumps(preview, ensure_ascii=False, indent=2))
        content = ("<!doctype html><html lang='ko'><meta charset='utf-8'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            "<title>실습 모델 전송 미리보기</title><style>body{max-width:1100px;margin:32px auto;"
            "font:16px/1.7 system-ui;padding:0 24px}pre{white-space:pre-wrap;word-break:break-word;"
            "background:#f4f6f9;padding:20px;font:13px/1.6 monospace}</style>"
            "<h1>실습 모델 전송 미리보기</h1><p>아직 승인·외부 전송하지 않았습니다. 아래 두 요청의 본문만 전송 대상입니다. "
            "credential_fingerprint 등 plan 메타데이터는 본문에 포함되지 않습니다.</p>"
            "<p>비용 상한 건당 $0.10·UTC 일일 $1, 생성 최대 1회. store=false여도 공급자의 "
            "기본 abuse monitoring 보관은 최대 30일이며 예외가 있을 수 있습니다. 계정의 선택적 데이터 공유 설정은 "
            "자동 확인하지 않았습니다. 로그·이벤트·배포/CI 원문은 제외했습니다.</p><pre>"+payload+"</pre></html>")
        with self.locked():
            record, _, _, _ = self._validated(identifier)
            if record["binding_hash"] != preview["binding_hash"]:
                raise ValueError("comparison_preview_changed")
            path = self.root / ("preview-"+identifier+".html")
            if path.is_symlink():
                raise ValueError("comparison_symlink_rejected")
            fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as f:
                os.fchmod(f.fileno(), 0o600); f.write(content); f.flush(); os.fsync(f.fileno())
            return path

    def _reserve(self, now, mode="mock"):
        path = self.root / ("budget.json" if mode == "mock" else "budget-live.json")
        ledger = self._read(path) if path.exists() else {"execution_mode": mode, "days": {}, "last_at": utc_text(now)}
        if ledger.get("execution_mode") != mode:
            raise ValueError("comparison_budget_mode_rejected")
        if now < _utc_datetime(ledger["last_at"]):
            raise ValueError("comparison_clock_moved_backwards")
        key = now.date().isoformat(); amount = ledger["days"].get(key, 0)
        if type(amount) is not int or not 0 <= amount <= 90:
            raise ValueError("comparison_daily_budget_limit")
        ledger["days"][key] = amount + 10
        ledger["last_at"] = utc_text(now)
        self._write(path, ledger)

    def run(self, identifier, *, transport=None):
        # 모의 승인을 실제 전송에 사용할 수 없도록 실행 계획과 전송부를 함께 확인한다.
        if transport is None or getattr(transport, "comparison_mode", None) not in {"mock", "live"}:
            raise ValueError("comparison_transport_not_enabled")
        with self.locked():
            record, projection, plan, templates = self._validated(identifier)
            if (getattr(transport, "comparison_mode", None) != plan.execution_mode or
                    (plan.execution_mode == "live" and getattr(transport, "expected_credential", None) != plan.credential_fingerprint)):
                raise ValueError("comparison_transport_plan_mismatch")
            if record["state"] == "sending":
                record.update(state="needs_review", reason="projection_interrupted_unknown")
                self._write(self._path(identifier), record)
                raise ValueError("comparison_previous_send_unknown")
            if record["state"] in {"failed", "needs_review"}:
                return record
            now = _utc_datetime(self.now()); approval = record.get("approval", {})
            if (record["state"] != "approved" or approval.get("binding_hash") != record["binding_hash"]
                    or not _utc_datetime(approval["approved_at"]) <= now < _utc_datetime(approval["expires_at"])):
                raise ValueError("comparison_not_approved_or_expired")
            if plan.execution_mode == "live" and approval.get("provider_retention_acknowledged") is not True:
                raise ValueError("comparison_provider_retention_ack_required")
            if plan.execution_mode == "live" and approval.get("optional_data_sharing_off_confirmed_by_operator") is not True:
                raise ValueError("comparison_account_data_controls_confirmation_required")
            if not 0 <= (now.date()-datetime.fromisoformat(plan.price.checked_on).date()).days <= 7:
                raise ValueError("comparison_price_check_expired")
            self._reserve(now, plan.execution_mode)
            record.update(state="sending", started_at=utc_text(now), reserved_usd=0.10,
                          transport_kind=plan.execution_mode, external_model_calls=0)
            self._write(self._path(identifier), record)
            # 재실행은 같은 파일 잠금으로 배제한다. 전송 시작 기록 뒤 프로세스 종료는 재시도하지 않는다.
            try:
                draft, usage, model = generate_once(templates, plan, transport, clock=self.tick)
                report = validate_projected_report(draft, projection.view)
                record.update(state="needs_review", reason="semantic_review_required", usage=usage,
                              response_model=model, model_report=report.model_dump(mode="json"))
            except ProjectionModelFailure as error:
                record.update(state="needs_review", reason=error.reason, usage=error.usage)
            except Exception:
                record.update(state="failed", reason="projection_report_contract_rejected",
                              usage=locals().get("usage"))
            record["finished_at"] = utc_text(self.now())
            if plan.execution_mode == "live":
                record["external_model_calls"] = getattr(transport, "attempts", {}).get("/v1/responses", 0)
                record["external_request_attempts"] = dict(getattr(transport, "attempts", {}))
            self._write(self._path(identifier), record)
            return record

    def show(self, identifier):
        with self.locked():
            record, _, _, _ = self._validated(identifier)
            return record

    def purge(self, *, apply=False):
        """본문 파일을 연계 삭제하고 사용 이력 tombstone과 당일 예약은 남긴다."""
        with self.locked():
            now = _utc_datetime(self.now()); expired = []
            for path in sorted(self.root.glob("cmp-*.json")):
                record = self._read(path)
                if record.get("state") == "purged":
                    continue
                if _utc_datetime(record["expires_at"]) <= now:
                    expired.append(record["id"])
                    if apply:
                        preview = self.root/("preview-"+record["id"]+".html")
                        if preview.is_symlink():
                            raise ValueError("comparison_symlink_rejected")
                        preview.unlink(missing_ok=True)
                        self._write(path, {"id": record["id"], "binding_hash": record["binding_hash"],
                            "state": "purged", "purged_at": utc_text(now)})
            # 원자 저장 중 프로세스가 종료돼 남은 임시 본문도 7일 기한으로 정리한다.
            orphans = []
            for temporary in sorted(self.root.glob(".pending-*")):
                if temporary.is_symlink() or not temporary.is_file():
                    raise ValueError("comparison_temporary_file_rejected")
                if now.timestamp()-temporary.stat().st_mtime >= 7*86400:
                    orphans.append(temporary.name)
                    if apply:
                        temporary.unlink()
            for name in ("budget.json", "budget-live.json"):
                path = self.root / name
                if apply and path.exists():
                    ledger = self._read(path)
                    if now < _utc_datetime(ledger["last_at"]):
                        raise ValueError("comparison_clock_moved_backwards")
                    ledger["days"] = {d: c for d, c in ledger["days"].items()
                                      if (now.date()-datetime.fromisoformat(d).date()).days < 90}
                    self._write(path, ledger)
            return {"mode": "apply" if apply else "preview", "expired": expired, "orphan_files": orphans}


def implementation_hash():
    """승인 후 전송·필드 정책 코드 교체도 새 승인이 필요하도록 결합한다."""
    base = Path(__file__).parent
    return sha256_json({n: hashlib.sha256((base/n).read_bytes()).hexdigest() for n in
        ("model_projection.py", "model_comparison.py", "projected_model_engine.py", "projected_model_transport.py")})
