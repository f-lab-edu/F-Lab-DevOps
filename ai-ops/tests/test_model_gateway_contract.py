"""worker와 모델 gateway 사이의 정제 입력·서명 경계를 검증한다."""

import json
import unittest

from ai_ops.contracts import canonical_json_bytes
from ai_ops.model_gateway_contract import ModelSubmission, sign_submission, verify_submission
from test_model_projection import SENTINEL, example_projection


class GatewaySubmissionTest(unittest.TestCase):
    def setUp(self):
        self.projection = example_projection()
        self.credential = b"model-gateway-service-credential-000001"
        self.submission = ModelSubmission.from_projection(
            "campaign-" + "1" * 32, self.projection,
            rules_ref=f"{self.projection.identity.incident_id}/{self.projection.identity.run_id}/report.md")
        self.raw = canonical_json_bytes(self.submission.model_dump(mode="json"))

    def test_roundtrip_excludes_raw_evidence_and_source_mapping(self):
        signature = sign_submission(self.raw, self.credential)
        parsed = verify_submission(self.raw, "Bearer " + self.credential.decode(),
                                   signature, self.credential)
        self.assertEqual(parsed, self.submission)
        for forbidden in (SENTINEL, "ev-pod-status", "source_ref", '"mapping":', '"snapshot":'):
            self.assertNotIn(forbidden.encode(), self.raw)
        self.assertEqual(parsed.view_hash, self.projection.view_hash)

    def test_authentication_tampering_and_noncanonical_input_are_rejected(self):
        signature = sign_submission(self.raw, self.credential)
        for raw, token, signed in (
            (self.raw, "Bearer wrong", signature),
            (self.raw, "Bearer " + self.credential.decode(), "0" * 64),
            (self.raw, "Bearer " + self.credential.decode(), None),
            (self.raw + b" ", "Bearer " + self.credential.decode(),
             sign_submission(self.raw + b" ", self.credential)),
        ):
            with self.subTest(raw=raw[-4:], signed=signed), self.assertRaises(ValueError):
                verify_submission(raw, token, signed, self.credential)

    def test_extra_fields_and_binding_changes_are_rejected(self):
        value = self.submission.model_dump(mode="json")
        for change in (
            lambda x: x.update(snapshot={"raw": SENTINEL}),
            lambda x: x.update(mapping=[{"source_id": "ev-pod-status"}]),
            lambda x: x.update(view_hash="0" * 64),
            lambda x: x.update(source_run="run-" + "3" * 32),
            lambda x: x["view"].update(secret=SENTINEL),
        ):
            altered = json.loads(json.dumps(value)); change(altered)
            raw = canonical_json_bytes(altered)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_submission(raw, "Bearer " + self.credential.decode(),
                                  sign_submission(raw, self.credential), self.credential)


if __name__ == "__main__":
    unittest.main()
