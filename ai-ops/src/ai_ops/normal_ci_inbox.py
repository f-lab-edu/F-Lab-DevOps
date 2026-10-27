"""검증된 정상 CI 영수증을 공유 PVC에 보관하고 worker가 정제 요약으로 확정한다."""

from __future__ import annotations

import hmac
import json
import re
import time

from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.model_comparison import rules_projection
from ai_ops.model_sequence_projection import SEQUENCE_POLICY
from ai_ops.normal_ci_contract import NormalCIBinding, NormalCIReceipt
from ai_ops.normal_ci_history import VerifiedNormalHistory


MAX_NORMAL_RECEIPTS = 100
RETENTION_SECONDS = 7 * 24 * 60 * 60


class NormalCIInbox:
    """GitHub 토큰 없이도 worker가 큐 HMAC과 대상 결합을 재검사한다."""

    def __init__(self, queue, binding: NormalCIBinding):
        self.queue = queue
        self.binding = NormalCIBinding.model_validate(binding)
        self.binding_hash = sha256_json(self.binding.model_dump(mode="json"))
        with queue.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS normal_ci_receipts ("
                       "episode_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, attempt TEXT NOT NULL, "
                       "payload TEXT NOT NULL, fingerprint TEXT NOT NULL, state TEXT NOT NULL "
                       "CHECK(state IN ('queued','ready','needs_review')), summary TEXT, "
                       "created_at REAL NOT NULL, updated_at REAL NOT NULL, "
                       "UNIQUE(run_id,attempt))")

    def _fingerprint(self, episode_key: str, payload: dict) -> str:
        return self.queue.engine.request_fingerprint({"source": "normal-ci-inbox-v1",
            "episode_key": episode_key, "payload": payload})

    @staticmethod
    def _key(run_id: str, attempt: str) -> str:
        return sha256_json({"source": "verified_normal_ci_v1", "run_id": run_id,
                            "run_attempt": attempt})

    def _validate(self, payload: dict) -> NormalCIReceipt:
        if set(payload) != {"run_id", "run_attempt", "artifact_id", "archive_digest",
                            "source_revision", "receipt"}:
            raise ValueError("normal_ci_inbox_payload_rejected")
        receipt = NormalCIReceipt.model_validate(payload["receipt"])
        if (receipt.binding_hash != self.binding_hash
                or receipt.config_hash != self.binding.config_hash
                or receipt.executor_sha256 != self.binding.executor_sha256
                or receipt.identity.run_id != payload["run_id"]
                or receipt.identity.run_attempt != payload["run_attempt"]
                or receipt.identity.source_revision != payload["source_revision"]
                or receipt.view.policy_version != SEQUENCE_POLICY
                or not isinstance(payload["artifact_id"], str)
                or not payload["artifact_id"].isdigit()
                or not isinstance(payload["archive_digest"], str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", payload["archive_digest"])):
            raise ValueError("normal_ci_inbox_binding_rejected")
        return receipt

    def submit(self, history: VerifiedNormalHistory) -> tuple[str, bool]:
        """GitHub 검증 결과만 받으며 같은 시도의 다른 artifact를 거부한다."""
        if type(history) is not VerifiedNormalHistory:
            raise ValueError("normal_ci_verified_history_required")
        payload = {"run_id": history.run_id, "run_attempt": history.run_attempt,
                   "artifact_id": history.artifact_id, "archive_digest": history.archive_digest,
                   "source_revision": history.source_revision,
                   "receipt": history.receipt.model_dump(mode="json")}
        self._validate(payload)
        if len(canonical_json_bytes(payload)) > 32768:
            raise ValueError("normal_ci_inbox_size_rejected")
        key = self._key(history.run_id, history.run_attempt)
        fingerprint = self._fingerprint(key, payload)
        with self.queue.transaction() as db:
            current = db.execute("SELECT fingerprint FROM normal_ci_receipts WHERE episode_key=?",
                                 (key,)).fetchone()
            if current:
                if not hmac.compare_digest(current["fingerprint"], fingerprint):
                    raise ValueError("normal_ci_inbox_duplicate_conflict")
                return key, True
            if db.execute("SELECT count(*) FROM normal_ci_receipts").fetchone()[0] >= MAX_NORMAL_RECEIPTS:
                raise ValueError("normal_ci_inbox_full")
            now = time.time()
            db.execute("INSERT INTO normal_ci_receipts VALUES (?,?,?,?,?,'queued',NULL,?,?)",
                       (key, history.run_id, history.run_attempt,
                        canonical_json_bytes(payload).decode(), fingerprint, now, now))
        return key, False

    def process_one(self) -> dict | None:
        """worker가 원본 없이 정제 view의 규칙 요약만 확정한다. 모델 호출은 없다."""
        with self.queue.transaction() as db:
            row = db.execute("SELECT * FROM normal_ci_receipts WHERE state='queued' "
                             "ORDER BY created_at,episode_key LIMIT 1").fetchone()
            if row is None:
                return None
            try:
                payload = json.loads(row["payload"])
                if (self._key(row["run_id"], row["attempt"]) != row["episode_key"]
                        or not hmac.compare_digest(self._fingerprint(row["episode_key"], payload),
                                                   row["fingerprint"])):
                    raise ValueError("normal_ci_inbox_integrity_rejected")
                receipt = self._validate(payload)
                summary = rules_projection(receipt.view).model_dump(mode="json")
                result = {"record_type": "normal_ci_rules_summary", "source_run": receipt.source_run,
                          "rules_ref": receipt.rules_ref, "view_hash": receipt.view_hash,
                          "artifact_id": payload["artifact_id"],
                          "archive_digest": payload["archive_digest"], "summary": summary,
                          "external_model_calls": 0}
                if len(canonical_json_bytes(result)) > 32768:
                    raise ValueError("normal_ci_summary_size_rejected")
                state, body = "ready", canonical_json_bytes(result).decode()
            except (ValueError, TypeError, KeyError, AttributeError):
                state, body = "needs_review", None
            db.execute("UPDATE normal_ci_receipts SET state=?,summary=?,updated_at=? "
                       "WHERE episode_key=? AND state='queued'", (state, body, time.time(), row["episode_key"]))
            return {"episode_key": row["episode_key"], "state": state}

    def prune(self) -> int:
        """정제 영수증·요약도 7일이 지나면 보관소에서 제거한다."""
        with self.queue.transaction() as db:
            result = db.execute("DELETE FROM normal_ci_receipts WHERE updated_at < ?",
                                (time.time() - RETENTION_SECONDS,))
            return result.rowcount

    def status(self, run_id: str, attempt: str) -> dict | None:
        key = self._key(run_id, attempt)
        with self.queue.transaction() as db:
            row = db.execute("SELECT state,summary FROM normal_ci_receipts WHERE episode_key=?",
                             (key,)).fetchone()
        return None if row is None else {"state": row["state"],
            "summary": json.loads(row["summary"]) if row["summary"] else None}
