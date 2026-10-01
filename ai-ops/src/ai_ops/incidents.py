"""로컬 사건·실행 상태와 정제 입력·규칙 보고서를 원자적으로 보존한다."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from ai_ops.contracts import (
    AnalysisReport, EvidenceBundle, IncidentRequest, TargetRegistry,
    canonical_json_bytes, parse_evidence_bundle_json, parse_incident_request_json,
    sha256_json,
)
from ai_ops.evidence import create_sanitized_incident_request, load_evidence_bundle, sanitize_question
from ai_ops.openai_engine import AIAnalysisError, AIConfig, AIOutcome
from ai_ops.reports import render_markdown, validate_report
from ai_ops.replay_runner import ReplayLimits, ReplayResult, SelectionModel, run_replay
from ai_ops.rules import analyze_rules


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGE_ROOT.parent
ID_PATTERN = re.compile(r"(?:inc|run)-[0-9a-f]{32}\Z")
ACTIVE_STATES = {"accepted", "validating", "analyzing"}
FALLBACK_REASONS = {
    "total_timeout", "tool_timeout", "model_rate_limited", "model_server_error",
    "model_network_error", "model_http_401", "model_http_403",
    "model_credit_balance_exhausted", "model_insufficient_quota",
    "model_organization_spend_limit_exceeded", "model_project_spend_limit_exceeded",
    "model_organization_usage_limit_exceeded",
    "model_retry_exhausted", "model_call_limit", "case_budget_limit",
    "case_budget_exceeded", "daily_budget_limit",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("storage directory cannot be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("storage directory must be owned by the user and private (0700)")


def _atomic_write(path: Path, content: bytes) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as output:
            temporary = output.name
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _write_json(path: Path, value: dict) -> None:
    _atomic_write(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def _read_json(path: Path, limit: int = 5 * 1024 * 1024) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("stored file is missing or unsafe")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("stored JSON must be an object")
    return value


@dataclass(frozen=True)
class RunRecord:
    metadata: dict
    request: IncidentRequest | None = None
    bundle: EvidenceBundle | None = None
    report: AnalysisReport | None = None
    markdown: str | None = None

    @property
    def incident_id(self) -> str:
        return self.metadata["incident_id"]

    @property
    def run_id(self) -> str:
        return self.metadata["run_id"]


class RunStopped(Exception):
    def __init__(self, incident_id: str, run_id: str, status: str, reason: str | None = None):
        super().__init__(f"{status}: {incident_id}/{run_id}")
        self.incident_id = incident_id
        self.run_id = run_id
        self.status = status
        self.reason = reason


class IncidentStore:
    """한 저장소의 동시 요청을 잠그고 중단된 실행을 실패로 복원한다."""

    def __init__(self, root: Path):
        root = root.expanduser().absolute()
        if root.resolve().is_relative_to(REPO_ROOT.resolve()):
            raise ValueError("runtime storage must be outside the repository")
        _private_directory(root)
        _private_directory(root / "requests")
        self.root = root
        self._fingerprint_key = self._load_fingerprint_key()

    def _load_fingerprint_key(self) -> bytes:
        """원본 질문을 저장하지 않고 재전송 내용을 비교할 개인 저장소 키를 준비한다."""
        fd = os.open(self.root / ".fingerprint-key", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("fingerprint key must be a private regular file")
            if info.st_size == 0:
                os.write(fd, os.urandom(32))
                os.fsync(fd)
            if os.fstat(fd).st_size != 32:
                raise ValueError("fingerprint key has invalid size")
            os.lseek(fd, 0, os.SEEK_SET)
            return os.read(fd, 32)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def load_api_key(self) -> str:
        """개인 저장소의 API 키를 권한 검사 후 읽는다. 키 값은 출력하지 않는다."""
        path = self.root / ".openai-api-key"
        with self._locked():
            if not path.exists() and not path.is_symlink():
                return ""
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                        stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 512):
                    raise ValueError("API 키 파일은 사용자 소유의 일반 파일(0600)이어야 합니다")
                try:
                    key = os.read(fd, 513).decode("ascii")
                except UnicodeDecodeError:
                    raise ValueError("API 키 파일의 내용이 올바르지 않습니다") from None
                self._validate_api_key(key)
                return key
            finally:
                os.close(fd)

    @staticmethod
    def _validate_api_key(key: str) -> None:
        if not isinstance(key, str) or not 20 <= len(key) <= 512 or any(
                not 33 <= ord(character) <= 126 for character in key):
            raise ValueError("API 키 형식을 확인해 주세요. 공백 없이 한 줄로 입력해야 합니다")

    def save_api_key(self, key: str) -> None:
        """키를 저장소 밖 개인 경로의 0600 파일에 원자적으로 저장한다."""
        self._validate_api_key(key)
        path = self.root / ".openai-api-key"
        with self._locked():
            if path.exists() or path.is_symlink():
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                        raise ValueError("기존 API 키 파일의 권한이 안전하지 않습니다")
                finally:
                    os.close(fd)
            _atomic_write(path, key.encode("ascii"))

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._reconcile()
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _run_dir(self, incident_id: str, run_id: str) -> Path:
        if not ID_PATTERN.fullmatch(incident_id) or not incident_id.startswith("inc-"):
            raise ValueError("invalid incident ID")
        if not ID_PATTERN.fullmatch(run_id) or not run_id.startswith("run-"):
            raise ValueError("invalid run ID")
        incident_dir = self.root / incident_id
        run_dir = incident_dir / run_id
        if incident_dir.is_symlink() or run_dir.is_symlink():
            raise ValueError("stored run path cannot be a symlink")
        return run_dir

    def _index_path(self, request_id: str) -> Path:
        return self.root / "requests" / (sha256_json(request_id) + ".json")

    def _transition(self, run_dir: Path, metadata: dict, status: str, reason: str | None = None) -> None:
        moment = _utc_text(_now())
        metadata["status"] = status
        metadata["updated_at"] = moment
        metadata["failure_reason"] = reason
        metadata["history"].append({"status": status, "at": moment})
        _write_json(run_dir / "metadata.json", metadata)

    def _reconcile(self) -> None:
        for incident_dir in self.root.glob("inc-*"):
            if incident_dir.is_symlink() or not incident_dir.is_dir() or not ID_PATTERN.fullmatch(incident_dir.name):
                continue
            for run_dir in incident_dir.glob("run-*"):
                if run_dir.is_symlink() or not run_dir.is_dir() or not ID_PATTERN.fullmatch(run_dir.name):
                    continue
                metadata_path = run_dir / "metadata.json"
                if not metadata_path.exists():
                    continue
                metadata = _read_json(metadata_path)
                if metadata.get("incident_id") != incident_dir.name or metadata.get("run_id") != run_dir.name:
                    raise ValueError("stored metadata path mismatch")
                if metadata.get("status") in ACTIVE_STATES:
                    self._transition(run_dir, metadata, "failed", "interrupted")
                index_path = self._index_path(metadata["request_id"])
                if not index_path.exists():
                    _write_json(index_path, {
                        "request_id": metadata["request_id"], "fingerprint": metadata["fingerprint"],
                        "incident_id": incident_dir.name, "run_id": run_dir.name,
                    })

    def _read_record(self, incident_id: str, run_id: str) -> RunRecord:
        run_dir = self._run_dir(incident_id, run_id)
        metadata = _read_json(run_dir / "metadata.json")
        if metadata.get("incident_id") != incident_id or metadata.get("run_id") != run_id:
            raise ValueError("stored metadata does not match requested run")
        if metadata["status"] not in ("completed", "partial"):
            return RunRecord(metadata=metadata)
        input_data = _read_json(run_dir / "input.json")
        request = parse_incident_request_json(canonical_json_bytes(input_data["request"]))
        bundle = parse_evidence_bundle_json(canonical_json_bytes(input_data["bundle"]))
        report = AnalysisReport.model_validate(_read_json(run_dir / "report.json"))
        fallback = (metadata["status"] == "partial" and metadata.get("mode") == "ai" and
                    report.execution.mode == "rules" and report.execution.engine == "rules-fallback-v1" and
                    report.analysis_status == "partial" and metadata.get("failure_reason") in FALLBACK_REASONS)
        if report.execution.mode != metadata.get("mode") and not fallback:
            raise ValueError("stored report mode does not match run")
        if metadata["status"] == "partial" and not fallback:
            raise ValueError("stored partial report is not a rule fallback")
        delivered = None
        if report.execution.mode == "ai":
            audit = _read_json(run_dir / "tool-audit.json")
            if audit.get("run_id") != run_id or audit.get("input_hash") != bundle.bundle_hash:
                raise ValueError("stored AI audit does not match report")
            delivered = set(audit["delivered_evidence_ids"])
        validate_report(report, request, bundle, run_id, delivered)
        markdown = render_markdown(report, request, bundle, delivered)
        return RunRecord(metadata=metadata, request=request, bundle=bundle, report=report, markdown=markdown)

    def show(self, incident_id: str, run_id: str) -> RunRecord:
        with self._locked():
            return self._read_record(incident_id, run_id)

    def replay(
        self, incident_id: str, run_id: str, model: SelectionModel,
        limits: ReplayLimits = ReplayLimits(),
    ) -> ReplayResult:
        """완료된 A3 입력을 읽어 도구 조회를 재생하고 개인 저장소에 감사를 남긴다."""
        record = self.show(incident_id, run_id)
        if record.request is None or record.bundle is None:
            raise ValueError("replay requires a completed investigation")
        audit_path = self._run_dir(incident_id, run_id) / "tool-audit.json"
        if audit_path.exists() or audit_path.is_symlink():
            raise ValueError("replay audit already exists for this run")
        result = run_replay(
            request=record.request, bundle=record.bundle, run_id=run_id,
            model=model, limits=limits,
        )
        with self._locked():
            latest = self._read_record(incident_id, run_id)
            if latest.bundle is None or latest.bundle.bundle_hash != result.input_hash:
                raise ValueError("stored replay input changed")
            if audit_path.exists() or audit_path.is_symlink():
                raise ValueError("replay audit already exists for this run")
            _write_json(audit_path, result.as_dict())
        return result

    def investigate(
        self, *, bundle_root: Path, registry: TargetRegistry, question: str,
        request_id: str, analyzer: Callable[[IncidentRequest, EvidenceBundle, str], AnalysisReport | AIOutcome] = analyze_rules,
        mode: str = "rules", ai_config: AIConfig | None = None,
    ) -> RunRecord:
        if mode not in ("rules", "ai") or (mode == "ai") != (ai_config is not None):
            raise ValueError("invalid investigation mode or AI configuration")
        bundle = load_evidence_bundle(bundle_root, registry)
        if mode == "ai" and (bundle.target.environment != "practice" or
                             any(item.provenance.kind != "synthetic" for item in bundle.evidence)):
            raise ValueError("AI mode accepts synthetic practice evidence only")
        cleaned_question, _ = sanitize_question(question)
        fingerprint = hmac.new(self._fingerprint_key, canonical_json_bytes({
            "request_id": request_id, "mode": mode, "question": question,
            "sanitized_question": cleaned_question,
            "target": bundle.target.model_dump(mode="json"),
            "window": bundle.window.model_dump(mode="json"), "bundle_hash": bundle.bundle_hash,
        }), hashlib.sha256).hexdigest()
        with self._locked():
            index_path = self._index_path(request_id)
            if index_path.exists():
                index = _read_json(index_path)
                if index.get("request_id") != request_id or index.get("fingerprint") != fingerprint:
                    raise ValueError("request_id was already used with different input")
                return self._read_record(index["incident_id"], index["run_id"])

            request, question_redaction = create_sanitized_incident_request(
                request_id=request_id, target=bundle.target, question=question,
                window=bundle.window, bundle=bundle, registry=registry, requested_at=_now(),
            )
            run_id = "run-" + uuid.uuid4().hex
            run_dir = self._run_dir(request.incident_id, run_id)
            _private_directory(run_dir.parent)
            run_dir.mkdir(mode=0o700)
            moment = _utc_text(_now())
            metadata = {
                "schema_version": 1, "incident_id": request.incident_id, "run_id": run_id,
                "request_id": request_id, "fingerprint": fingerprint,
                "input_hash": bundle.bundle_hash, "mode": mode, "status": "accepted",
                "created_at": moment, "updated_at": moment, "failure_reason": None,
                "history": [{"status": "accepted", "at": moment}],
            }
            _write_json(run_dir / "input.json", {
                "request": request.model_dump(mode="json"),
                "bundle": bundle.model_dump(mode="json"),
                "question_redaction": question_redaction.model_dump(mode="json"),
            })
            _write_json(run_dir / "metadata.json", metadata)
            _write_json(index_path, {
                "request_id": request_id, "fingerprint": fingerprint,
                "incident_id": request.incident_id, "run_id": run_id,
            })
            try:
                if mode == "ai":
                    self._reserve_ai_budget(ai_config)
                self._transition(run_dir, metadata, "validating")
                self._transition(run_dir, metadata, "analyzing")
                started = time.perf_counter()
                analysis = analyzer(request, bundle, run_id)
                replay = analysis.replay if isinstance(analysis, AIOutcome) else None
                report = analysis.report if isinstance(analysis, AIOutcome) else analysis
                if (mode == "ai") != (replay is not None) or report.execution.mode != mode:
                    raise ValueError("analyzer returned an incompatible report")
                report.execution.duration_ms = int((time.perf_counter() - started) * 1000)
                delivered = set(replay.delivered_evidence_ids) if replay else None
                validate_report(report, request, bundle, run_id, delivered)
                markdown = render_markdown(report, request, bundle, delivered)
                if replay:
                    _write_json(run_dir / "tool-audit.json", replay.as_dict())
                _write_json(run_dir / "report.json", report.model_dump(mode="json"))
                _atomic_write(run_dir / "report.md", markdown.encode("utf-8"))
                self._transition(run_dir, metadata, "completed")
            except KeyboardInterrupt:
                self._transition(run_dir, metadata, "cancelled", "user_cancelled")
                raise RunStopped(request.incident_id, run_id, "cancelled", "user_cancelled") from None
            except Exception as exc:
                if isinstance(exc, AIAnalysisError) and exc.replay is not None:
                    _write_json(run_dir / "tool-audit.json", exc.replay.as_dict())
                if isinstance(exc, AIAnalysisError):
                    if exc.usage is not None:
                        metadata["model_usage"] = exc.usage
                    if exc.diagnostics:
                        metadata["validation_diagnostics"] = exc.diagnostics
                stopped = "cancelled" if isinstance(exc, AIAnalysisError) and exc.replay and exc.replay.status == "cancelled" else "failed"
                if isinstance(exc, AIAnalysisError):
                    reason = exc.reason
                elif mode == "ai" and isinstance(exc, ValueError) and str(exc) == "daily AI budget limit":
                    reason = "daily_budget_limit"
                else:
                    reason = "ai_error" if mode == "ai" else "rules_error"
                if mode == "ai" and stopped == "failed" and reason in FALLBACK_REASONS:
                    fallback = analyze_rules(request, bundle, run_id)
                    fallback.analysis_status = "partial"
                    fallback.execution.engine = "rules-fallback-v1"
                    fallback.limitations.append(f"AI 조사가 {reason} 사유로 실패하여 저장된 증거의 규칙 분석만 제공한다.")
                    validate_report(fallback, request, bundle, run_id)
                    markdown = render_markdown(fallback, request, bundle)
                    _write_json(run_dir / "report.json", fallback.model_dump(mode="json"))
                    _atomic_write(run_dir / "report.md", markdown.encode("utf-8"))
                    self._transition(run_dir, metadata, "partial", reason)
                    return RunRecord(metadata=metadata, request=request, bundle=bundle,
                                     report=fallback, markdown=markdown)
                self._transition(run_dir, metadata, stopped, reason)
                raise RunStopped(request.incident_id, run_id, stopped, reason) from None
            return RunRecord(metadata=metadata, request=request, bundle=bundle,
                             report=report, markdown=markdown)

    def _reserve_ai_budget(self, config: AIConfig) -> None:
        """저장소 잠금 안에서 건당 상한을 UTC 일일 한도에 보수적으로 선예약한다."""
        path = self.root / "ai-budget.json"
        ledger = _read_json(path) if path.exists() or path.is_symlink() else {"schema_version": 1, "days": {}}
        if ledger.get("schema_version") != 1 or not isinstance(ledger.get("days"), dict):
            raise ValueError("invalid AI budget ledger")
        key = _now().date().isoformat()
        spent = ledger["days"].get(key, 0)
        if (type(spent) not in (int, float) or not math.isfinite(spent) or
                spent < 0 or spent + config.per_case_usd > config.per_day_usd + 1e-9):
            raise ValueError("daily AI budget limit")
        ledger["days"][key] = round(spent + config.per_case_usd, 6)
        _write_json(path, ledger)
