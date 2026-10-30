"""C-02 감사 명령의 재조회·초안 저장 분기를 검증한다."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_c2_live_preflight.py"
SPEC = importlib.util.spec_from_file_location("c2_proposal_audit", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class ProposalAuditCLITest(unittest.TestCase):
    def arguments(self):
        return ["audit_c2_live_preflight.py", "--kubeconfig", "/private/tmp/kubeconfig",
                "--output", "/private/tmp/audit.json", "--normal-binding", "/private/tmp/binding.json",
                "--normal-run-id", "12345", "--normal-head", "a" * 40,
                "--operator-state-root", "/private/tmp/state", "--incident-id", "inc-" + "a" * 32,
                "--incident-run-id", "run-" + "b" * 32, "--request-id", "creq-" + "c" * 32,
                "--proposal-recheck", "--proposal-output", "/private/tmp/proposal.json"]

    def automatic_arguments(self):
        values = self.arguments()
        index = values.index("--request-id")
        del values[index:index + 2]
        return [*values, "--create-request"]

    def test_recovered_first_read_does_not_recheck_or_save_draft(self):
        first = {"incident_ready": False, "blockers": ["already_recovered"]}
        with patch.object(sys, "argv", self.arguments()), \
                patch.object(AUDIT, "cli_paths", side_effect=lambda _parser, args, _policy: args), \
                patch.object(AUDIT, "_collect", return_value=(first, None, None, b"")) as collect, \
                patch.object(AUDIT, "write_private_json") as write, \
                patch.object(AUDIT, "RecoveryDraftStore") as store:
            self.assertEqual(AUDIT.main(), 0)
        collect.assert_called_once()
        store.assert_not_called()
        self.assertEqual(write.call_count, 1)
        self.assertFalse(write.call_args.args[1]["proposal_ready"])

    def test_ready_case_rechecks_before_saving_draft(self):
        first = {"incident_ready": True}
        second = {"incident_ready": True}
        gate = {"proposal_ready": True, "proposal_allowed": False}
        draft = SimpleNamespace(model_dump=lambda **_: {"state": "draft", "schema_version": 2})
        with patch.object(sys, "argv", self.arguments()), \
                patch.object(AUDIT, "cli_paths", side_effect=lambda _parser, args, _policy: args), \
                patch.object(AUDIT, "_collect", side_effect=[
                    (first, None, None, b""), (second, "projection", "request", b"values")]) as collect, \
                patch.object(AUDIT, "assess_proposal_recheck", return_value=gate) as recheck, \
                patch.object(AUDIT, "draft_verified_proposal", return_value=draft) as build, \
                patch.object(AUDIT, "write_private_json") as write, \
                patch.object(AUDIT, "RecoveryDraftStore") as store:
            self.assertEqual(AUDIT.main(), 0)
        self.assertEqual(collect.call_count, 2)
        recheck.assert_called_once_with(first, second)
        build.assert_called_once()
        store.return_value.save.assert_called_once_with("request", draft)
        self.assertEqual(write.call_count, 2)

    def test_request_is_not_created_without_current_failure_proof(self):
        blocked = {"proof_ready": False, "blockers": ["already_recovered"]}
        with patch.object(sys, "argv", self.automatic_arguments()), \
                patch.object(AUDIT, "cli_paths", side_effect=lambda _parser, args, _policy: args), \
                patch.object(AUDIT, "_collect", return_value=(blocked, None, None, b"")) as collect, \
                patch.object(AUDIT, "_client") as client, \
                patch.object(AUDIT, "write_private_json") as write, \
                patch.object(AUDIT, "RecoveryDraftStore") as store:
            self.assertEqual(AUDIT.main(), 2)
        collect.assert_called_once()
        self.assertIsNone(collect.call_args.args[0].operator_state_root)
        client.assert_not_called()
        store.assert_not_called()
        self.assertEqual(write.call_count, 1)

    def test_request_and_draft_require_matching_projection_and_fresh_recheck(self):
        proof = {"proof_ready": True, "pod_uid_hash": "pod", "deployment_uid_hash": "dep",
                 "image_hash": "image", "proposal_allowed": False}
        failure = SimpleNamespace(pod_uid_hash="pod", deployment_uid_hash="dep",
                                  image_hash="image", evidence_ids=["ev-pod"])
        projection = SimpleNamespace(image_failure=failure, diagnosis_status="suspected_cause",
                                     current_state="unknown", cited_evidence_ids=["ev-pod"])
        request = SimpleNamespace(request_id="creq-" + "d" * 32)
        first = {"incident_ready": True, "proposal_allowed": False}
        refreshed = {"incident_ready": True, "proposal_allowed": False}
        gate = {"proposal_ready": True, "proposal_allowed": False}
        draft = SimpleNamespace(model_dump=lambda **_: {"state": "draft", "schema_version": 2})
        with patch.object(sys, "argv", self.automatic_arguments()), \
                patch.object(AUDIT, "cli_paths", side_effect=lambda _parser, args, _policy: args), \
                patch.object(AUDIT, "_collect", side_effect=[
                    (proof, None, None, b""), (refreshed, projection, request, b"values")]) as collect, \
                patch.object(AUDIT, "_client", side_effect=[
                    {"status": "ok", "projection": {}}, {"status": "ok", "request": {}}]) as client, \
                patch.object(AUDIT.IncidentProjection, "model_validate", return_value=projection), \
                patch.object(AUDIT.RecoveryRequest, "model_validate", return_value=request), \
                patch.object(AUDIT, "assess_incident_request", return_value=first) as assess, \
                patch.object(AUDIT, "assess_proposal_recheck", return_value=gate) as recheck, \
                patch.object(AUDIT, "draft_verified_proposal", return_value=draft), \
                patch.object(AUDIT, "write_private_json") as write, \
                patch.object(AUDIT, "RecoveryDraftStore") as store:
            self.assertEqual(AUDIT.main(), 0)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(client.call_args_list[0].args[1], "show")
        self.assertEqual(client.call_args_list[1].args[1], "request")
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(collect.call_args_list[1].args[0].request_id, request.request_id)
        assess.assert_called_once_with(proof, projection, request)
        recheck.assert_called_once_with(first, refreshed)
        store.return_value.save.assert_called_once_with(request, draft)
        self.assertEqual(write.call_count, 2)

    def test_mismatched_incident_never_creates_request(self):
        proof = {"proof_ready": True, "pod_uid_hash": "pod", "deployment_uid_hash": "dep",
                 "image_hash": "image", "proposal_allowed": False}
        projection = SimpleNamespace(image_failure=SimpleNamespace(
            pod_uid_hash="other", deployment_uid_hash="dep", image_hash="image",
            evidence_ids=["ev-pod"]), diagnosis_status="suspected_cause",
            current_state="unknown", cited_evidence_ids=["ev-pod"])
        with patch.object(sys, "argv", self.automatic_arguments()), \
                patch.object(AUDIT, "cli_paths", side_effect=lambda _parser, args, _policy: args), \
                patch.object(AUDIT, "_collect", return_value=(proof, None, None, b"")), \
                patch.object(AUDIT, "_client", return_value={"status": "ok", "projection": {}}) as client, \
                patch.object(AUDIT.IncidentProjection, "model_validate", return_value=projection), \
                patch.object(AUDIT, "write_private_json") as write, \
                patch.object(AUDIT, "RecoveryDraftStore") as store:
            self.assertEqual(AUDIT.main(), 2)
        client.assert_called_once()
        store.assert_not_called()
        self.assertEqual(write.call_args.args[1]["blockers"], ["incident_or_live_proof_mismatch"])

    def test_recovered_second_read_does_not_save_draft(self):
        proof = {"proof_ready": True, "pod_uid_hash": "pod", "deployment_uid_hash": "dep",
                 "image_hash": "image", "proposal_allowed": False}
        failure = SimpleNamespace(pod_uid_hash="pod", deployment_uid_hash="dep",
                                  image_hash="image", evidence_ids=["ev-pod"])
        projection = SimpleNamespace(image_failure=failure, diagnosis_status="suspected_cause",
                                     current_state="unknown", cited_evidence_ids=["ev-pod"])
        request = SimpleNamespace(request_id="creq-" + "d" * 32)
        first = {"incident_ready": True, "proposal_allowed": False}
        recovered = {"incident_ready": False, "blockers": ["already_recovered"],
                     "proposal_allowed": False}
        gate = {**recovered, "proposal_ready": False}
        with patch.object(sys, "argv", self.automatic_arguments()), \
                patch.object(AUDIT, "cli_paths", side_effect=lambda _parser, args, _policy: args), \
                patch.object(AUDIT, "_collect", side_effect=[
                    (proof, None, None, b""), (recovered, None, None, b"")]), \
                patch.object(AUDIT, "_client", side_effect=[
                    {"status": "ok", "projection": {}}, {"status": "ok", "request": {}}]), \
                patch.object(AUDIT.IncidentProjection, "model_validate", return_value=projection), \
                patch.object(AUDIT.RecoveryRequest, "model_validate", return_value=request), \
                patch.object(AUDIT, "assess_incident_request", return_value=first), \
                patch.object(AUDIT, "assess_proposal_recheck", return_value=gate), \
                patch.object(AUDIT, "draft_verified_proposal") as build, \
                patch.object(AUDIT, "write_private_json") as write, \
                patch.object(AUDIT, "RecoveryDraftStore") as store:
            self.assertEqual(AUDIT.main(), 2)
        build.assert_not_called()
        store.assert_not_called()
        self.assertEqual(write.call_count, 1)
        self.assertEqual(write.call_args.args[1]["blockers"], ["already_recovered"])


if __name__ == "__main__":
    unittest.main()
