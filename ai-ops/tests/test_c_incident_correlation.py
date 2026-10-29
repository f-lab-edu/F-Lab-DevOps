"""검증된 이미지 장애 사건·해결 요청을 현재 장애 근거에 결합한다."""

from datetime import timedelta
from dataclasses import replace
from copy import deepcopy
import unittest
from unittest.mock import patch

from pathlib import Path

from ai_ops.c_recovery import (TARGET, RecoveryDraftStore, RecoveryProposal, create_request,
                               draft_verified_proposal, read_existing_request,
                               read_incident_projection)
from ai_ops.c_recovery_proof import assess_incident_request, assess_proposal_recheck
from ai_ops.contracts import sha256_json
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
        deployment_uid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        app = self.live_case.binding.app.model_copy(update={"deployment_uid": deployment_uid})
        self.live_case.binding = self.live_case.binding.model_copy(update={"app": app})
        receipt = self.live_case.history.receipt.model_copy(update={
            "binding_hash": sha256_json(self.live_case.binding.model_dump(mode="json"))})
        self.live_case.history = replace(self.live_case.history, receipt=receipt)
        self.live_case.deployment["metadata"]["uid"] = deployment_uid
        self.live_case.rs["metadata"]["ownerReferences"][0]["uid"] = deployment_uid
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

    def test_two_fresh_reads_create_only_a_v2_draft(self):
        projection = self.projected()
        request = create_request(projection, 12345, now=self.observed + timedelta(seconds=1))
        moment = self.live_case.history.receipt.finished_at + timedelta(hours=2)
        first = assess_incident_request(self.live_case.assess(now=moment), projection, request,
                                        now=moment)
        later = moment + timedelta(seconds=1)
        second = assess_incident_request(self.live_case.assess(now=later), projection, request,
                                         now=later)
        gate = assess_proposal_recheck(first, second, now=later + timedelta(seconds=1))
        self.assertTrue(gate["proposal_ready"])
        self.assertFalse(gate["proposal_allowed"])
        proposal = draft_verified_proposal(request, projection, gate, self.live_case.values,
                                           now=later + timedelta(seconds=2))
        self.assertEqual(proposal.schema_version, 2)
        self.assertEqual(proposal.source_evidence_ids, projection.image_failure.evidence_ids)
        self.assertEqual(proposal.desired_digest, gate["prior_digest"])
        self.assertEqual(proposal.state, "draft")
        self.assertEqual(proposal.failure_action, "stop_and_report_without_unapproved_rollback")
        state = Path(self.case.directory.name) / "c-v2-state"
        store = RecoveryDraftStore(state)
        store.save(request, proposal)
        self.assertEqual(store.read(proposal.proposal_id), proposal)
        forged = deepcopy(proposal.model_dump(mode="json"))
        forged["verification_checks"] = ["argo_synced_healthy"]
        forged["proposal_hash"] = sha256_json({key: value for key, value in forged.items()
                                                if key != "proposal_hash"})
        with self.assertRaises(ValueError):
            RecoveryProposal.model_validate(forged)

    def test_changed_or_recovered_recheck_cannot_make_a_draft(self):
        projection = self.projected()
        request = create_request(projection, 12345, now=self.observed + timedelta(seconds=1))
        moment = self.live_case.history.receipt.finished_at + timedelta(hours=2)
        first = assess_incident_request(self.live_case.assess(now=moment), projection, request,
                                        now=moment)
        later = moment + timedelta(seconds=1)
        changed = assess_incident_request(self.live_case.assess(now=later), projection, request,
                                          now=later)
        changed["base_sha"] = "c" * 40
        gate = assess_proposal_recheck(first, changed, now=later + timedelta(seconds=1))
        self.assertFalse(gate["proposal_ready"])
        self.assertEqual(gate["blockers"], ["incident_recheck_changed_or_stale"])
        with self.assertRaises(ValueError):
            draft_verified_proposal(request, projection, gate, self.live_case.values)
        recovered = dict(changed, incident_ready=False, blockers=["already_recovered"])
        stopped = assess_proposal_recheck(first, recovered, now=later + timedelta(seconds=1))
        self.assertFalse(stopped["proposal_ready"])
        self.assertEqual(stopped["blockers"], ["already_recovered"])
        fresh = assess_incident_request(self.live_case.assess(now=later), projection, request,
                                        now=later)
        stale = assess_proposal_recheck(first, fresh, now=later + timedelta(minutes=1))
        self.assertFalse(stale["proposal_ready"])
        self.assertEqual(stale["blockers"], ["incident_recheck_changed_or_stale"])


if __name__ == "__main__":
    unittest.main()
