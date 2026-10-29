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


if __name__ == "__main__":
    unittest.main()
