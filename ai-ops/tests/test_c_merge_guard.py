"""C-03 병합 요청과 실제 main 결과가 승인한 이미지 변경에 묶이는지 확인한다."""

from datetime import timedelta
import unittest

from ai_ops.c_approval import ApprovalRejected, REPO_API
from ai_ops.c_merge_guard import plan_exact_merge_request, verify_merged_commit
from ai_ops.c_pr_link import make_pr_link
import test_c_approval as approval_fixtures


MERGE = "e" * 40


class MergeGuardTest(unittest.TestCase):
    def setUp(self):
        fixture = approval_fixtures.CApprovalTest(
            "test_exact_review_returns_evidence_without_approving_or_executing")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.proposal = fixture.proposal
        refreshed = fixture.link_proof(approval_fixtures.BASE,
                                       approval_fixtures.NOW - timedelta(seconds=1))
        self.link = make_pr_link(self.proposal, refreshed, approval_fixtures.BASE,
                                 "5" * 40, approval_fixtures.NOW - timedelta(minutes=4))
        self.locked = {
            "locked_preflight_ready": True, "preflight_ready": True, "execution_allowed": False,
            "proposal_id": self.proposal.proposal_id, "pr_link_hash": self.link.link_hash,
            "base_sha": approval_fixtures.BASE, "head_sha": approval_fixtures.HEAD,
            "pull_number": 123, "review_id": 9, "required_check_run_id": 55,
            "executor_actor_id": 33, "checked_at": approval_fixtures.NOW,
            "lease": {"name": "b6-scenario-lock", "uid": "lease-uid",
                      "holder": "c3-" + "f" * 32},
        }
        self.reader = approval_fixtures.FixtureReader(self.proposal)
        self.reader.data[approval_fixtures.MAIN]["object"]["sha"] = MERGE
        self.reader.data[f"{REPO_API}/git/commits/{MERGE}"] = {
            "sha": MERGE, "parents": [{"sha": approval_fixtures.BASE}],
            "tree": {"sha": "2" * 40}, "message": "실습 이미지 원복"}
        self.reader.data[approval_fixtures.PULL].update(
            state="closed", merged=True, merge_commit_sha=MERGE,
            merged_by={"id": 33, "type": "Bot"})

    def verify(self):
        return verify_merged_commit(reader=self.reader, locked=self.locked,
                                    proposal=self.proposal, pr_link=self.link,
                                    merge_response={"merged": True, "sha": MERGE})

    def test_exact_head_squash_request_is_only_a_plan(self):
        plan = plan_exact_merge_request(
            locked=self.locked, proposal=self.proposal, pr_link=self.link,
            now=approval_fixtures.NOW + timedelta(seconds=1))
        self.assertEqual(plan["method"], "PUT")
        self.assertEqual(plan["endpoint"], f"{REPO_API}/pulls/123/merge")
        self.assertEqual(plan["body"], {"sha": approval_fixtures.HEAD,
                                        "merge_method": "squash",
                                        "merge_action": "direct_merge", "bypass_rules": False})
        self.assertFalse(plan["write_enabled"])
        self.assertEqual(self.reader.calls, [])

    def test_stale_or_unbound_plan_is_rejected(self):
        with self.assertRaisesRegex(ApprovalRejected, "locked_preflight_expired_for_merge"):
            plan_exact_merge_request(locked=self.locked, proposal=self.proposal, pr_link=self.link,
                                     now=approval_fixtures.NOW + timedelta(seconds=15))
        self.locked["execution_allowed"] = True
        with self.assertRaisesRegex(ApprovalRejected, "locked_merge_binding_invalid"):
            plan_exact_merge_request(locked=self.locked, proposal=self.proposal, pr_link=self.link,
                                     now=approval_fixtures.NOW)

    def test_merge_result_rechecks_main_pr_and_exact_values_tree(self):
        result = self.verify()
        self.assertTrue(result["merge_verified"])
        self.assertFalse(result["sync_allowed"])
        self.assertEqual(result["merge_sha"], MERGE)
        self.assertEqual(result["desired_digest"], self.proposal.desired_digest)

    def test_wrong_main_actor_or_parent_is_rejected(self):
        self.reader.data[approval_fixtures.MAIN]["object"]["sha"] = "f" * 40
        with self.assertRaisesRegex(ApprovalRejected, "merged_main_sha_mismatch"):
            self.verify()
        self.reader.data[approval_fixtures.MAIN]["object"]["sha"] = MERGE
        self.reader.data[approval_fixtures.PULL]["merged_by"]["id"] = 44
        with self.assertRaisesRegex(ApprovalRejected, "merged_pull_request_binding_mismatch"):
            self.verify()
        self.reader.data[approval_fixtures.PULL]["merged_by"]["id"] = 33
        self.reader.data[f"{REPO_API}/git/commits/{MERGE}"]["parents"] = [{"sha": "f" * 40}]
        with self.assertRaisesRegex(ApprovalRejected, "merged_commit_parent_mismatch"):
            self.verify()

    def test_extra_tree_change_or_race_is_rejected(self):
        self.reader.data[approval_fixtures.tree_key("2")]["tree"][1]["sha"] = "f" * 40
        with self.assertRaisesRegex(ApprovalRejected, "unexpected_git_change"):
            self.verify()
        self.reader = approval_fixtures.FixtureReader(self.proposal)
        self.reader.data[approval_fixtures.MAIN]["object"]["sha"] = MERGE
        self.reader.data[f"{REPO_API}/git/commits/{MERGE}"] = {
            "sha": MERGE, "parents": [{"sha": approval_fixtures.BASE}],
            "tree": {"sha": "2" * 40}}
        self.reader.data[approval_fixtures.PULL].update(
            state="closed", merged=True, merge_commit_sha=MERGE,
            merged_by={"id": 33, "type": "Bot"})
        def close_after_first_read():
            if self.reader.calls.count(approval_fixtures.PULL) == 2:
                self.reader.data[approval_fixtures.PULL]["merged"] = False
        self.reader.hooks[approval_fixtures.PULL] = close_after_first_read
        with self.assertRaisesRegex(ApprovalRejected, "merge_changed_during_verification"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
