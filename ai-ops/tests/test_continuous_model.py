"""상시 실습 연결의 모의 승인·중복·비용·중단·보관 경계를 검증한다."""

from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.continuous_model import ContinuousModelStore, MockTransport
from ai_ops.model_comparison import rules_projection
from test_model_projection import example_plan, example_projection
from test_live_collectors import AT


class ContinuousModelTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.now = AT.timestamp()
        self.store = ContinuousModelStore(self.root / "model", clock=lambda: self.now)
        self.projection = example_projection()
        self.plan = example_plan()
        identity = self.projection.identity
        self.campaign = self.store.prepare_campaign(deployment_uid=identity.deployment_uid,
            config_hash=identity.config_hash, cluster_fingerprint=identity.cluster_fingerprint,
            plan=self.plan)
        self.reference = f"{identity.incident_id}/{identity.run_id}/report.md"

    def enqueue(self):
        return self.store.enqueue(self.campaign, self.projection,
                                  source_run=self.projection.identity.run_id, rules_ref=self.reference)

    def approve(self):
        binding_hash = self.store.campaign(self.campaign)["binding_hash"]
        self.store.approve_mock(self.campaign, binding_hash=binding_hash, actor="reviewer")

    def mock(self):
        report = rules_projection(self.projection.view).model_dump(mode="json")
        return MockTransport({"object": "response.input_tokens", "input_tokens": 100},
            {"status": "completed", "model": "gpt-6-sol", "usage": {"input_tokens": 100,
              "output_tokens": 100}, "output": [{"type": "message", "content": [
                  {"type": "output_text", "text": json.dumps(report)}]}]})

    def test_no_approval_no_call_and_private_view_only(self):
        identifier = self.enqueue()
        record = self.store.item(identifier)
        self.assertEqual(record["state"], "prepared")
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", record["view"])
        self.assertNotIn("deployment-1", record["view"])
        self.assertNotIn("mapping", record["view"])
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        mock = self.mock()
        with self.assertRaises(ValueError):
            self.store.process_mock(identifier, mock)
        self.assertEqual(mock.calls, [])
        self.assertEqual(self.store.enqueue(self.campaign, self.projection,
                         source_run=self.projection.identity.run_id, rules_ref=self.reference), identifier)
        with self.assertRaises(ValueError):
            self.store.approve_mock(self.campaign, binding_hash="0"*64, actor="reviewer")

    def test_approved_mock_result_is_separate_and_no_retry(self):
        identifier = self.enqueue()
        self.approve()
        mock = self.mock()
        result = self.store.process_mock(identifier, mock)
        self.assertEqual(result["state"], "needs_review")
        self.assertEqual(result["reason"], "semantic_review_required")
        self.assertEqual(mock.calls, ["/v1/responses/input_tokens", "/v1/responses"])
        self.assertEqual(json.loads(result["result"])["execution_status"], "not_executed")
        self.assertEqual(json.loads(result["rules"])["input_hash"],
                         json.loads(result["result"])["report"]["input_hash"])
        with self.assertRaises(ValueError):
            self.store.process_mock(identifier, mock)
        self.assertEqual(len(mock.calls), 2)
        self.assertEqual(ContinuousModelStore(self.root / "model", clock=lambda: self.now).item(identifier)["state"],
                         "needs_review")

    def test_wrong_target_and_live_plan_rejected(self):
        identity = self.projection.identity.model_copy(update={"deployment_uid": "other"})
        changed = self.projection.model_copy(update={"identity": identity})
        with self.assertRaises(ValueError):
            self.store.enqueue(self.campaign, changed, source_run=identity.run_id, rules_ref=self.reference)
        with self.assertRaises(ValueError):
            self.store.enqueue(self.campaign, self.projection,
                               source_run=self.projection.identity.run_id, rules_ref="other/report.md")
        live = self.plan.model_copy(update={"execution_mode": "live", "credential_fingerprint": "a"*64})
        with self.assertRaises(ValueError):
            self.store.prepare_campaign(deployment_uid="deployment-1", config_hash="a"*64,
                cluster_fingerprint="b"*64, plan=live)
        self.enqueue(); self.approve()
        with self.assertRaises(ValueError):
            self.store.process_mock(self.enqueue(), lambda *a, **k: None)

    def test_same_source_run_cannot_change_frozen_snapshot(self):
        self.enqueue()
        changed = self.projection.model_copy(update={"source_bundle_hash": "c"*64})
        with self.assertRaisesRegex(ValueError, "outbox_source_conflict"):
            self.store.enqueue(self.campaign, changed,
                source_run=self.projection.identity.run_id, rules_ref=self.reference)

    def test_stop_expiration_and_unknown_send_do_not_retry(self):
        identifier = self.enqueue(); self.approve()
        self.store.stop(self.campaign)
        with self.assertRaises(ValueError):
            self.store.process_mock(identifier, self.mock())
        with self.assertRaises(ValueError):
            self.enqueue()
        second = self.store.prepare_campaign(deployment_uid=self.projection.identity.deployment_uid,
            config_hash=self.projection.identity.config_hash,
            cluster_fingerprint=self.projection.identity.cluster_fingerprint, plan=self.plan)
        self.campaign = second
        identifier = self.enqueue(); self.approve()
        mock = MockTransport({"object": "response.input_tokens", "input_tokens": 100},
                             TimeoutError("모의 응답 유실"))
        record = self.store.process_mock(identifier, mock)
        self.assertEqual(record["state"], "needs_review")
        self.assertEqual(record["reason"], "projection_generation_unknown")
        with self.assertRaises(ValueError):
            self.store.process_mock(identifier, mock)
        self.now += timedelta(days=8).total_seconds()
        third = self.store.prepare_campaign(deployment_uid=self.projection.identity.deployment_uid,
            config_hash=self.projection.identity.config_hash,
            cluster_fingerprint=self.projection.identity.cluster_fingerprint, plan=self.plan)
        self.campaign = third
        identifier = self.enqueue(); self.approve()
        with self.assertRaises(ValueError):
            self.store.process_mock(identifier, self.mock())

    def test_retention_keeps_budget_record_after_body_removal(self):
        identifier = self.enqueue(); self.approve()
        self.store.process_mock(identifier, self.mock())
        self.now += timedelta(days=8).total_seconds()
        self.assertEqual(self.store.prune(), (1, 0))
        record = self.store.item(identifier)
        self.assertIsNone(record["view"])
        self.assertIsNotNone(record["reserved_day"])
        self.now += timedelta(days=83).total_seconds()
        self.assertEqual(self.store.prune(), (0, 1))
        with self.assertRaises(ValueError):
            self.store.item(identifier)

    def test_ten_case_and_shared_utc_daily_reservations(self):
        self.approve()
        for index in range(10):
            source_run = f"run-{index+1:032x}"
            identity = self.projection.identity.model_copy(update={"run_id": source_run})
            projection = self.projection.model_copy(update={"identity": identity})
            identifier = self.store.enqueue(self.campaign, projection, source_run=source_run,
                rules_ref=f"{identity.incident_id}/{source_run}/report.md")
            self.assertEqual(self.store.process_mock(identifier, self.mock())["state"], "needs_review")
        with self.store._transaction() as db:
            self.assertEqual(db.execute("SELECT reserved_cents FROM budget").fetchone()[0], 100)
        source_run = f"run-{11:032x}"
        identity = self.projection.identity.model_copy(update={"run_id": source_run})
        projection = self.projection.model_copy(update={"identity": identity})
        identifier = self.store.enqueue(self.campaign, projection, source_run=source_run,
            rules_ref=f"{identity.incident_id}/{source_run}/report.md")
        mock = self.mock()
        with self.assertRaises(ValueError):
            self.store.process_mock(identifier, mock)
        self.assertEqual(mock.calls, [])
        second = self.store.prepare_campaign(deployment_uid=identity.deployment_uid,
            config_hash=identity.config_hash, cluster_fingerprint=identity.cluster_fingerprint,
            plan=self.plan)
        self.store.approve_mock(second, binding_hash=self.store.campaign(second)["binding_hash"], actor="reviewer")
        other = self.store.enqueue(second, projection, source_run=source_run,
                                   rules_ref=f"{identity.incident_id}/{source_run}/report.md")
        with self.assertRaises(ValueError):
            self.store.process_mock(other, mock)
        self.assertEqual(mock.calls, [])

    def test_restart_marks_uncertain_send_and_tampered_view_blocks_transport(self):
        identifier = self.enqueue(); self.approve()
        with self.store._transaction() as db:
            db.execute("UPDATE outbox SET state='sending',reserved_day=? WHERE id=?",
                       (AT.date().isoformat(), identifier))
        reopened = ContinuousModelStore(self.root / "model", clock=lambda: self.now)
        self.assertEqual(reopened.item(identifier)["reason"], "unknown_send")
        mock = self.mock()
        with self.assertRaises(ValueError):
            reopened.process_mock(identifier, mock)
        self.assertEqual(mock.calls, [])
        second = reopened.prepare_campaign(deployment_uid=self.projection.identity.deployment_uid,
            config_hash=self.projection.identity.config_hash,
            cluster_fingerprint=self.projection.identity.cluster_fingerprint, plan=self.plan)
        self.campaign = second
        item = self.enqueue()
        reopened.approve_mock(second, binding_hash=reopened.campaign(second)["binding_hash"], actor="reviewer")
        with reopened._transaction() as db:
            view = json.loads(db.execute("SELECT view FROM outbox WHERE id=?", (item,)).fetchone()[0])
            view["items"][0]["source_status"] = "missing"
            db.execute("UPDATE outbox SET view=? WHERE id=?", (json.dumps(view), item))
        with self.assertRaises(ValueError):
            reopened.process_mock(item, mock)
        self.assertEqual(mock.calls, [])

    def test_clock_rollback_cannot_open_a_second_budget_day(self):
        first = self.enqueue(); self.approve()
        self.store.process_mock(first, self.mock())
        self.now -= 60
        second = self.store.prepare_campaign(deployment_uid=self.projection.identity.deployment_uid,
            config_hash=self.projection.identity.config_hash,
            cluster_fingerprint=self.projection.identity.cluster_fingerprint, plan=self.plan)
        self.campaign = second
        self.approve()
        item = self.enqueue()
        mock = self.mock()
        with self.assertRaisesRegex(ValueError, "clock_moved_backwards"):
            self.store.process_mock(item, mock)
        self.assertEqual(mock.calls, [])


if __name__ == "__main__":
    unittest.main()
