"""캠페인 검토 자료가 승인 범위를 고정하고 자격 원문을 숨기는지 확인한다."""

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.continuous_model import ContinuousModelStore
from ai_ops.model_campaign_review import build_review_packet, private_credential_fingerprint
from ai_ops.projected_model_engine import CallPlan
from test_live_collectors import AT
from test_model_projection import example_projection


class ModelCampaignReviewTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.store = ContinuousModelStore(self.root / "model", clock=lambda: AT.timestamp())
        self.projection = example_projection()
        self.key = b"synthetic-api-key-for-local-test"
        self.api_fingerprint = hashlib.sha256(self.key).hexdigest()
        self.client_fingerprint = "b" * 64
        plan = CallPlan(execution_mode="live", account_label="test",
            credential_fingerprint=self.api_fingerprint, price={
                "checked_on": AT.date().isoformat(),
                "source": "https://developers.openai.com/api/docs/pricing",
                "input_per_million": 2.0, "output_per_million": 10.0,
                "cache_write_per_million": 2.5, "cached_input_per_million": 0.2})
        identity = self.projection.identity
        self.campaign_id = self.store.prepare_live_campaign(
            deployment_uid=identity.deployment_uid, config_hash=identity.config_hash,
            cluster_fingerprint=identity.cluster_fingerprint,
            image_digest="sha256:" + "d" * 64,
            sample_view_hash=self.projection.view_hash, plan=plan)

    def packet(self, **changes):
        params = {"api_fingerprint": self.api_fingerprint,
                  "client_fingerprint": self.client_fingerprint,
                  "clock": lambda: AT.timestamp()}
        params.update(changes)
        return build_review_packet(self.store.campaign(self.campaign_id),
                                   self.projection.view, **params)

    def test_prepared_packet_exposes_scope_without_credential(self):
        packet = self.packet()
        self.assertFalse(packet["activation_allowed"])
        self.assertTrue(packet["automatic_future_inputs"])
        self.assertEqual(len(packet["requests"]), 2)
        self.assertNotIn(self.key.decode(), str(packet))
        self.assertEqual(packet["limit"]["cases"], 10)

    def test_changed_credential_and_approved_state_rejected(self):
        with self.assertRaisesRegex(ValueError, "credential_changed"):
            self.packet(api_fingerprint="c" * 64)
        self.store.approve_live(self.campaign_id,
            binding_hash=self.store.campaign(self.campaign_id)["binding_hash"], actor="reviewer",
            optional_sharing_off=True, provider_retention_acknowledged=True)
        with self.assertRaisesRegex(ValueError, "scope_changed"):
            self.packet()

    def test_private_file_only_and_no_raw_value(self):
        path = self.root / "key"
        path.write_bytes(self.key)
        path.chmod(0o600)
        self.assertEqual(private_credential_fingerprint(path, kind="api"), self.api_fingerprint)
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "permissions"):
            private_credential_fingerprint(path, kind="api")
        path.chmod(0o600)
        link = self.root / "key-link"
        link.symlink_to(path)
        with self.assertRaises(OSError):
            private_credential_fingerprint(link, kind="api")


if __name__ == "__main__":
    unittest.main()
