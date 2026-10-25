"""검증된 실습 CI 규칙 보고서만 정제 모델 대기열에 연결한다."""

from __future__ import annotations

import re

from ai_ops.ci_handoff import SOURCE, check_snapshot
from ai_ops.ci_rules import ENGINE as CI_RULES_ENGINE
from ai_ops.model_ci_projection import CI_POLICY, project_ci_snapshot
from ai_ops.model_projection import SourceIdentity, project_snapshot
from ai_ops.model_projection import POLICY


class ContinuousCIBridge:
    """실습 worker에 명시적으로 주입할 때만 동작하는 모의 연결이다."""

    def __init__(self, store, campaign_id: str, *, cluster_fingerprint: str,
                 exporter_sha256: str, policy: str = POLICY):
        if not all(re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in (cluster_fingerprint, exporter_sha256)):
            raise ValueError("model_bridge_identity_rejected")
        self.store = store
        self.campaign_id = campaign_id
        self.cluster_fingerprint = cluster_fingerprint
        self.exporter_sha256 = exporter_sha256
        if policy not in {POLICY, CI_POLICY}:
            raise ValueError("model_bridge_policy_rejected")
        self.policy = policy

    def capture(self, worker, job: dict, record=None):
        """큐 HMAC·고정 snapshot·규칙 보고서 검증 뒤에만 view를 만든다."""
        payload = worker._payload(job)
        if payload.get("source") != SOURCE or "ci_history" not in payload:
            return None
        history = worker.queue.verify_ci_payload(job, payload)
        if record is None:
            record = worker.queue.engine.find_request(worker.queue.request_id(job))
        if (record is None or record.metadata["status"] not in ("completed", "partial")
                or record.report.execution.engine != CI_RULES_ENGINE):
            raise ValueError("model_bridge_rules_report_required")
        worker._check_record(job, record)
        if (self.store.has_source(self.campaign_id, record.run_id) or
                self.store.has_rejection(self.campaign_id, job["job_id"])):
            return None
        campaign = self.store.campaign(self.campaign_id)
        if (campaign["state"] == "stopped" or
                not campaign["created"] <= job["created_at"] < campaign["expires"]):
            return None
        snapshot = worker._load_snapshot(job)
        check_snapshot(snapshot, history)
        identity = SourceIdentity(cluster_fingerprint=self.cluster_fingerprint,
            deployment_uid=history.parsed()[0].observed.deployment_uid,
            config_hash=worker.settings.config_hash, incident_id=record.incident_id,
            run_id=record.run_id, exporter_sha256=self.exporter_sha256)
        try:
            projection = (project_ci_snapshot(snapshot, worker.settings, identity, history)
                          if self.policy == CI_POLICY else
                          project_snapshot(snapshot, worker.settings, identity))
        except ValueError:
            self.store.reject_projection(self.campaign_id, job["job_id"], record.run_id)
            return None
        return self.store.enqueue(self.campaign_id, projection, source_run=record.run_id,
            rules_ref=f"{record.incident_id}/{record.run_id}/report.md")

    def reconcile(self, worker):
        """완료 직후 중단된 CI 결과도 동일한 outbox 키로 재구성한다."""
        campaign = self.store.campaign(self.campaign_id)
        with worker.queue.transaction() as db:
            jobs = [dict(row) for row in db.execute(
                "SELECT * FROM jobs WHERE state IN ('completed','partial') AND "
                "created_at >= ? AND created_at < ? ORDER BY created_at,job_id",
                (campaign["created"], campaign["expires"]))]
        for job in jobs:
            try:
                self.capture(worker, job)
            except ValueError:
                self.store.reject_projection(self.campaign_id, job["job_id"],
                                             job["run_id"] or "unknown", "integrity_rejected")
