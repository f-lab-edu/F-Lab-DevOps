"""검증된 이미지 장애 사건·해결 요청을 현재 장애 근거에 결합한다."""

from datetime import timedelta
import unittest
from unittest.mock import patch

from pathlib import Path

from ai_ops.c_recovery import (TARGET, RecoveryDraftStore, create_request,
                               read_existing_request, read_incident_projection)
from ai_ops.c_recovery_proof import assess_incident_request
import test_c_recovery_proof as live_fixtures
import test_captured_rules_v3 as rules_fixtures
from test_live_collectors import AT


class LaterClock:
    def __init__(self, moment):
        self.moment = moment

    def now(self):
        return self.moment

    def monotonic(self):
        return 0.0


class IncidentCorrelationTest(unittest.TestCase):
    def setUp(self):
        self.live_case = live_fixtures.RecoveryProofTest()
        self.live_case.setUp()
        self.case = rules_fixtures.CapturedRulesV3Test()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.settings.bindings.bindings[0].target = TARGET
        self.case.target = TARGET
        self.observed = self.live_case.history.receipt.finished_at + timedelta(hours=1, seconds=5)

    def projected(self, **changes):
        with patch("test_captured_rules_v3.AT", self.observed), \
                patch("test_captured_rules_v3.Clock", return_value=LaterClock(self.observed)):
            snapshot = self.case.snapshot(pod_image=self.live_case.image,
                deployment_image=self.live_case.image,
                deployment_uid=self.live_case.binding.app.deployment_uid,
                pod_deployment_uid=self.live_case.binding.app.deployment_uid, **changes)
        record = self.case.investigate(snapshot, request="c-current-image")
        return read_incident_projection(self.case.store.root, record.incident_id, record.run_id)

    def test_captured_triad_and_authenticated_request_can_match_live_proof(self):
        projection = self.projected()
        failure = projection.image_failure
        self.assertIsNotNone(failure)
        self.assertEqual(failure.evidence_ids,
                         ["ev-deployment-object", "ev-event-image", "ev-pod-status"])
        self.assertNotIn("pod-1", projection.model_dump_json())
        request = create_request(projection, 12345, now=self.observed + timedelta(seconds=1))
        self.assertEqual(len(request.failure_binding_hash), 64)
        state = Path(self.case.directory.name) / "c-state"
        RecoveryDraftStore(state).save_request(request)
        self.assertEqual(read_existing_request(state, request.request_id), request)
        live = self.live_case.assess()
        result = assess_incident_request(live, projection, request,
            now=self.live_case.history.receipt.finished_at + timedelta(hours=2, seconds=10))
        self.assertTrue(result["incident_ready"])
        self.assertFalse(result["proposal_allowed"])
        self.assertEqual(result["blockers"], ["fresh_recheck_and_proposal_contract_required"])

    def test_unrelated_or_stale_incident_cannot_be_used(self):
        projection = self.projected()
        request = create_request(projection, 12345, now=self.observed + timedelta(seconds=1))
        live = self.live_case.assess()
        changed = dict(live, pod_uid_hash="0" * 64)
        self.assertEqual(assess_incident_request(changed, projection, request,
                         now=self.live_case.history.receipt.finished_at + timedelta(hours=2, seconds=10))
                         ["blockers"], ["incident_or_request_mismatch"])
        stale = assess_incident_request(live, projection, request,
            now=self.live_case.history.receipt.finished_at + timedelta(hours=2, minutes=2))
        self.assertEqual(stale["blockers"], ["incident_or_live_proof_stale"])
        wrong = request.model_copy(update={"bundle_hash": "a" * 64})
        self.assertEqual(assess_incident_request(live, projection, wrong,
                         now=self.live_case.history.receipt.finished_at + timedelta(hours=2, seconds=10))
                         ["blockers"], ["incident_or_request_mismatch"])

    def test_missing_event_has_no_current_image_binding(self):
        projection = self.projected(event_missing=True)
        self.assertIsNone(projection.image_failure)
        request = create_request(projection, 12345, now=self.observed + timedelta(seconds=1))
        self.assertIsNone(request.failure_binding_hash)
        result = assess_incident_request(self.live_case.assess(), projection, request,
            now=self.live_case.history.receipt.finished_at + timedelta(hours=2, seconds=10))
        self.assertEqual(result["blockers"], ["incident_or_request_mismatch"])


if __name__ == "__main__":
    unittest.main()
