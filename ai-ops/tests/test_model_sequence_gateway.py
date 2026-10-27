"""v3 순서 입력의 서명·정책 결합과 자동 대기열 경계를 모의로 확인한다."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.continuous_model import ContinuousModelStore, MockTransport
from ai_ops.contracts import canonical_json_bytes
from ai_ops.model_gateway_contract import (SequenceModelSubmission, sign_submission,
                                            verify_submission)
from ai_ops.model_gateway_client import ModelGatewayClient
from ai_ops.model_campaign_review import build_review_packet
from ai_ops.model_policy import policy_spec
from ai_ops.model_sequence_projection import SEQUENCE_POLICY
from ai_ops.projected_model_engine import CallPlan
from ai_ops.projected_sequence_model_engine import (rules_sequence_projection,
                                                     SEQUENCE_PROMPT_VERSION)
from test_live_collectors import AT
from test_model_projection import example_plan
from test_model_sequence_projection import case, project


class SequenceGatewayTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        snapshot, settings, base, probe, logs = case()
        self.projection = project(snapshot, settings, base, probe, logs)
        self.identity = self.projection.base.identity
        self.store = ContinuousModelStore(self.root/"model", clock=lambda: AT.timestamp())
        self.plan = example_plan()
        self.ref = f"{self.identity.incident_id}/{self.identity.run_id}/report.md"
        self.credential = b"model-gateway-service-credential-000001"

    def campaign(self, *, live=False, policy=SEQUENCE_POLICY, approve=True):
        plan = self.plan
        if live:
            data = plan.model_dump(mode="json")
            data.update(execution_mode="live", credential_fingerprint="a"*64)
            data["price"].update(cache_write_per_million=2.5,
                                 cached_input_per_million=0.2)
            plan = CallPlan.model_validate(data)
            identifier = self.store.prepare_live_campaign(
                deployment_uid=self.identity.deployment_uid,
                config_hash=self.identity.config_hash,
                cluster_fingerprint=self.identity.cluster_fingerprint,
                image_digest="sha256:"+"b"*64,
                sample_view_hash=self.projection.view_hash,
                plan=plan, policy=policy)
            if approve:
                self.store.approve_live(identifier,
                    binding_hash=self.store.campaign(identifier)["binding_hash"],
                    actor="operator", optional_sharing_off=True,
                    provider_retention_acknowledged=True)
        else:
            identifier = self.store.prepare_campaign(
                deployment_uid=self.identity.deployment_uid,
                config_hash=self.identity.config_hash,
                cluster_fingerprint=self.identity.cluster_fingerprint,
                plan=plan, policy=policy)
            self.store.approve_mock(identifier,
                binding_hash=self.store.campaign(identifier)["binding_hash"],
                actor="operator")
        return identifier

    def test_signed_v3_submission_excludes_probe_and_cannot_downgrade(self):
        identifier = self.campaign()
        submission = SequenceModelSubmission.from_projection(identifier, self.projection,
                                                               rules_ref=self.ref)
        raw = canonical_json_bytes(submission.model_dump(mode="json"))
        parsed = verify_submission(raw, "Bearer "+self.credential.decode(),
                                   sign_submission(raw, self.credential), self.credential)
        self.assertEqual(parsed, submission)
        self.assertEqual(parsed.schema_version, 3)
        self.assertEqual(parsed.view_hash, self.projection.view_hash)
        self.assertEqual(policy_spec(SEQUENCE_POLICY).prompt, SEQUENCE_PROMPT_VERSION)
        for secret in (b'"probe":', b'"mapping":', b'"raw_logs":', b'"item_id":', b'"pod_uid":'):
            self.assertNotIn(secret, raw)
        value = submission.model_dump(mode="json")
        for mutate in (lambda data: data.update(schema_version=1),
                       lambda data: data["view"].update(policy_version="captured-practice-projection-v1"),
                       lambda data: data.update(view_hash="0"*64),
                       lambda data: data["view"].update(raw_logs="PRIVATE_SENTINEL")):
            changed = json.loads(json.dumps(value)); mutate(changed)
            body = canonical_json_bytes(changed)
            with self.assertRaises(ValueError):
                verify_submission(body, "Bearer "+self.credential.decode(),
                                  sign_submission(body, self.credential), self.credential)

    def test_v3_mock_outbox_uses_sequence_prompt_and_one_generation(self):
        identifier = self.campaign()
        outbox = self.store.enqueue(identifier, self.projection,
                                    source_run=self.identity.run_id, rules_ref=self.ref)
        row = self.store.item(outbox)
        self.assertEqual(json.loads(row["view"])["policy_version"], SEQUENCE_POLICY)
        self.assertNotIn("item_id", row["view"])
        self.assertEqual(self.store.enqueue(identifier, self.projection,
            source_run=self.identity.run_id, rules_ref=self.ref), outbox)
        report = rules_sequence_projection(self.projection.view).model_dump(mode="json")
        transport = MockTransport({"object": "response.input_tokens", "input_tokens": 100},
            {"status": "completed", "model": "gpt-6-sol",
             "usage": {"input_tokens": 100, "output_tokens": 100},
             "output": [{"type": "message", "content": [{"type": "output_text",
                                                           "text": json.dumps(report)}]}]})
        result = self.store.process_mock(outbox, transport)
        self.assertEqual(result["state"], "needs_review")
        self.assertEqual(result["reason"], "semantic_review_required")
        self.assertEqual(transport.calls, ["/v1/responses/input_tokens", "/v1/responses"])
        with self.assertRaises(ValueError): self.store.process_mock(outbox, transport)

    def test_live_gateway_accepts_only_approved_v3_policy_without_sending(self):
        identifier = self.campaign(live=True)
        submission = SequenceModelSubmission.from_projection(identifier, self.projection,
                                                               rules_ref=self.ref)
        outbox = self.store.enqueue_submission(submission)
        self.assertEqual(self.store.item(outbox)["state"], "prepared")
        self.assertIsNone(self.store.item(outbox)["reserved_day"])
        self.assertFalse(list(self.store.root.glob("budget*")))
        wrong_policy = self.campaign(live=True, policy="captured-practice-projection-v2")
        wrong = SequenceModelSubmission.from_projection(wrong_policy, self.projection,
                                                        rules_ref=self.ref)
        with self.assertRaises(ValueError): self.store.enqueue_submission(wrong)

    def test_v3_review_exposes_same_sequence_input_for_both_requests(self):
        identifier = self.campaign(live=True, approve=False)
        packet = build_review_packet(self.store.campaign(identifier), self.projection.view,
            api_fingerprint="a"*64, client_fingerprint="b"*64,
            clock=lambda: AT.timestamp())
        self.assertFalse(packet["activation_allowed"])
        self.assertEqual(packet["sample_view"]["policy_version"], SEQUENCE_POLICY)
        requests = packet["requests"]
        self.assertEqual(requests["/v1/responses/input_tokens"]["body"]["input"],
                         requests["/v1/responses"]["body"]["input"])
        self.assertIn("cache_probe_sequence", json.dumps(requests))
        submission = SequenceModelSubmission.from_projection(identifier, self.projection,
                                                               rules_ref=self.ref)
        with self.assertRaises(ValueError): self.store.enqueue_submission(submission)

    def test_v3_client_uses_version_three_and_rejects_wrong_source_before_request(self):
        client = ModelGatewayClient()
        identifier = self.campaign()
        outbox = "outbox-"+"a"*64
        with patch.object(client, "_request", return_value={"outbox_id": outbox}) as request:
            self.assertEqual(client.enqueue(identifier, self.projection,
                source_run=self.identity.run_id, rules_ref=self.ref), outbox)
            method, path, payload = request.call_args.args
            self.assertEqual((method, path, payload["schema_version"]), ("POST", "/v1/submit", 3))
            request.reset_mock()
            with self.assertRaises(ValueError):
                client.enqueue(identifier, self.projection, source_run="run-"+"0"*32,
                               rules_ref=self.ref)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
