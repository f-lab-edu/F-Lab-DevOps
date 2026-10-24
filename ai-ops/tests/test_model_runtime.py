"""모델 gateway 처리 루프가 승인·대상 재검사 뒤 단회 처리하는지 확인한다."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.continuous_model import ContinuousModelStore
from ai_ops.model_comparison import rules_projection
from ai_ops.model_gateway_contract import ModelSubmission
from ai_ops.model_gateway_transport import GatewayOpenAITransport
from ai_ops.model_runtime import ModelGatewayRuntime
from ai_ops.projected_model_engine import CallPlan
from test_live_collectors import AT
from test_model_projection import example_projection


class Probe:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def verify(self, _binding):
        self.calls += 1
        if self.fail:
            raise ValueError("model_probe_deployment_changed")


class ModelRuntimeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(root)})
        environment.start(); self.addCleanup(environment.stop)
        self.store = ContinuousModelStore(root / "model", clock=lambda: AT.timestamp())
        self.projection = example_projection()
        self.key = "test-runtime-openai-key-with-no-network"
        self.fingerprint = hashlib.sha256(self.key.encode()).hexdigest()
        self.image = "sha256:" + "d" * 64
        plan = CallPlan(execution_mode="live", account_label="test",
            credential_fingerprint=self.fingerprint, price={
                "checked_on": AT.date().isoformat(),
                "source": "https://developers.openai.com/api/docs/pricing",
                "input_per_million": 2.0, "output_per_million": 10.0,
                "cache_write_per_million": 2.5, "cached_input_per_million": 0.2})
        identity = self.projection.identity
        self.campaign = self.store.prepare_live_campaign(
            deployment_uid=identity.deployment_uid, config_hash=identity.config_hash,
            cluster_fingerprint=identity.cluster_fingerprint, image_digest=self.image,
            sample_view_hash=self.projection.view_hash, plan=plan)
        self.probe = Probe()
        self.runtime = ModelGatewayRuntime(self.store, self.campaign, self.image, self.probe)

    def approve(self):
        self.store.approve_live(self.campaign,
            binding_hash=self.store.campaign(self.campaign)["binding_hash"], actor="reviewer",
            optional_sharing_off=True, provider_retention_acknowledged=True)

    def enqueue(self):
        identity = self.projection.identity
        return self.store.enqueue_submission(ModelSubmission.from_projection(
            self.campaign, self.projection,
            rules_ref=f"{identity.incident_id}/{identity.run_id}/report.md"))

    def test_no_approval_or_changed_target_never_calls_transport(self):
        with patch("ai_ops.model_runtime.read_cluster_credential", return_value=self.key):
            with self.assertRaises(ValueError):
                self.runtime.verify()
            self.approve()
            item = self.enqueue()
            self.probe.fail = True
            with patch.object(GatewayOpenAITransport, "__call__") as call:
                with self.assertRaises(ValueError):
                    self.runtime.run_once()
                call.assert_not_called()
            self.assertEqual(self.store.item(item)["state"], "prepared")
            self.assertIsNone(self.store.item(item)["reserved_day"])

    def test_processes_one_item_only_after_approval_and_current_probe(self):
        self.approve()
        item = self.enqueue()
        report = rules_projection(self.projection.view).model_dump(mode="json")
        calls = []
        def respond(_transport, path, _body, *, timeout):
            calls.append(path)
            if path.endswith("input_tokens"):
                return {"object": "response.input_tokens", "input_tokens": 100}
            return {"status": "completed", "model": "gpt-6-sol",
                    "usage": {"input_tokens": 100, "output_tokens": 100},
                    "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": json.dumps(report)}]}]}
        with patch("ai_ops.model_runtime.read_cluster_credential", return_value=self.key), patch.object(
                GatewayOpenAITransport, "__call__", respond):
            self.assertEqual(self.runtime.run_once(), item)
            self.assertIsNone(self.runtime.run_once())
        self.assertEqual(calls, ["/v1/responses/input_tokens", "/v1/responses"])
        self.assertEqual(self.probe.calls, 1)
        self.assertEqual(self.store.item(item)["reason"], "semantic_review_required")

    def test_stop_after_token_count_prevents_generation(self):
        self.approve()
        item = self.enqueue()
        calls = []
        def respond(transport, path, _body, *, timeout):
            transport.before_request()
            calls.append(path)
            if path.endswith("input_tokens"):
                self.store.stop(self.campaign)
                return {"object": "response.input_tokens", "input_tokens": 100}
            self.fail("중지한 캠페인은 생성 요청을 보내면 안 된다")
        with patch("ai_ops.model_runtime.read_cluster_credential", return_value=self.key), patch.object(
                GatewayOpenAITransport, "__call__", respond):
            self.assertEqual(self.runtime.run_once(), item)
        self.assertEqual(calls, ["/v1/responses/input_tokens"])
        self.assertEqual(self.store.item(item)["reason"], "campaign_stopped")


if __name__ == "__main__":
    unittest.main()
