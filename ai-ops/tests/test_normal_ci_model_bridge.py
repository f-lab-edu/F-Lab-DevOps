"""정상 CI 영수증의 승인 캠페인 연결·중복·전송 불명 경계를 검사한다."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.job_queue import JobQueue
from ai_ops.continuous_model import ContinuousModelStore
from ai_ops.model_gateway_contract import SequenceModelSubmission
from ai_ops.model_sequence_projection import SEQUENCE_POLICY
from ai_ops.normal_ci_history import verify_normal_ci_history
from ai_ops.normal_ci_inbox import NormalCIInbox
from ai_ops.normal_ci_model_bridge import NormalCIModelBridge
from test_normal_ci import fixture
from test_normal_ci_handoff import Reader
from test_model_projection import example_plan
from ai_ops.projected_model_engine import CallPlan


CAMPAIGN = "campaign-" + "a" * 32
OUTBOX = "outbox-" + "b" * 64


class Gateway:
    def __init__(self, receipt, *, state="approved", fail=False):
        self.receipt = receipt
        self.state = state
        self.fail = fail
        self.calls = []

    def campaign(self, _identifier):
        finished = self.receipt.finished_at.timestamp()
        return {"state": self.state, "created": finished - 60,
                "approved_at": finished - 30, "expires": finished + 3600}

    def enqueue_normal_receipt(self, identifier, receipt, binding):
        submission = SequenceModelSubmission.from_normal_receipt(identifier, receipt, binding)
        self.calls.append(submission)
        if self.fail:
            raise TimeoutError("응답 유실")
        return OUTBOX


class NormalCIModelBridgeTest(unittest.TestCase):
    def setUp(self):
        _, _, _, self.binding, _, self.receipt = fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        env.start(); self.addCleanup(env.stop)
        self.queue = JobQueue(self.root / "store")
        self.inbox = NormalCIInbox(self.queue, self.binding)
        history = verify_normal_ci_history(12345, "a" * 40, self.binding,
                                           reader=Reader(self.receipt))
        self.key, _ = self.inbox.submit(history)
        self.assertEqual(self.inbox.process_one()["state"], "ready")

    def bridge(self, gateway, *, clock=lambda: 0):
        return NormalCIModelBridge(self.inbox, gateway, CAMPAIGN, clock=clock)

    def test_approved_v3_view_is_submitted_once_without_raw_source(self):
        gateway = Gateway(self.receipt)
        bridge = self.bridge(gateway)
        self.assertEqual(bridge.process_one()["state"], "submitted")
        self.assertEqual(bridge.status(self.key), {"state": "submitted", "outbox_id": OUTBOX})
        self.assertIsNone(self.bridge(gateway).process_one())
        self.assertEqual(len(gateway.calls), 1)
        submission = gateway.calls[0]
        self.assertEqual(submission.schema_version, 3)
        self.assertEqual(submission.config_hash, self.binding.config_hash)
        self.assertNotIn("raw_logs", submission.model_dump_json())

    def test_inactive_or_old_records_are_not_submitted(self):
        gateway = Gateway(self.receipt, state="prepared")
        self.assertIsNone(self.bridge(gateway).process_one())
        self.assertEqual(gateway.calls, [])
        self.assertIsNone(self.bridge(Gateway(self.receipt), clock=lambda: 10**12).process_one())

    def test_preapproval_record_is_not_submitted_after_approval(self):
        gateway = Gateway(self.receipt)
        gateway.campaign = lambda _identifier: {
            "state": "approved", "created": self.receipt.finished_at.timestamp() - 60,
            "approved_at": self.receipt.finished_at.timestamp() + 1,
            "expires": self.receipt.finished_at.timestamp() + 3600}
        bridge = self.bridge(gateway)
        self.assertEqual(bridge.process_one()["state"], "needs_review")
        self.assertEqual(gateway.calls, [])

    def test_unknown_delivery_is_recorded_without_retry(self):
        gateway = Gateway(self.receipt, fail=True)
        bridge = self.bridge(gateway)
        self.assertEqual(bridge.process_one()["state"], "needs_review")
        self.assertEqual(bridge.status(self.key)["state"], "needs_review")
        self.assertIsNone(self.bridge(gateway).process_one())
        self.assertEqual(len(gateway.calls), 1)

    def test_tampered_ready_receipt_cannot_be_submitted(self):
        with self.queue.transaction() as db:
            db.execute("UPDATE normal_ci_receipts SET payload=? WHERE episode_key=?",
                       ('{"forged":true}', self.key))
        gateway = Gateway(self.receipt)
        self.assertEqual(self.bridge(gateway).process_one()["state"], "needs_review")
        self.assertEqual(gateway.calls, [])

    def test_real_gateway_ledger_accepts_receipt_without_model_call(self):
        moment = self.receipt.finished_at.timestamp() - 1
        store = ContinuousModelStore(self.root / "model", clock=lambda: moment)
        plan = example_plan().model_dump(mode="json")
        plan.update(execution_mode="live", credential_fingerprint="a" * 64)
        plan["price"].update(cache_write_per_million=2.5,
                             cached_input_per_million=0.2)
        campaign = store.prepare_live_campaign(
            deployment_uid=self.binding.app.deployment_uid,
            config_hash=self.binding.config_hash,
            cluster_fingerprint=self.binding.cluster_fingerprint,
            image_digest="sha256:" + "b" * 64,
            sample_view_hash=self.receipt.view_hash,
            plan=CallPlan.model_validate(plan), policy=SEQUENCE_POLICY)
        store.approve_live(campaign, binding_hash=store.campaign(campaign)["binding_hash"],
                           actor="operator", optional_sharing_off=True,
                           provider_retention_acknowledged=True)
        submission = SequenceModelSubmission.from_normal_receipt(
            campaign, self.receipt, self.binding)
        outbox = store.enqueue_submission(submission)
        self.assertEqual(store.item(outbox)["state"], "prepared")
        self.assertIsNone(store.item(outbox)["reserved_day"])


if __name__ == "__main__":
    unittest.main()
