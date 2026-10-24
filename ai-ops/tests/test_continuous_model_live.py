"""실제 캠페인 코드의 승인·예약·단회 처리 경계를 네트워크 없이 확인한다."""

import json
import http.client
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from ai_ops.continuous_model import ContinuousModelStore, MockTransport
from ai_ops.model_comparison import rules_projection
from ai_ops.model_gateway_contract import ModelSubmission
from ai_ops.model_gateway import _Handler
from ai_ops.model_gateway_client import ModelGatewayClient
from ai_ops.model_gateway_transport import GatewayOpenAITransport
from ai_ops.projected_model_engine import CallPlan
from test_live_collectors import AT
from test_model_projection import example_projection


class LiveCampaignTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.now = AT.timestamp()
        self.store = ContinuousModelStore(self.root / "model", clock=lambda: self.now)
        self.projection = example_projection()
        self.digest = "sha256:" + "d" * 64
        self.fingerprint = "c" * 64
        self.plan = CallPlan(execution_mode="live", account_label="test",
            credential_fingerprint=self.fingerprint, price={
                "checked_on": AT.date().isoformat(),
                "source": "https://developers.openai.com/api/docs/pricing",
                "input_per_million": 2.0, "output_per_million": 10.0,
                "cache_write_per_million": 2.5,
                "cached_input_per_million": 0.2})
        identity = self.projection.identity
        self.campaign = self.store.prepare_live_campaign(
            deployment_uid=identity.deployment_uid, config_hash=identity.config_hash,
            cluster_fingerprint=identity.cluster_fingerprint,
            image_digest=self.digest, sample_view_hash=self.projection.view_hash,
            plan=self.plan)
        self.submission = ModelSubmission.from_projection(self.campaign, self.projection,
            rules_ref=f"{identity.incident_id}/{identity.run_id}/report.md")

    def approve(self):
        self.store.approve_live(self.campaign,
            binding_hash=self.store.campaign(self.campaign)["binding_hash"], actor="reviewer",
            optional_sharing_off=True, provider_retention_acknowledged=True)

    def test_no_approval_or_wrong_acknowledgement_blocks_enqueue(self):
        with self.assertRaises(ValueError):
            self.store.enqueue_submission(self.submission)
        for sharing, retention in ((False, True), (True, False)):
            with self.assertRaises(ValueError):
                self.store.approve_live(self.campaign,
                    binding_hash=self.store.campaign(self.campaign)["binding_hash"],
                    actor="reviewer", optional_sharing_off=sharing,
                    provider_retention_acknowledged=retention)
        with self.assertRaises(ValueError):
            self.store.approve_mock(self.campaign,
                binding_hash=self.store.campaign(self.campaign)["binding_hash"], actor="reviewer")

    def test_bound_submission_reserves_before_two_calls_and_never_retries(self):
        self.approve()
        item = self.store.enqueue_submission(self.submission)
        self.assertEqual(self.store.enqueue_submission(self.submission), item)
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", self.store.item(item)["view"])
        self.assertNotIn('"mapping":', self.store.item(item)["projection_binding"])
        with self.assertRaises(ValueError):
            self.store.process_live(item, GatewayOpenAITransport("0" * 64),
                                    image_digest=self.digest)
        with self.assertRaises(ValueError):
            self.store.process_live(item, GatewayOpenAITransport(self.fingerprint),
                                    image_digest="sha256:" + "e" * 64)
        with self.store._transaction() as db:
            self.assertIsNone(db.execute("SELECT reserved_day FROM outbox WHERE id=?", (item,)).fetchone()[0])
        transport = GatewayOpenAITransport(self.fingerprint)
        report = rules_projection(self.projection.view).model_dump(mode="json")
        calls = []
        def respond(_transport, path, _body, *, timeout):
            calls.append(path)
            with self.store._transaction() as db:
                self.assertEqual(db.execute("SELECT state FROM outbox WHERE id=?", (item,)).fetchone()[0], "sending")
                self.assertEqual(db.execute("SELECT reserved_cents FROM budget").fetchone()[0], 10)
            if path.endswith("input_tokens"):
                return {"object": "response.input_tokens", "input_tokens": 100}
            return {"status": "completed", "model": "gpt-6-sol",
                    "usage": {"input_tokens": 100, "output_tokens": 100},
                    "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": json.dumps(report)}]}]}
        with patch.object(GatewayOpenAITransport, "__call__", respond):
            result = self.store.process_live(item, transport, image_digest=self.digest)
        self.assertEqual(result["reason"], "semantic_review_required")
        self.assertEqual(calls, ["/v1/responses/input_tokens", "/v1/responses"])
        self.assertEqual(json.loads(result["result"])["execution_status"], "not_executed")
        with self.assertRaises(ValueError):
            self.store.process_live(item, transport, image_digest=self.digest)
        self.assertEqual(len(calls), 2)

    def test_failed_first_call_still_reserves_and_restart_does_not_resend(self):
        self.approve()
        item = self.store.enqueue_submission(self.submission)
        transport = GatewayOpenAITransport(self.fingerprint)
        with patch.object(GatewayOpenAITransport, "__call__", side_effect=OSError("offline")):
            result = self.store.process_live(item, transport, image_digest=self.digest)
        self.assertEqual(result["reason"], "projection_token_count_unavailable")
        self.assertIsNotNone(result["reserved_day"])
        with self.assertRaises(ValueError):
            self.store.process_live(item, transport, image_digest=self.digest)
        reopened = ContinuousModelStore(self.root / "model", clock=lambda: self.now)
        self.assertEqual(reopened.item(item)["state"], "needs_review")

    def test_worker_client_submits_only_signed_view_to_local_gateway(self):
        self.approve()
        credential = b"test-model-gateway-service-credential-001"
        secret = self.root / "service-token"
        secret.write_bytes(credential)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.model_store = self.store
        server.model_campaign_id = self.campaign
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        original = http.client.HTTPConnection
        def local_connection(host, port, *, timeout):
            self.assertEqual((host, port),
                ("model-gateway.ai-model.svc.cluster.local", 8081))
            return original("127.0.0.1", server.server_port, timeout=timeout)
        client = ModelGatewayClient(secret)
        with patch.object(_Handler, "_credential", return_value=credential), patch(
                "ai_ops.model_gateway_client.http.client.HTTPConnection", local_connection):
            self.assertEqual(client.campaign(self.campaign)["state"], "approved")
            self.assertFalse(client.has_source(self.campaign, self.submission.source_run))
            item = client.enqueue(self.campaign, self.projection,
                source_run=self.submission.source_run, rules_ref=self.submission.rules_ref)
            self.assertEqual(item, self.store.enqueue_submission(self.submission))
            self.assertTrue(client.has_source(self.campaign, self.submission.source_run))
            job = "job-" + "7" * 32
            self.assertFalse(client.has_rejection(self.campaign, job))
            client.reject_projection(self.campaign, job, self.submission.source_run)
            self.assertTrue(client.has_rejection(self.campaign, job))
        self.assertEqual(self.store.item(item)["state"], "prepared")


if __name__ == "__main__":
    unittest.main()
