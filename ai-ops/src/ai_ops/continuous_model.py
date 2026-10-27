"""실습 CI 정제 입력의 비활성 기본 대기열과 모의 모델 처리 장부다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.incidents import _private_directory
from ai_ops.model_comparison import rules_projection
from ai_ops.model_projection import POLICY, Projection, TARGET
from ai_ops.model_sequence_projection import SequenceProjection
from ai_ops.model_policy import policy_spec
from ai_ops.model_gateway_contract import ModelSubmission
from ai_ops.model_gateway_transport import GatewayOpenAITransport
from ai_ops.path_policy import permitted_path
from ai_ops.projected_model_engine import CallPlan, ProjectionModelFailure, generate_once


BODY_DAYS = 7
LEDGER_DAYS = 90
MAX_CASES = 10
RESERVE_CENTS = 10
DAILY_CENTS = 100
CAMPAIGN_SECONDS = 24 * 60 * 60


def implementation_hash() -> str:
    """승인 범위에 정제·프롬프트·장부·연결 코드를 함께 고정한다."""
    root = Path(__file__).parent
    names = ("model_projection.py", "model_ci_projection.py", "model_sequence_projection.py",
             "model_policy.py", "model_comparison.py", "projected_model_engine.py",
             "projected_ci_model_engine.py", "projected_sequence_model_engine.py",
             "continuous_model.py", "continuous_ci_bridge.py", "model_gateway_contract.py",
             "model_gateway_transport.py", "model_gateway_client.py", "model_gateway.py",
             "model_live_probe.py", "model_runtime.py", "model_activation.py", "model_service.py",
             "normal_ci_contract.py", "normal_ci_rbac.py", "normal_ci_runner.py", "normal_ci_source.py",
             "normal_ci_history.py", "normal_ci_inbox.py", "normal_ci_model_bridge.py",
             "ci_history.py", "ci_token_reader.py",
             "ci_relay.py", "ci_service.py", "worker_runtime.py", "model_campaign_review.py")
    return sha256_json({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in names})


def _utc(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


class MockTransport:
    """테스트가 지정한 두 응답만 반환하며 네트워크 호출 수단을 갖지 않는다."""

    def __init__(self, token_response: dict, generation_response: dict):
        self.responses = {"/v1/responses/input_tokens": token_response,
                          "/v1/responses": generation_response}
        self.calls: list[str] = []

    def __call__(self, path: str, body: dict, *, timeout: float):
        if path not in self.responses or timeout <= 0 or len(self.calls) >= 2:
            raise ValueError("mock_transport_rejected")
        self.calls.append(path)
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return response


class ContinuousModelStore:
    """독립 SQLite 장부에 예약을 먼저 확정하고 불명 전송은 재시도하지 않는다."""

    def __init__(self, root: Path, *, clock=time.time):
        self.root = permitted_path(root, "store")
        _private_directory(self.root)
        self.path = self.root / "continuous-model.sqlite3"
        self.clock = clock
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, binding TEXT NOT NULL, "
                       "state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, actor TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS live_approvals (campaign_id TEXT PRIMARY KEY, "
                       "binding_hash TEXT NOT NULL, actor TEXT NOT NULL, approved REAL NOT NULL, "
                       "sharing_off INTEGER NOT NULL, retention_ack INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, "
                       "binding_hash TEXT NOT NULL, projection_binding TEXT NOT NULL, view TEXT, rules TEXT, "
                       "result TEXT, source_run TEXT NOT NULL, "
                       "rules_ref TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, created REAL NOT NULL, "
                       "updated REAL NOT NULL, reserved_day TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS budget (day TEXT PRIMARY KEY, reserved_cents INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS budget_clock (id INTEGER PRIMARY KEY CHECK(id=1), "
                       "last_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS rejections (job_id TEXT NOT NULL, campaign_id TEXT NOT NULL, "
                       "source_run TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL, "
                       "PRIMARY KEY(job_id,campaign_id))")
            db.execute("CREATE INDEX IF NOT EXISTS outbox_age ON outbox(created)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS outbox_source ON outbox(campaign_id,source_run)")
        self.reconcile_sending()

    def _now(self) -> float:
        value = self.clock()
        if type(value) not in (int, float) or not 0 <= value < 10**11:
            raise ValueError("model_clock_rejected")
        return float(value)

    def _transaction(self):
        from contextlib import contextmanager
        @contextmanager
        def open_db():
            for suffix in ("", "-wal", "-shm", "-journal"):
                path = Path(str(self.path) + suffix)
                if path.is_symlink() or path.exists():
                    info = path.lstat()
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                        raise ValueError("model_store_permissions")
            db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=5)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA busy_timeout=5000")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("PRAGMA secure_delete=ON")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
            finally:
                db.close()
        return open_db()

    @staticmethod
    def _plan(plan: CallPlan):
        plan = CallPlan.model_validate(plan.model_dump(mode="json"))
        if plan.execution_mode != "mock" or plan.credential_fingerprint is not None:
            raise ValueError("continuous_model_mock_only")
        return plan

    @staticmethod
    def _live_plan(plan: CallPlan):
        plan = CallPlan.model_validate(plan.model_dump(mode="json"))
        if plan.execution_mode != "live" or plan.credential_fingerprint is None:
            raise ValueError("continuous_model_live_plan_required")
        return plan

    def prepare_campaign(self, *, deployment_uid: str, config_hash: str,
                         cluster_fingerprint: str, plan: CallPlan,
                         policy: str = POLICY) -> str:
        """실제 활성화가 아닌 모의 승인안만 만든다."""
        plan = self._plan(plan)
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", deployment_uid):
            raise ValueError("campaign_uid_rejected")
        if not all(re.fullmatch(r"[0-9a-f]{64}", x) for x in (config_hash, cluster_fingerprint)):
            raise ValueError("campaign_hash_rejected")
        selected = policy_spec(policy)
        binding = {"target": TARGET, "deployment_uid": deployment_uid,
                   "config_hash": config_hash, "cluster_fingerprint": cluster_fingerprint,
                   "policy": selected.policy, "prompt": selected.prompt,
                   "implementation_hash": implementation_hash(),
                   "plan": plan.model_dump(mode="json"),
                   "limit": {"seconds": CAMPAIGN_SECONDS, "cases": MAX_CASES,
                             "case_cents": RESERVE_CENTS, "utc_day_cents": DAILY_CENTS}}
        now = self._now()
        identifier = "campaign-" + uuid.uuid4().hex
        with self._transaction() as db:
            db.execute("INSERT INTO campaigns VALUES (?,?,'prepared',?,?,NULL)",
                       (identifier, canonical_json_bytes(binding).decode(), now, now + CAMPAIGN_SECONDS))
        return identifier

    def prepare_live_campaign(self, *, deployment_uid: str, config_hash: str,
                              cluster_fingerprint: str, image_digest: str,
                              sample_view_hash: str, plan: CallPlan,
                              policy: str = POLICY) -> str:
        """대상·이미지·샘플·키 지문·비용을 결합한 승인안을 만들고 비활성으로 둔다."""
        plan = self._live_plan(plan)
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", deployment_uid):
            raise ValueError("campaign_uid_rejected")
        if not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in
                   (config_hash, cluster_fingerprint, sample_view_hash)):
            raise ValueError("campaign_hash_rejected")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
            raise ValueError("campaign_image_rejected")
        selected = policy_spec(policy)
        binding = {"target": TARGET, "deployment_uid": deployment_uid,
                   "config_hash": config_hash, "cluster_fingerprint": cluster_fingerprint,
                   "image_digest": image_digest, "sample_view_hash": sample_view_hash,
                   "policy": selected.policy, "prompt": selected.prompt,
                   "implementation_hash": implementation_hash(),
                   "plan": plan.model_dump(mode="json"),
                   "limit": {"seconds": CAMPAIGN_SECONDS, "cases": MAX_CASES,
                             "case_cents": RESERVE_CENTS, "utc_day_cents": DAILY_CENTS}}
        now = self._now()
        identifier = "campaign-" + uuid.uuid4().hex
        with self._transaction() as db:
            db.execute("INSERT INTO campaigns VALUES (?,?,'prepared',?,?,NULL)",
                       (identifier, canonical_json_bytes(binding).decode(), now, now + CAMPAIGN_SECONDS))
        return identifier

    def approve_live(self, identifier: str, *, binding_hash: str, actor: str,
                     optional_sharing_off: bool, provider_retention_acknowledged: bool):
        """명시된 개인정보·보관 인지와 승인 범위 해시가 모두 맞을 때만 활성화한다."""
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", actor) or
                optional_sharing_off is not True or provider_retention_acknowledged is not True):
            raise ValueError("campaign_live_approval_rejected")
        with self._transaction() as db:
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (identifier,)).fetchone()
            now = self._now()
            if (row is None or row["state"] != "prepared" or now >= row["expires"] or
                    sha256_json(json.loads(row["binding"])) != binding_hash or
                    self._live_plan(CallPlan.model_validate(json.loads(row["binding"])["plan"])).execution_mode != "live"):
                raise ValueError("campaign_live_approval_rejected")
            db.execute("INSERT INTO live_approvals VALUES (?,?,?,?,1,1)",
                       (identifier, binding_hash, actor, now))
            db.execute("UPDATE campaigns SET state='approved',actor=? WHERE id=?", (actor, identifier))

    def approve_mock(self, identifier: str, *, binding_hash: str, actor: str):
        """모의 승인도 정확한 범위 해시와 승인자를 요구한다."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", actor):
            raise ValueError("campaign_actor_rejected")
        with self._transaction() as db:
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (identifier,)).fetchone()
            if (row is None or row["state"] != "prepared" or self._now() >= row["expires"] or
                    sha256_json(json.loads(row["binding"])) != binding_hash or
                    self._plan(CallPlan.model_validate(json.loads(row["binding"])["plan"])).execution_mode != "mock"):
                raise ValueError("campaign_approval_rejected")
            db.execute("UPDATE campaigns SET state='approved',actor=? WHERE id=?", (actor, identifier))

    def campaign(self, identifier: str) -> dict:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM campaigns WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ValueError("campaign_missing")
            value = dict(row)
            approval = db.execute("SELECT approved FROM live_approvals WHERE campaign_id=?",
                                  (identifier,)).fetchone()
        value["binding"] = json.loads(value["binding"])
        value["binding_hash"] = sha256_json(value["binding"])
        value["approved_at"] = approval["approved"] if approval else None
        return value

    def enqueue(self, campaign_id: str, projection: Projection | SequenceProjection, *, source_run: str,
                rules_ref: str) -> str:
        """검증된 실습 Projection의 view만 저장한다. 원본 대응표는 기록하지 않는다."""
        selected = policy_spec(projection.view.policy_version)
        projection = selected.parse_projection(canonical_json_bytes(projection.model_dump(mode="json")))
        source = projection.base if isinstance(projection, SequenceProjection) else projection
        if source_run != source.identity.run_id or not re.fullmatch(r"run-[0-9a-f]{32}", source_run):
            raise ValueError("outbox_source_run_rejected")
        if not re.fullmatch(r"inc-[0-9a-f]{32}/run-[0-9a-f]{32}/report\.md", rules_ref):
            raise ValueError("outbox_rules_ref_rejected")
        if rules_ref != f"{source.identity.incident_id}/{source_run}/report.md":
            raise ValueError("outbox_rules_binding_rejected")
        with self._transaction() as db:
            campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if campaign is None or campaign["state"] == "stopped":
                raise ValueError("outbox_campaign_unavailable")
            binding = json.loads(campaign["binding"])
            if (binding["implementation_hash"] != implementation_hash() or
                    binding["prompt"] != selected.prompt or binding["policy"] != selected.policy):
                raise ValueError("campaign_code_changed")
            identity = source.identity
            if (identity.deployment_uid != binding["deployment_uid"] or
                    identity.config_hash != binding["config_hash"] or
                    identity.cluster_fingerprint != binding["cluster_fingerprint"] or
                    projection.view.policy_version != binding["policy"] or
                    projection.view.target_alias != "service-1"):
                raise ValueError("outbox_target_rejected")
            plan = self._plan(CallPlan.model_validate(binding["plan"]))
            selected.request_templates(projection.view, plan)
            key = sha256_json({"source_run": source_run,
                               "snapshot_hash": source.source_bundle_hash,
                               "view_hash": projection.view_hash, "policy": selected.policy,
                               "campaign_id": campaign_id})
            identifier = "outbox-" + key
            view = projection.view.model_dump(mode="json")
            rules = rules_projection(projection.view).model_dump(mode="json")
            projection_binding = {"source_bundle_hash": source.source_bundle_hash,
                                  "source_audit_hash": source.source_audit_hash,
                                  "mapping_hash": projection.mapping_hash,
                                  "exporter_sha256": identity.exporter_sha256,
                                  "view_hash": projection.view_hash}
            bound = sha256_json({"view": view, "projection_binding": projection_binding,
                                 "source_run": source_run,
                                 "rules_ref": rules_ref, "campaign": campaign_id,
                                 "binding": binding})
            existing = db.execute("SELECT binding_hash FROM outbox WHERE id=?", (identifier,)).fetchone()
            if existing:
                if existing["binding_hash"] != bound:
                    raise ValueError("outbox_identity_conflict")
                return identifier
            if db.execute("SELECT 1 FROM outbox WHERE campaign_id=? AND source_run=?",
                          (campaign_id, source_run)).fetchone():
                raise ValueError("outbox_source_conflict")
            now = self._now()
            db.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                       (identifier, campaign_id, bound, canonical_json_bytes(projection_binding).decode(),
                        canonical_json_bytes(view).decode(),
                        canonical_json_bytes(rules).decode(), None, source_run, rules_ref,
                        "prepared", None, now, now))
        return identifier

    def enqueue_submission(self, submission: ModelSubmission) -> str:
        """gateway는 서명 검증을 마친 정제 입력만 받아 별도 장부에 기록한다."""
        from ai_ops.model_gateway_contract import parse_model_submission
        submission = parse_model_submission(canonical_json_bytes(submission.model_dump(mode="json")))
        with self._transaction() as db:
            campaign = db.execute("SELECT * FROM campaigns WHERE id=?",
                                  (submission.campaign_id,)).fetchone()
            now = self._now()
            if (campaign is None or campaign["state"] != "approved" or
                    not campaign["created"] <= now < campaign["expires"]):
                raise ValueError("outbox_live_campaign_unavailable")
            binding = json.loads(campaign["binding"])
            selected = policy_spec(binding["policy"])
            plan = self._live_plan(CallPlan.model_validate(binding["plan"]))
            approval = db.execute("SELECT * FROM live_approvals WHERE campaign_id=?",
                                  (submission.campaign_id,)).fetchone()
            if (approval is None or approval["binding_hash"] != sha256_json(binding) or
                    approval["sharing_off"] != 1 or approval["retention_ack"] != 1 or
                    binding["implementation_hash"] != implementation_hash() or
                    binding["prompt"] != selected.prompt or
                    submission.view.policy_version != selected.policy):
                raise ValueError("outbox_live_approval_or_code_changed")
            if (submission.deployment_uid != binding["deployment_uid"] or
                    submission.config_hash != binding["config_hash"] or
                    submission.cluster_fingerprint != binding["cluster_fingerprint"] or
                    submission.view.policy_version != binding["policy"]):
                raise ValueError("outbox_target_rejected")
            selected.request_templates(submission.view, plan)
            projection_binding = {"source_bundle_hash": submission.source_bundle_hash,
                                  "source_audit_hash": submission.source_audit_hash,
                                  "mapping_hash": submission.mapping_hash,
                                  "exporter_sha256": submission.exporter_sha256,
                                  "view_hash": submission.view_hash}
            view = submission.view.model_dump(mode="json")
            rules = rules_projection(submission.view).model_dump(mode="json")
            key = sha256_json({"source_run": submission.source_run,
                               "snapshot_hash": submission.source_bundle_hash,
                               "view_hash": submission.view_hash, "policy": selected.policy,
                               "campaign_id": submission.campaign_id})
            identifier = "outbox-" + key
            bound = sha256_json({"view": view, "projection_binding": projection_binding,
                                 "source_run": submission.source_run,
                                 "rules_ref": submission.rules_ref,
                                 "campaign": submission.campaign_id, "binding": binding})
            existing = db.execute("SELECT binding_hash FROM outbox WHERE id=?",
                                  (identifier,)).fetchone()
            if existing:
                if existing["binding_hash"] != bound:
                    raise ValueError("outbox_identity_conflict")
                return identifier
            if db.execute("SELECT 1 FROM outbox WHERE campaign_id=? AND source_run=?",
                          (submission.campaign_id, submission.source_run)).fetchone():
                raise ValueError("outbox_source_conflict")
            db.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                       (identifier, submission.campaign_id, bound,
                        canonical_json_bytes(projection_binding).decode(),
                        canonical_json_bytes(view).decode(), canonical_json_bytes(rules).decode(),
                        None, submission.source_run, submission.rules_ref, "prepared", None, now, now))
        return identifier

    def item(self, identifier: str) -> dict:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM outbox WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise ValueError("outbox_missing")
            return dict(row)

    def next_prepared(self, campaign_id: str) -> str | None:
        """아직 전송하지 않은 가장 오래된 한 건만 선택한다."""
        with self._transaction() as db:
            row = db.execute("SELECT id FROM outbox WHERE campaign_id=? AND state='prepared' "
                             "ORDER BY created,id LIMIT 1", (campaign_id,)).fetchone()
            return row["id"] if row else None

    def has_source(self, campaign_id: str, source_run: str) -> bool:
        with self._transaction() as db:
            return db.execute("SELECT 1 FROM outbox WHERE campaign_id=? AND source_run=?",
                              (campaign_id, source_run)).fetchone() is not None

    def has_rejection(self, campaign_id: str, job_id: str) -> bool:
        with self._transaction() as db:
            return db.execute("SELECT 1 FROM rejections WHERE campaign_id=? AND job_id=?",
                              (campaign_id, job_id)).fetchone() is not None

    def reject_projection(self, campaign_id: str, job_id: str, source_run: str,
                          reason: str = "projection_rejected"):
        """원문 오류 대신 정제 불가 사유만 저장하고 규칙 결과를 유지한다."""
        if not re.fullmatch(r"job-[0-9a-f]{32}", job_id):
            raise ValueError("rejection_job_rejected")
        if reason not in {"projection_rejected", "integrity_rejected"}:
            raise ValueError("rejection_reason_rejected")
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO rejections VALUES (?,?,?,?,?)",
                       (job_id, campaign_id, source_run, reason, self._now()))

    def process_mock(self, identifier: str, transport: MockTransport) -> dict:
        """예약 commit 뒤 모의 응답을 처리한다. 중단 후 sending은 자동 재전송하지 않는다."""
        if type(transport) is not MockTransport:
            raise ValueError("continuous_model_mock_only")
        return self._process(identifier, transport, live=False)

    def process_live(self, identifier: str, transport: GatewayOpenAITransport, *,
                     image_digest: str) -> dict:
        """승인된 live 캠페인만 토큰 계산 전 예약하고 실제 전송 객체를 한 번 호출한다."""
        if type(transport) is not GatewayOpenAITransport:
            raise ValueError("continuous_model_live_transport_required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
            raise ValueError("campaign_image_rejected")
        return self._process(identifier, transport, live=True, image_digest=image_digest)

    def _process(self, identifier: str, transport, *, live: bool,
                 image_digest: str | None = None) -> dict:
        with self._transaction() as db:
            row = db.execute("SELECT * FROM outbox WHERE id=?", (identifier,)).fetchone()
            if row is None or row["state"] != "prepared":
                raise ValueError("outbox_not_prepared")
            campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (row["campaign_id"],)).fetchone()
            now = self._now()
            if campaign is None or campaign["state"] != "approved" or not campaign["created"] <= now < campaign["expires"]:
                raise ValueError("campaign_not_active")
            binding = json.loads(campaign["binding"])
            selected = policy_spec(binding["policy"])
            if (binding["implementation_hash"] != implementation_hash() or
                    binding["prompt"] != selected.prompt):
                raise ValueError("campaign_code_changed")
            if live:
                if binding.get("image_digest") != image_digest:
                    raise ValueError("campaign_image_changed")
                approval = db.execute("SELECT * FROM live_approvals WHERE campaign_id=?",
                                      (row["campaign_id"],)).fetchone()
                if (approval is None or approval["binding_hash"] != sha256_json(binding) or
                        approval["sharing_off"] != 1 or approval["retention_ack"] != 1):
                    raise ValueError("campaign_live_approval_missing")
            view = json.loads(row["view"])
            projection_binding = json.loads(row["projection_binding"])
            bound = sha256_json({"view": view, "projection_binding": projection_binding,
                                 "source_run": row["source_run"], "rules_ref": row["rules_ref"],
                                 "campaign": row["campaign_id"], "binding": binding})
            if (bound != row["binding_hash"] or sha256_json(view) != projection_binding["view_hash"] or
                    json.loads(row["rules"]) != rules_projection(
                        selected.view_type.model_validate(view)).model_dump(mode="json")):
                raise ValueError("outbox_integrity_rejected")
            plan = (self._live_plan if live else self._plan)(
                CallPlan.model_validate(binding["plan"]))
            if live and transport.expected_credential != plan.credential_fingerprint:
                raise ValueError("campaign_credential_changed")
            if (datetime.fromisoformat(plan.price.checked_on).date() -
                    datetime.fromtimestamp(now, timezone.utc).date()).days not in range(-7, 1):
                raise ValueError("campaign_price_expired")
            count = db.execute("SELECT count(*) FROM outbox WHERE campaign_id=? AND reserved_day IS NOT NULL",
                               (row["campaign_id"],)).fetchone()[0]
            last = db.execute("SELECT last_at FROM budget_clock WHERE id=1").fetchone()
            if last is not None and now < last["last_at"]:
                raise ValueError("campaign_clock_moved_backwards")
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            reserved = db.execute("SELECT reserved_cents FROM budget WHERE day=?", (day,)).fetchone()
            if count >= MAX_CASES or (reserved[0] if reserved else 0) + RESERVE_CENTS > DAILY_CENTS:
                raise ValueError("campaign_budget_limit")
            db.execute("INSERT INTO budget(day,reserved_cents) VALUES (?,?) ON CONFLICT(day) DO UPDATE "
                       "SET reserved_cents=reserved_cents+excluded.reserved_cents", (day, RESERVE_CENTS))
            db.execute("INSERT INTO budget_clock(id,last_at) VALUES (1,?) ON CONFLICT(id) DO UPDATE "
                       "SET last_at=excluded.last_at", (now,))
            db.execute("UPDATE outbox SET state='sending',reserved_day=?,updated=? WHERE id=?",
                       (day, now, identifier))
        try:
            parsed = selected.view_type.model_validate(view)
            templates = selected.request_templates(parsed, plan)
            draft, usage, model = generate_once(templates, plan, transport, clock=time.monotonic)
            report = selected.validate_report(draft, parsed)
            result = {"report": report.model_dump(mode="json"), "usage": usage, "model": model,
                      "semantic_review_required": True, "execution_status": "not_executed"}
            state, reason = "needs_review", "semantic_review_required"
        except (ProjectionModelFailure, ValueError) as error:
            result = None
            state, reason = "needs_review", (error.reason if isinstance(error, ProjectionModelFailure)
                                               else "projection_report_invalid")
        with self._transaction() as db:
            db.execute("UPDATE outbox SET state=?,reason=?,result=?,updated=? WHERE id=? AND state='sending'",
                       (state, reason, canonical_json_bytes(result).decode() if result else None,
                        self._now(), identifier))
        return self.item(identifier)

    def stop(self, campaign_id: str):
        with self._transaction() as db:
            db.execute("UPDATE campaigns SET state='stopped' WHERE id=?", (campaign_id,))

    def reconcile_sending(self) -> int:
        """재시작 후 요청이 나갔을 수 있는 기록을 보류하고 다시 보내지 않는다."""
        with self._transaction() as db:
            return db.execute("UPDATE outbox SET state='needs_review',reason='unknown_send',updated=? "
                              "WHERE state='sending'", (self._now(),)).rowcount

    def prune(self) -> tuple[int, int]:
        """본문 7일·본문 없는 예약/중복 기록 90일을 유지한다."""
        now = self._now()
        with self._transaction() as db:
            bodies = db.execute("UPDATE outbox SET view=NULL,rules=NULL,result=NULL,updated=? "
                                "WHERE created<? AND view IS NOT NULL", (now, now-BODY_DAYS*86400)).rowcount
            records = db.execute("DELETE FROM outbox WHERE created<?", (now-LEDGER_DAYS*86400,)).rowcount
            db.execute("DELETE FROM rejections WHERE created<?", (now-LEDGER_DAYS*86400,))
            db.execute("DELETE FROM budget WHERE day<?", (_utc(now-LEDGER_DAYS*86400)[:10],))
            db.execute("DELETE FROM campaigns WHERE created<? AND id NOT IN "
                       "(SELECT campaign_id FROM outbox)", (now-LEDGER_DAYS*86400,))
            db.execute("DELETE FROM live_approvals WHERE campaign_id NOT IN (SELECT id FROM campaigns)")
        return bodies, records
