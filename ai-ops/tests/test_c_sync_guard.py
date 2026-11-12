"""병합 SHA의 고정 동기화 계획과 Argo 적용 증거를 로컬 fixture로 확인한다."""

from copy import deepcopy
from datetime import timedelta
import unittest

from ai_ops.b6_coordination import LabLease
from ai_ops.c_approval import ApprovalRejected, REPO_API
from ai_ops.c_execution_preflight import acquire_locked_preflight
from ai_ops.c_merge_guard import verify_merged_commit
from ai_ops.c_sync_guard import plan_exact_sync, verify_sync_result
from ai_ops.c_recovery import IMAGE_REPOSITORY
import test_c_execution_preflight as execution_fixtures
from test_c_approval import BASE, HEAD, MAIN, NOW, PULL


MERGE = "e" * 40


class SyncGuardTest(unittest.TestCase):
    def setUp(self):
        fixture = execution_fixtures.ExecutionPreflightTest(
            "test_fresh_review_gate_identity_and_runtime_are_only_readiness")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        command = execution_fixtures.LeaseCommand(fixture.live)
        self.locked = acquire_locked_preflight(
            lease=LabLease(command, "c3-" + "f" * 32, clock=lambda: NOW),
            **fixture.preflight_args())
        fixture.reader.data[MAIN]["object"]["sha"] = MERGE
        fixture.reader.data[f"{REPO_API}/git/commits/{MERGE}"] = {
            "sha": MERGE, "parents": [{"sha": BASE}], "tree": {"sha": "2" * 40}}
        fixture.reader.data[PULL].update(state="closed", merged=True, merge_commit_sha=MERGE,
                                         merged_by={"id": 33, "type": "Bot"})
        self.response = {"merged": True, "sha": MERGE}
        self.merged = verify_merged_commit(
            reader=fixture.reader, locked=self.locked, proposal=fixture.proposal,
            pr_link=fixture.link, merge_response=self.response)

    def plan(self):
        return plan_exact_sync(
            reader=self.fixture.reader, live_reader=self.fixture.live,
            locked=self.locked, proposal=self.fixture.proposal,
            pr_link=self.fixture.link, merge_response=self.response, now=NOW)

    def result_snapshot(self):
        value = deepcopy(self.fixture.live.value)
        app = value["application"]
        app["spec"]["source"]["targetRevision"] = MERGE
        app["status"]["sync"]["revision"] = MERGE
        app["status"]["operationState"]["syncResult"]["revision"] = MERGE
        deployment = value["deployment"]
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = (
            IMAGE_REPOSITORY + "@" + self.fixture.proposal.desired_digest)
        value["checked_at"] = NOW.isoformat()
        return value

    def verify_result(self, snapshot):
        return verify_sync_result(
            reader=self.fixture.reader, snapshot=snapshot, locked=self.locked,
            proposal=self.fixture.proposal, pr_link=self.fixture.link,
            merge_response=self.response, now=NOW)

    def test_plan_rechecks_merge_runtime_and_lock_without_writing(self):
        plan = self.plan()
        self.assertTrue(plan["sync_plan_ready"])
        self.assertFalse(plan["write_enabled"])
        self.assertEqual(plan["commands"][0][-1], MERGE)
        self.assertEqual(plan["commands"][1][4], MERGE)
        self.assertEqual(plan["lease_holder"], self.locked["lease"]["holder"])

    def test_plan_rejects_changed_main_runtime_or_lock(self):
        self.fixture.reader.data[MAIN]["object"]["sha"] = HEAD
        with self.assertRaisesRegex(ApprovalRejected, "merged_main_sha_mismatch"):
            self.plan()
        self.fixture.reader.data[MAIN]["object"]["sha"] = MERGE
        self.fixture.live.value["deployment"]["metadata"]["uid"] = "wrong"
        with self.assertRaisesRegex(ApprovalRejected, "runtime_target_changed"):
            self.plan()
        self.fixture.live.value["deployment"]["metadata"]["uid"] = self.fixture.proposal.target_uid
        self.fixture.live.value["lease"]["spec"]["holderIdentity"] = "ci-" + "a" * 32
        with self.assertRaisesRegex(ApprovalRejected, "shared_lock_lost"):
            self.plan()

    def test_result_is_revision_applied_not_service_resolved(self):
        result = self.verify_result(self.result_snapshot())
        self.assertTrue(result["sync_verified"])
        self.assertFalse(result["service_resolved"])
        self.assertEqual(result["merge_sha"], MERGE)

    def test_result_rejects_stale_wrong_revision_image_or_lost_lock(self):
        snapshot = self.result_snapshot()
        snapshot["checked_at"] = (NOW - timedelta(seconds=16)).isoformat()
        with self.assertRaisesRegex(ApprovalRejected, "sync_result_snapshot_stale"):
            self.verify_result(snapshot)
        snapshot = self.result_snapshot()
        snapshot["application"]["status"]["sync"]["revision"] = BASE
        with self.assertRaisesRegex(ApprovalRejected, "sync_result_argo_mismatch"):
            self.verify_result(snapshot)
        snapshot = self.result_snapshot()
        snapshot["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] = "other"
        with self.assertRaisesRegex(ApprovalRejected, "sync_result_deployment_mismatch"):
            self.verify_result(snapshot)
        snapshot = self.result_snapshot()
        snapshot["lease"]["spec"]["holderIdentity"] = ""
        with self.assertRaisesRegex(ApprovalRejected, "sync_result_lock_lost"):
            self.verify_result(snapshot)


if __name__ == "__main__":
    unittest.main()
