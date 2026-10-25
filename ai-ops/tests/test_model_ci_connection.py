"""v2 캠페인의 worker·서명·장부·검토 경계를 네트워크 없이 확인한다."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.collection import SnapshotBuilder
from ai_ops.continuous_ci_bridge import ContinuousCIBridge
from ai_ops.continuous_model import ContinuousModelStore, MockTransport
from ai_ops.contracts import canonical_json_bytes
from ai_ops.live_collectors.common import add
from ai_ops.live_collectors.deployment import _import_ci_history
from ai_ops.model_activation import build_activation_patches
from ai_ops.model_campaign_review import build_review_packet
from ai_ops.model_ci_projection import CI_POLICY, CIModelView, project_ci_snapshot
from ai_ops.model_comparison import rules_projection
from ai_ops.model_gateway_client import ModelGatewayClient
from ai_ops.model_gateway_contract import (CIModelSubmission, ModelSubmission,
    sign_submission, verify_submission)
from ai_ops.model_gateway_transport import GatewayOpenAITransport
from ai_ops.model_policy import policy_spec
from ai_ops.projected_model_engine import CallPlan
from ai_ops.worker import Worker
from test_live_collectors import AT
from test_model_ci_projection import saved_s10_case
from test_model_projection import SENTINEL, example_plan, example_projection
import test_ci_queue as queue_fixtures


class CIModelConnectionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.store = ContinuousModelStore(self.root / "model", clock=lambda: AT.timestamp())
        snapshot, settings, identity, history = saved_s10_case()
        self.projection = project_ci_snapshot(snapshot, settings, identity, history)
        self.identity = identity
        self.rules_ref = f"{identity.incident_id}/{identity.run_id}/report.md"
        self.digest = "sha256:" + "d" * 64
        self.fingerprint = "c" * 64

    def live_plan(self):
        return CallPlan(execution_mode="live", account_label="ci-v2",
            credential_fingerprint=self.fingerprint, price={
                "checked_on": AT.date().isoformat(),
                "source": "https://developers.openai.com/api/docs/pricing",
                "input_per_million": 2.0, "output_per_million": 10.0,
                "cache_write_per_million": 2.5, "cached_input_per_million": 0.2})

    def prepare(self, *, policy=CI_POLICY):
        return self.store.prepare_live_campaign(
            deployment_uid=self.identity.deployment_uid,
            config_hash=self.identity.config_hash,
            cluster_fingerprint=self.identity.cluster_fingerprint,
            image_digest=self.digest, sample_view_hash=self.projection.view_hash,
            plan=self.live_plan(), policy=policy)

    def test_mock_campaign_processes_v2_and_v1_downgrade_is_rejected(self):
        campaign = self.store.prepare_campaign(
            deployment_uid=self.identity.deployment_uid,
            config_hash=self.identity.config_hash,
            cluster_fingerprint=self.identity.cluster_fingerprint,
            plan=example_plan(), policy=CI_POLICY)
        self.store.approve_mock(campaign,
            binding_hash=self.store.campaign(campaign)["binding_hash"], actor="reviewer")
        item = self.store.enqueue(campaign, self.projection,
                                  source_run=self.identity.run_id, rules_ref=self.rules_ref)
        stored = self.store.item(item)
        self.assertEqual(json.loads(stored["view"])["policy_version"], CI_POLICY)
        self.assertNotIn("source_ref", stored["view"])
        report = rules_projection(self.projection.view).model_dump(mode="json")
        transport = MockTransport({"object": "response.input_tokens", "input_tokens": 100},
            {"status": "completed", "model": "gpt-6-sol",
             "usage": {"input_tokens": 100, "output_tokens": 100},
             "output": [{"type": "message", "content": [{"type": "output_text",
                         "text": json.dumps(report)}]}]})
        self.assertEqual(self.store.process_mock(item, transport)["reason"],
                         "semantic_review_required")
        self.assertEqual(transport.calls, ["/v1/responses/input_tokens", "/v1/responses"])
        with self.assertRaises(ValueError):
            self.store.enqueue(campaign, example_projection(),
                               source_run=self.identity.run_id, rules_ref=self.rules_ref)
        with self.assertRaises(ValueError):
            policy_spec("captured-practice-projection-v3")

    def test_live_review_submission_and_activation_bind_v2_without_api_call(self):
        campaign = self.prepare()
        reviewed = build_review_packet(self.store.campaign(campaign), self.projection.view,
            api_fingerprint=self.fingerprint, client_fingerprint="a" * 64,
            clock=lambda: AT.timestamp())
        self.assertFalse(reviewed["activation_allowed"])
        self.assertEqual(reviewed["sample_view"]["policy_version"], CI_POLICY)
        self.assertIn("ci_deployment_state", json.dumps(reviewed["requests"]))
        requests = reviewed["requests"]
        self.assertEqual(requests["/v1/responses/input_tokens"]["body"]["input"],
                         requests["/v1/responses"]["body"]["input"])
        with self.assertRaises(ValueError):
            build_review_packet(self.store.campaign(campaign), example_projection().view,
                api_fingerprint=self.fingerprint, client_fingerprint="a" * 64,
                clock=lambda: AT.timestamp())
        self.store.approve_live(campaign, binding_hash=reviewed["binding_hash"],
            actor="reviewer", optional_sharing_off=True,
            provider_retention_acknowledged=True)
        submission = CIModelSubmission.from_projection(campaign, self.projection,
            rules_ref=self.rules_ref)
        raw = canonical_json_bytes(submission.model_dump(mode="json"))
        credential = b"model-gateway-service-credential-000001"
        parsed = verify_submission(raw, "Bearer " + credential.decode(),
                                   sign_submission(raw, credential), credential)
        self.assertIsInstance(parsed, CIModelSubmission)
        for version in (1, 3):
            altered = submission.model_dump(mode="json")
            altered["schema_version"] = version
            changed = canonical_json_bytes(altered)
            with self.subTest(version=version), self.assertRaises(ValueError):
                verify_submission(changed, "Bearer " + credential.decode(),
                                  sign_submission(changed, credential), credential)
        item = self.store.enqueue_submission(parsed)
        self.assertEqual(item, self.store.enqueue_submission(parsed))
        client = ModelGatewayClient(self.root / "no-secret-needed")
        with patch.object(client, "_request", return_value={"outbox_id": "outbox-" + "f" * 64}) as send:
            client.enqueue(campaign, self.projection,
                           source_run=self.identity.run_id, rules_ref=self.rules_ref)
        self.assertEqual(send.call_args.args[2]["schema_version"], 2)
        self.assertNotIn(SENTINEL, str(send.call_args.args[2]))
        patches = build_activation_patches(self.store.campaign(campaign),
            image="716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/"
                  "urlshortener-practice-ai-ops@" + self.digest,
            cluster_ca_sha256="a" * 64, exporter_sha256="b" * 64,
            clock=lambda: AT.timestamp())
        worker_env = patches["worker"]["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertIn({"name": "AI_MODEL_POLICY", "value": CI_POLICY}, worker_env)
        report = rules_projection(self.projection.view).model_dump(mode="json")
        calls = []
        def respond(_transport, path, _body, *, timeout):
            calls.append(path)
            if path.endswith("input_tokens"):
                return {"object": "response.input_tokens", "input_tokens": 100}
            return {"status": "completed", "model": "gpt-6-sol",
                    "usage": {"input_tokens": 100, "output_tokens": 100},
                    "output": [{"type": "message", "content": [{"type": "output_text",
                                "text": json.dumps(report)}]}]}
        with patch.object(GatewayOpenAITransport, "__call__", respond):
            result = self.store.process_live(item, GatewayOpenAITransport(self.fingerprint),
                                             image_digest=self.digest)
        self.assertEqual(result["reason"], "semantic_review_required")
        self.assertEqual(calls, ["/v1/responses/input_tokens", "/v1/responses"])
        with self.assertRaises(ValueError):
            ModelSubmission.model_validate(submission.model_dump(mode="json"))
        with self.assertRaises(ValueError):
            CIModelSubmission.from_projection(campaign, example_projection(),
                rules_ref=self.rules_ref)

    def test_cross_policy_submission_rejected_before_outbox(self):
        campaign_v1 = self.prepare(policy="captured-practice-projection-v1")
        self.store.approve_live(campaign_v1,
            binding_hash=self.store.campaign(campaign_v1)["binding_hash"], actor="reviewer",
            optional_sharing_off=True, provider_retention_acknowledged=True)
        submission = CIModelSubmission.from_projection(campaign_v1, self.projection,
            rules_ref=self.rules_ref)
        with self.assertRaises(ValueError):
            self.store.enqueue_submission(submission)
        with self.store._transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)

    def test_bridge_uses_v2_only_when_policy_explicit(self):
        fixture = queue_fixtures.CIQueueTest()
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        store = ContinuousModelStore(fixture.root / "model", clock=lambda: fixture.time)
        uid = fixture.history.parsed()[0].observed.deployment_uid
        campaign = store.prepare_campaign(deployment_uid=uid,
            config_hash=fixture.settings.config_hash, cluster_fingerprint="a" * 64,
            plan=example_plan(), policy=CI_POLICY)
        bridge = ContinuousCIBridge(store, campaign, cluster_fingerprint="a" * 64,
            exporter_sha256="b" * 64, policy=CI_POLICY)

        def collect(settings, target, *, ci_history, **_options):
            builder = SnapshotBuilder(settings, target, clock=fixture.clock)
            _import_ci_history(builder, fixture.workload, fixture.app, ci_history)
            at = fixture.clock.now().isoformat().replace("+00:00", "Z")
            builder.workload_expected_pods = 1
            add(builder, "current-deployment", "deployment", at=at,
                payload={"image": SENTINEL + ":v1", "status": "unknown"},
                context={"resource_uid": uid, "resource_version": "1"})
            add(builder, "pod-status", "workload_status", at=at,
                payload={"image": SENTINEL + ":v1", "phase": "Running",
                         "ready": True, "restarts": 0},
                context={"resource_uid": "pod-1", "resource_version": "1",
                         "deployment_uid": uid, "replicaset_uid": "rs-1"})
            add(builder, "pod-config", "config_summary", at=at,
                payload={"read_engine_role": "unknown", "redis_url_configured": False},
                context={"resource_uid": "pod-1", "resource_version": "1"})
            return builder.finish()

        fixture.queue.submit_ci(fixture.settings, fixture.history)
        result = Worker(fixture.queue, fixture.settings,
                        collector=collect, model_bridge=bridge).run_once()
        self.assertEqual(result["state"], "completed")
        with store._transaction() as db:
            row = db.execute("SELECT view FROM outbox").fetchone()
        view = CIModelView.model_validate(json.loads(row["view"]))
        self.assertEqual(sum(item.kind == "ci_deployment_state" for item in view.items), 2)


if __name__ == "__main__":
    unittest.main()
