"""보존 C-03 실행 묶음에서 실제 승인·CI·병합 계획 경계를 연결한다."""

from copy import deepcopy
from datetime import timedelta
import unittest

from ai_ops.b6_coordination import LabLease
from ai_ops.c2_active_capture import ISOLATED_REPOSITORY
from ai_ops.c_failure_handoff import (FailureHandoffReader, acquire_failure_preflight,
    assess_failure_handoff_proof, offer_failure_handoff, plan_failure_merge)
from ai_ops.contracts import _utc_datetime, sha256_json
from test_b6_gitops import LeaseServer
from test_c2_active_capture import fixture as failure_fixture


class FailureHandoffRuntimeTest(unittest.TestCase):
    def setUp(self):
        from test_c_execution_preflight import ExecutionPreflightTest
        from test_c_approval import BASE, HEAD, NOW, PULL, REPO_API
        from ai_ops.c_pr_link import make_pr_link
        from ai_ops.c_recovery import RecoveryDraftStore, RecoveryProposal
        from ai_ops.c_recovery_proof import proposal_identity

        self.now = NOW
        self.base = BASE
        self.repo_api = "repos/joungGo/c03-execution-lab"
        self.review_path = self.repo_api+"/pulls/123/reviews?per_page=100"
        self.fixture = fx = ExecutionPreflightTest("test_fresh_review_gate_identity_and_runtime_are_only_readiness")
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        for proof in (fx.original, fx.live.value["proof"]):
            proof["source_repository"] = "joungGo/c03-execution-lab"
            proof["proposal_readiness_hash"] = sha256_json({
                **proposal_identity(proof), "proof_checked_at": proof["proof_checked_at"]})
        body = fx.proposal.model_dump(mode="json", exclude={"proposal_hash"})
        body.update(repository="joungGo/c03-execution-lab",
                    recheck_hash=fx.original["proposal_readiness_hash"])
        fx.proposal = RecoveryProposal.model_validate({**body, "proposal_hash": sha256_json(body)})
        fx.store = RecoveryDraftStore(fx.store.root.parent/"failure-handoff")
        fx.store.save(fx.fixture.request, fx.proposal)
        fx.link = make_pr_link(fx.proposal, fx.live.value["proof"], BASE, "5"*40,
                              NOW-timedelta(minutes=4))
        fx.reader.data[PULL]["base"]["repo"]["full_name"] = "joungGo/c03-execution-lab"
        fx.reader.data[PULL]["head"]["repo"]["full_name"] = "joungGo/c03-execution-lab"
        fx.reader.data[f"{REPO_API}/git/commits/{HEAD}"]["message"] = (
            f"실습 이미지 원복\n\nRecovery-Proposal-SHA256: {fx.link.link_hash}")
        fx.reader.data = {key.replace(REPO_API, self.repo_api): value for key, value in fx.reader.data.items()}
        fx.executor.update(repository="joungGo/c03-execution-lab",
                           permissions={"contents": "write", "pull_requests": "read"})
        app = fx.live.value["application"]
        app["spec"]["source"]["repoURL"] = ISOLATED_REPOSITORY
        started = (NOW-timedelta(seconds=30)).isoformat()
        app["status"]["operationState"].update(startedAt=started,
            operation={"sync": {"revision": BASE}})
        _, _, self.rs, self.pods, _ = failure_fixture()
        self.rs["metadata"]["ownerReferences"][0]["uid"] = fx.proposal.target_uid
        image = fx.live.value["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"]
        self.pods[0]["metadata"]["creationTimestamp"] = started
        self.pods[0]["metadata"]["uid"] = "failed-pod"
        self.pods[0]["spec"]["containers"][0]["image"] = image
        self.pods[0]["status"]["containerStatuses"][0]["image"] = image
        self.server = LeaseServer()
        self.server.value = deepcopy(fx.live.value["lease"])

        def command(*args, **kwargs):
            result = self.server.command(*args, **kwargs)
            fx.live.value["lease"] = deepcopy(self.server.value)
            if args[0] == "replace":
                proof = fx.fixture.link_proof(BASE, self.now)
                proof["source_repository"] = "joungGo/c03-execution-lab"
                proof["proposal_readiness_hash"] = sha256_json({
                    **proposal_identity(proof), "proof_checked_at": proof["proof_checked_at"]})
                fx.live.value.update(checked_at=self.now.isoformat(), proof=proof)
            return result

        self.now -= timedelta(seconds=60)
        self.owner = LabLease(command, "manual-"+"1"*32, clock=lambda: self.now)
        self.owner.acquire()
        self.now = NOW
        self.record = offer_failure_handoff(self.owner, read_failure=self.read,
            trial_id=123, executor_holder="c3-"+"2"*32, normal_revision="9"*40,
            normal_image=image.split("@")[0]+"@"+fx.proposal.desired_digest,
            failure_revision=BASE, failure_image=image, application_uid=fx.link.application_uid,
            deployment_uid=fx.proposal.target_uid, injected_at=NOW-timedelta(seconds=40),
            rollback_deadline=NOW+timedelta(seconds=260))
        self.bound = FailureHandoffReader(self.record, fx.live, self.read, clock=lambda: self.now)

    def read(self):
        return {**deepcopy(self.fixture.live.value), "replica_set": deepcopy(self.rs),
                "pods": deepcopy(self.pods), "checked_at": self.now.isoformat()}

    def preflight(self):
        args = self.fixture.preflight_args()
        args["live_reader"] = self.bound
        return acquire_failure_preflight(owner=self.owner, handoff=self.record,
                                         read_failure=self.read, **args)

    def test_c02_held_failure_reaches_real_c03_review_ci_and_exact_head_plan(self):
        locked, recipient = self.preflight()
        self.assertTrue(locked["locked_preflight_ready"])
        self.assertFalse(locked["execution_allowed"])
        plan = plan_failure_merge(handoff=self.record, live_reader=self.bound,
            locked=locked, proposal=self.fixture.proposal, pr_link=self.fixture.link)
        self.assertEqual(plan["body"]["sha"], locked["head_sha"])
        self.assertFalse(plan["body"]["bypass_rules"])
        self.assertFalse(plan["write_enabled"])
        self.assertEqual(recipient.uid, self.record.lease_uid)

    def test_donor_proof_still_requires_normal_ci_and_ecr_evidence(self):
        from test_c_recovery_proof import RecoveryProofTest

        proof = RecoveryProofTest("test_proof_can_be_ready_but_cannot_approve_or_create_proposal")
        proof.setUp()
        at = _utc_datetime(proof.pods[0]["metadata"]["creationTimestamp"])+timedelta(seconds=30)
        server = LeaseServer()
        owner = LabLease(server.command, "manual-"+"4"*32, clock=lambda: at-timedelta(seconds=60))
        owner.acquire()
        owner.clock = lambda: at
        proof.app["spec"]["source"]["repoURL"] = ISOLATED_REPOSITORY
        proof.app["status"]["operationState"]["operation"] = {"sync": {"revision": "b"*40}}
        snapshot = lambda: {"application": proof.app, "deployment": proof.deployment,
            "replica_set": proof.rs, "pods": proof.pods, "values_bytes": proof.values,
            "lease": deepcopy(server.value), "checked_at": at.isoformat()}
        record = offer_failure_handoff(owner, read_failure=snapshot,
            trial_id=456, executor_holder="c3-"+"5"*32, normal_revision="a"*40,
            normal_image=proof.binding.app.image, failure_revision="b"*40, failure_image=proof.image,
            application_uid=proof.app["metadata"]["uid"], deployment_uid=proof.deployment["metadata"]["uid"],
            injected_at=at-timedelta(seconds=40), rollback_deadline=at+timedelta(seconds=260))
        args = dict(binding=proof.binding, history=proof.history,
                    ecr_response=proof.ecr, bad_digest_absent=True)
        result = assess_failure_handoff_proof(record, snapshot(), now=at, **args)
        self.assertTrue(result["proof_ready"])
        self.assertFalse(result["proposal_allowed"])
        self.assertFalse(result["cluster_or_github_write_performed"])
        args["ecr_response"] = {"imageDetails": []}
        rejected = assess_failure_handoff_proof(record, snapshot(), now=at, **args)
        self.assertFalse(rejected["proof_ready"])
        self.assertIn("prior_digest_not_found_in_ecr", rejected["blockers"])

    def test_missing_or_wrong_head_review_is_still_rejected_with_lock_retained(self):
        from ai_ops.c_approval import ApprovalRejected

        self.fixture.reader.data[self.review_path][0]["commit_id"] = "f"*40
        with self.assertRaises(ApprovalRejected):
            self.preflight()
        self.assertEqual(self.server.value["spec"]["holderIdentity"], self.record.executor_holder)

    def test_failed_required_ci_is_still_rejected(self):
        from ai_ops.c_approval import ApprovalRejected
        from test_c_approval import HEAD

        path = self.repo_api+f"/commits/{HEAD}/check-runs?per_page=100"
        self.fixture.reader.data[path]["check_runs"][0]["conclusion"] = "failure"
        with self.assertRaises(ApprovalRejected):
            self.preflight()
        self.assertEqual(self.server.value["spec"]["holderIdentity"], self.record.executor_holder)

    def test_changed_pr_target_is_rejected_before_transfer(self):
        self.record = self.record.model_copy(update={"deployment_uid": "other"})
        self.bound.handoff = self.record
        with self.assertRaises(ValueError):
            self.preflight()
        self.assertEqual(self.server.value["spec"]["holderIdentity"], self.record.owner_holder)

    def test_expired_handoff_cannot_create_merge_plan(self):
        locked, _ = self.preflight()
        self.now = self.record.rollback_deadline
        with self.assertRaises(ValueError):
            plan_failure_merge(handoff=self.record, live_reader=self.bound,
                locked=locked, proposal=self.fixture.proposal, pr_link=self.fixture.link)
        self.assertEqual(self.server.value["spec"]["holderIdentity"], self.record.executor_holder)

    def test_expiry_during_live_recheck_stops_preflight(self):
        original = self.fixture.live.read

        def delayed(**kwargs):
            result = original(**kwargs)
            self.now = self.record.rollback_deadline
            return result

        self.fixture.live.read = delayed
        with self.assertRaises(ValueError):
            self.preflight()
        self.assertEqual(self.server.value["spec"]["holderIdentity"], self.record.executor_holder)


if __name__ == "__main__":
    unittest.main()
