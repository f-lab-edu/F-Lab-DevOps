"""C-03 수집 adapter가 현재 원본을 재조회해 변화를 거부하는지 검증한다."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_c_approval as approval_fixtures
import test_c_live_preflight as live_fixtures


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_c2_live_preflight.py"
spec = importlib.util.spec_from_file_location("audit_c2_live_preflight_c3_test", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ExecutionSourceTest(unittest.TestCase):
    def setUp(self):
        fixture = live_fixtures.LivePreflightTest("test_even_ready_helm_needs_verified_previous_digest_and_failure")
        fixture.setUp()
        fixture.app["spec"]["source"].pop("directory")
        fixture.app["status"]["health"]["status"] = "Degraded"
        fixture.deployment["status"]["readyReplicas"] = 0
        fixture.deployment["spec"]["template"]["spec"]["containers"][0]["image"] = (
            fixture.deployment["spec"]["template"]["spec"]["containers"][0]["image"][:-64] + "1" * 64)
        fixture.lease["metadata"].update(uid="lease-uid", resourceVersion="1")
        self.objects = {("argocd", "application", "lab-target"): fixture.app,
                        ("ai-ops-lab", "deployment", "lab-api"): fixture.deployment,
                        ("ai-ops-lab", "lease", "b6-scenario-lock"): fixture.lease}
        self.values = fixture.values
        self.lease_reads = 0
        self.git_reads = 0
        self.change_last_lease = False

    def run_command(self, *args):
        if args[0] == "git":
            self.git_reads += 1
            return self.values.decode()
        self.assertEqual(args[0], "kubectl")
        key = (args[4], args[6], args[7])
        value = deepcopy(self.objects[key])
        if key[1] == "lease":
            self.lease_reads += 1
            if self.change_last_lease and self.lease_reads == 2:
                value["metadata"]["resourceVersion"] = "2"
        return json.dumps(value)

    def test_returns_raw_current_snapshot_after_same_object_recheck(self):
        args = SimpleNamespace(kubeconfig="/tmp/c3-test-kubeconfig", normal_binding=None,
                               operator_state_root=None)
        with patch.object(audit, "_run", side_effect=self.run_command):
            result, _, _, values, raw = audit._collect(args, include_snapshot=True)
        self.assertEqual(self.lease_reads, 2)
        self.assertEqual(result["blockers"], ["prior_digest_and_failure_proof_required"])
        self.assertEqual(raw["values_bytes"], values)
        self.assertEqual(raw["lease"]["metadata"]["uid"], "lease-uid")

    def test_execution_source_uses_fixed_github_blob_instead_of_local_git(self):
        args = SimpleNamespace(kubeconfig="/tmp/c3-test-kubeconfig", normal_binding=None,
                               operator_state_root=None)
        revisions = []
        def remote_values(revision):
            revisions.append(revision)
            return self.values
        with patch.object(audit, "_run", side_effect=self.run_command):
            _, _, _, values, _ = audit._collect(
                args, include_snapshot=True, read_values=remote_values)
        self.assertEqual(revisions, ["a" * 40])
        self.assertEqual(values, self.values)
        self.assertEqual(self.git_reads, 0)

    def test_github_values_reader_uses_fixed_commit_tree_and_blob(self):
        reader = approval_fixtures.FixtureReader(SimpleNamespace(proposal_hash="f" * 64))
        self.assertEqual(audit._github_values(approval_fixtures.BASE, reader=reader),
                         approval_fixtures._encoded_values(approval_fixtures.CURRENT).encode())
        self.assertIn(f"{approval_fixtures.REPO_API}/git/commits/{approval_fixtures.BASE}",
                      reader.calls)

    def test_changed_lease_during_one_collection_is_rejected(self):
        self.change_last_lease = True
        args = SimpleNamespace(kubeconfig="/tmp/c3-test-kubeconfig", normal_binding=None,
                               operator_state_root=None)
        with patch.object(audit, "_run", side_effect=self.run_command):
            with self.assertRaisesRegex(ValueError, "원본 조회 중"):
                audit._collect(args, include_snapshot=True)


if __name__ == "__main__":
    unittest.main()
