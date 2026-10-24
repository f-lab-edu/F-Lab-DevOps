"""승인 전 활성 패치 생성 금지와 패치의 자격·대상 범위를 검증한다."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.continuous_model import ContinuousModelStore
from ai_ops.model_activation import build_activation_patches
from ai_ops.projected_model_engine import CallPlan
from test_live_collectors import AT
from test_model_projection import example_projection


IMAGE = ("716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/"
         "urlshortener-practice-ai-ops@sha256:" + "d" * 64)


class ModelActivationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(root)})
        environment.start(); self.addCleanup(environment.stop)
        self.store = ContinuousModelStore(root / "model", clock=lambda: AT.timestamp())
        projection = example_projection()
        identity = projection.identity
        plan = CallPlan(execution_mode="live", account_label="test",
            credential_fingerprint="c" * 64, price={
                "checked_on": AT.date().isoformat(),
                "source": "https://developers.openai.com/api/docs/pricing",
                "input_per_million": 2.0, "output_per_million": 10.0,
                "cache_write_per_million": 2.5, "cached_input_per_million": 0.2})
        self.campaign_id = self.store.prepare_live_campaign(
            deployment_uid=identity.deployment_uid, config_hash=identity.config_hash,
            cluster_fingerprint=identity.cluster_fingerprint,
            image_digest=IMAGE.split("@")[1], sample_view_hash=projection.view_hash,
            plan=plan)

    def patches(self):
        return build_activation_patches(self.store.campaign(self.campaign_id), image=IMAGE,
            cluster_ca_sha256="a" * 64, exporter_sha256="b" * 64,
            clock=lambda: AT.timestamp())

    def test_prepared_campaign_cannot_produce_active_patch(self):
        with self.assertRaises(ValueError):
            self.patches()

    def test_approved_patch_names_only_secrets_and_fixed_target(self):
        self.store.approve_live(self.campaign_id,
            binding_hash=self.store.campaign(self.campaign_id)["binding_hash"],
            actor="reviewer", optional_sharing_off=True,
            provider_retention_acknowledged=True)
        patches = self.patches()
        self.assertEqual(set(patches), {"gateway", "proxy", "worker"})
        for item in patches.values():
            self.assertEqual(item["spec"]["replicas"], 1)
        gateway = patches["gateway"]["spec"]["template"]["spec"]["containers"][0]
        self.assertIn({"name": "AI_MODEL_ENABLED", "value": "true"}, gateway["env"])
        worker = patches["worker"]["spec"]["template"]["spec"]
        self.assertEqual(worker["volumes"][0]["secret"]["secretName"], "model-client-auth")
        raw = json.dumps(patches)
        self.assertNotIn("sk-", raw)
        self.assertNotIn("OPENAI_API_KEY", raw)
        with self.assertRaises(ValueError):
            build_activation_patches(self.store.campaign(self.campaign_id),
                image=IMAGE.replace("d" * 64, "e" * 64), cluster_ca_sha256="a" * 64,
                exporter_sha256="b" * 64, clock=lambda: AT.timestamp())
        with self.assertRaises(ValueError):
            build_activation_patches(self.store.campaign(self.campaign_id), image=IMAGE,
                cluster_ca_sha256="a" * 64, exporter_sha256="b" * 64,
                clock=lambda: AT.timestamp() + 86400)


if __name__ == "__main__":
    unittest.main()
