"""C-03 실행 직전 경계의 원본 변화와 실패 닫힘을 로컬 입력으로 검증한다."""

from copy import deepcopy
from datetime import timedelta
import json
import unittest

from ai_ops.b6_coordination import LabLease, PURPOSE
from ai_ops.c_approval import APPROVAL_READ_PATH, ApprovalGitHubReader, ApprovalRejected, REPO_API
from ai_ops.c_execution_preflight import (REQUIRED_CHECK, acquire_locked_preflight,
                                           verify_execution_preflight)
from ai_ops.c_pr_link import make_pr_link
from ai_ops.c_recovery import (IMAGE_REPOSITORY, ISOLATED_REPOSITORY, REPOSITORY,
                               RecoveryDraftStore, RecoveryProposal, _encoded_values)
from ai_ops.c_recovery_proof import proposal_identity
from ai_ops.contracts import sha256_json
import test_c_approval as approval_fixtures
from test_c_approval import AUTHOR, BASE, CURRENT, HEAD, MAIN, NOW, OPERATOR, PULL, REVIEWS, FixtureReader


PROTECTION = f"{REPO_API}/branches/main/protection"
CHECKS = f"{REPO_API}/commits/{HEAD}/check-runs?per_page=100"


class LiveReader:
    def __init__(self, value):
        self.value = value
        self.calls = 0
        self.change_second = None

    def read(self, *, expected_lease=None):
        self.calls += 1
        result = deepcopy(self.value)
        if self.calls == 2 and self.change_second:
            self.change_second(result)
        return result


class LeaseCommand:
    def __init__(self, live):
        self.live = live
        self.current = deepcopy(live.value["lease"])
        self.on_replace = None
        self.refresh_proof = True

    def __call__(self, *args, payload=None):
        if args[:3] == ("-n", "ai-ops-lab", "get"):
            return json.dumps(self.current)
        if args[0] == "replace":
            proposed = json.loads(payload)
            if proposed["metadata"]["resourceVersion"] != self.current["metadata"]["resourceVersion"]:
                raise ValueError("lease_compare_and_swap_conflict")
            proposed["metadata"]["resourceVersion"] = str(int(self.current["metadata"]["resourceVersion"]) + 1)
            self.current = proposed
            self.live.value["lease"] = deepcopy(proposed)
            if proposed["spec"].get("holderIdentity") and self.refresh_proof:
                self.live.value["checked_at"] = NOW.isoformat()
                self.live.value["proof"] = self.live.value["proof"] | {
                    "proof_checked_at": NOW.isoformat()}
                from ai_ops.c_recovery_proof import PROPOSAL_IDENTITY_FIELDS
                identity = {key: self.live.value["proof"][key] for key in PROPOSAL_IDENTITY_FIELDS}
                identity["proof_checked_at"] = NOW.isoformat()
                self.live.value["proof"]["proposal_readiness_hash"] = sha256_json(identity)
            if self.on_replace is not None:
                self.on_replace()
            return json.dumps(proposed)
        raise AssertionError(args)


class ExecutionPreflightTest(unittest.TestCase):
    def setUp(self):
        fixture = approval_fixtures.CApprovalTest("test_exact_review_returns_evidence_without_approving_or_executing")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        prior = fixture.link_proof(BASE, fixture.proposal.created_at - timedelta(seconds=1))
        body = fixture.proposal.model_dump(mode="json", exclude={"proposal_hash"})
        body["recheck_hash"] = prior["proposal_readiness_hash"]
        self.proposal = RecoveryProposal.model_validate({**body, "proposal_hash": sha256_json(body)})
        self.store = RecoveryDraftStore(fixture.store.root.parent / "execution")
        self.store.save(fixture.request, self.proposal)
        self.original = prior
        refreshed = fixture.link_proof(BASE, NOW - timedelta(seconds=1))
        self.link = make_pr_link(self.proposal, refreshed, BASE, "5" * 40,
                                 NOW - timedelta(minutes=4))
        self.reader = FixtureReader(self.proposal)
        self.reader.data[f"{REPO_API}/git/commits/{HEAD}"]["message"] = (
            f"실습 이미지 원복\n\nRecovery-Proposal-SHA256: {self.link.link_hash}")
        self.reader.data[PULL].update(mergeable=True, mergeable_state="clean")
        self.reader.data[PROTECTION] = {
            "required_pull_request_reviews": {"required_approving_review_count": 1,
                                              "dismiss_stale_reviews": True},
            "required_status_checks": {"strict": True, "contexts": [REQUIRED_CHECK]},
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        }
        self.reader.data[CHECKS] = {"total_count": 1, "check_runs": [{
            "id": 55, "name": REQUIRED_CHECK, "head_sha": HEAD,
            "status": "completed", "conclusion": "success", "app": {"slug": "github-actions"},
        }]}
        self.executor = {"kind": "github_app_installation", "repository": "f-lab-edu/F-Lab-DevOps",
                         "actor_id": 33, "app_id": 34, "installation_id": 35,
                         "permissions": {"contents": "write", "pull_requests": "write"}}
        app = {"metadata": {"name": "lab-target", "namespace": "argocd",
                            "uid": self.link.application_uid},
               "spec": {"project": "b6-lab", "source": {
                   "repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git",
                   "path": "ai-ops/lab", "targetRevision": BASE},
                   "destination": {"server": "https://kubernetes.default.svc",
                                   "namespace": "ai-ops-lab"}},
               "status": {"sync": {"status": "Synced", "revision": BASE},
                          "health": {"status": "Degraded"},
                          "operationState": {"phase": "Succeeded",
                                             "syncResult": {"revision": BASE}}}}
        deployment = {"metadata": {"name": "lab-api", "namespace": "ai-ops-lab",
                                   "uid": self.proposal.target_uid, "generation": 1},
                      "spec": {"replicas": 1, "template": {"spec": {"containers": [{
                          "name": "api", "image": IMAGE_REPOSITORY + "@" + CURRENT}]}}},
                      "status": {"observedGeneration": 1, "readyReplicas": 0}}
        lease = {"apiVersion": "coordination.k8s.io/v1", "kind": "Lease",
                 "metadata": {"name": "b6-scenario-lock", "namespace": "ai-ops-lab",
                              "uid": "lease-uid", "resourceVersion": "1", "labels": dict(PURPOSE)},
                 "spec": {}}
        self.live = LiveReader({"application": app, "deployment": deployment,
                                "lease": lease, "values_bytes": _encoded_values(CURRENT).encode(),
                                "proof": refreshed,
                                "checked_at": (NOW - timedelta(seconds=1)).isoformat()})

    def preflight_args(self):
        return dict(drafts=self.store, proposal_id=self.proposal.proposal_id, pull_number=123,
                    allowed_github_id=OPERATOR, pr_author_id=AUTHOR,
                    executor_identity=self.executor, original_recheck=self.original,
                    pr_link=self.link, live_reader=self.live, reader=self.reader, clock=lambda: NOW)

    def check(self, **changes):
        return verify_execution_preflight(**(self.preflight_args() | changes))

    def test_reader_routes_remain_read_only(self):
        self.assertTrue(APPROVAL_READ_PATH.fullmatch(PROTECTION))
        self.assertTrue(APPROVAL_READ_PATH.fullmatch(CHECKS))
        self.assertFalse(APPROVAL_READ_PATH.fullmatch(f"{REPO_API}/pulls/123/merge"))

    def test_fresh_review_gate_identity_and_runtime_are_only_readiness(self):
        result = self.check()
        self.assertTrue(result["preflight_ready"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["required_check_run_id"], 55)
        self.assertEqual(self.live.calls, 2)
        self.assertEqual(self.store.status(self.proposal.proposal_id, OPERATOR, now=NOW)["state"], "draft")

    def test_isolated_preflight_binds_proof_pr_executor_and_argo_to_same_repository(self):
        repo = ISOLATED_REPOSITORY
        for record in (self.original, self.live.value["proof"]):
            record["source_repository"] = repo
            record["proposal_readiness_hash"] = sha256_json({
                **proposal_identity(record), "proof_checked_at": record["proof_checked_at"]})
        body = self.proposal.model_dump(mode="json", exclude={"proposal_hash"})
        body.update(repository=repo, recheck_hash=self.original["proposal_readiness_hash"])
        self.proposal = RecoveryProposal.model_validate({**body, "proposal_hash": sha256_json(body)})
        self.store = RecoveryDraftStore(self.store.root.parent / "isolated")
        self.store.save(self.fixture.request, self.proposal)
        self.link = make_pr_link(self.proposal, self.live.value["proof"], BASE, "5" * 40,
                                 NOW - timedelta(minutes=4))
        self.reader.data[PULL]["base"]["repo"]["full_name"] = repo
        self.reader.data[PULL]["head"]["repo"]["full_name"] = repo
        self.reader.data[f"{REPO_API}/git/commits/{HEAD}"]["message"] = (
            f"실습 이미지 원복\n\nRecovery-Proposal-SHA256: {self.link.link_hash}")
        self.reader.data = {path.replace(REPO_API, f"repos/{repo}"): value
                            for path, value in self.reader.data.items()}
        self.executor["repository"] = repo
        self.executor["permissions"]["pull_requests"] = "read"
        source = self.live.value["application"]["spec"]["source"]
        source["repoURL"] = f"https://github.com/{repo}.git"
        self.assertTrue(self.check()["preflight_ready"])
        self.assertTrue(all(path == "user" or path.startswith(f"repos/{repo}/")
                            for path in self.reader.calls))
        with self.assertRaisesRegex(ApprovalRejected, "executor_identity_not_separate"):
            self.check(executor_identity={**self.executor, "repository": REPOSITORY})
        source["repoURL"] = f"https://github.com/{REPOSITORY}.git"
        with self.assertRaisesRegex(ApprovalRejected, "live_snapshot_unavailable_or_invalid"):
            self.check()
        with self.assertRaisesRegex(ApprovalRejected, "github_read_path_rejected"):
            ApprovalGitHubReader(repository=repo).get(PROTECTION)

    def test_changed_main_or_review_is_rejected(self):
        self.reader.data[MAIN]["object"]["sha"] = "f" * 40
        with self.assertRaisesRegex(ApprovalRejected, "base_sha_changed"):
            self.check()
        self.reader.data[MAIN]["object"]["sha"] = BASE
        self.reader.data[REVIEWS][0]["commit_id"] = BASE
        with self.assertRaisesRegex(ApprovalRejected, "review_commit_mismatch"):
            self.check()

    def test_long_collection_passes_only_with_fresh_final_proof(self):
        moment = [NOW]
        def delayed_read(*, expected_lease=None):
            moment[0] = NOW + timedelta(seconds=20)
            self.live.value["checked_at"] = moment[0].isoformat()
            self.live.value["proof"] = self.fixture.link_proof(BASE, moment[0])
            return self.live.read(expected_lease=expected_lease)
        class DelayedReader:
            read = staticmethod(delayed_read)
        result = self.check(live_reader=DelayedReader(), clock=lambda: moment[0])
        self.assertTrue(result["preflight_ready"])
        self.assertEqual(result["checked_at"], moment[0])

    def test_required_gate_must_be_strict_successful_and_complete(self):
        self.reader.data[PROTECTION]["required_status_checks"]["strict"] = False
        with self.assertRaisesRegex(ApprovalRejected, "required_check_protection_missing"):
            self.check()
        self.reader.data[PROTECTION]["required_status_checks"]["strict"] = True
        self.reader.data[CHECKS]["check_runs"][0]["conclusion"] = "skipped"
        with self.assertRaisesRegex(ApprovalRejected, "required_check_not_successful"):
            self.check()
        self.reader.data[CHECKS]["check_runs"][0]["conclusion"] = "success"
        self.reader.data[CHECKS]["total_count"] = 100
        with self.assertRaisesRegex(ApprovalRejected, "check_runs_incomplete"):
            self.check()

    def test_recovered_changed_target_or_busy_lock_is_rejected(self):
        app = self.live.value["application"]
        deployment = self.live.value["deployment"]
        lease = self.live.value["lease"]
        deployment["status"]["readyReplicas"] = 1
        app["status"]["health"]["status"] = "Healthy"
        with self.assertRaisesRegex(ApprovalRejected, "runtime_not_failed_and_unlocked"):
            self.check()
        deployment["status"]["readyReplicas"] = 0
        app["status"]["health"]["status"] = "Degraded"
        deployment["metadata"]["uid"] = "c" * 36
        with self.assertRaisesRegex(ApprovalRejected, "runtime_target_changed"):
            self.check()
        deployment["metadata"]["uid"] = self.proposal.target_uid
        lease["spec"]["holderIdentity"] = "ci-" + "a" * 32
        with self.assertRaisesRegex(ApprovalRejected, "runtime_not_failed_and_unlocked"):
            self.check()

    def test_stale_or_changed_live_proof_and_second_read_are_rejected(self):
        self.live.value["checked_at"] = (NOW - timedelta(seconds=16)).isoformat()
        with self.assertRaisesRegex(ApprovalRejected, "live_snapshot_stale"):
            self.check()
        self.live.value["checked_at"] = (NOW - timedelta(seconds=1)).isoformat()
        self.live.value["proof"]["current_digest"] = self.proposal.desired_digest
        with self.assertRaisesRegex(ApprovalRejected, "live_proof_changed_or_stale"):
            self.check()
        self.live.value["proof"] = self.fixture.link_proof(BASE, NOW - timedelta(seconds=1))
        self.live.calls = 0
        self.live.change_second = lambda value: value["lease"]["metadata"].update(resourceVersion="2")
        with self.assertRaisesRegex(ApprovalRejected, "live_state_changed_during_preflight"):
            self.check()

    def test_mergeability_bypass_and_cooldown_are_rejected(self):
        self.reader.data[PULL]["mergeable_state"] = "blocked"
        with self.assertRaisesRegex(ApprovalRejected, "pull_request_not_mergeable"):
            self.check()
        self.reader.data[PULL]["mergeable_state"] = "clean"
        self.reader.data[PROTECTION]["required_pull_request_reviews"]["bypass_pull_request_allowances"] = {
            "apps": [{"id": self.executor["app_id"]}]}
        with self.assertRaisesRegex(ApprovalRejected, "review_bypass_allowed"):
            self.check()
        self.reader.data[PROTECTION]["required_pull_request_reviews"].pop("bypass_pull_request_allowances")
        self.live.value["lease"]["metadata"]["annotations"] = {
            "ai-ops/b6-not-before": (NOW + timedelta(minutes=1)).isoformat()}
        with self.assertRaisesRegex(ApprovalRejected, "lab_cooldown_active"):
            self.check()

    def test_gate_change_during_read_is_rejected(self):
        reads = [0]
        def change_gate():
            reads[0] += 1
            if reads[0] == 2:
                self.reader.data[CHECKS]["check_runs"][0]["conclusion"] = "failure"
        self.reader.hooks[CHECKS] = change_gate
        with self.assertRaisesRegex(ApprovalRejected, "github_gate_changed_during_preflight"):
            self.check()

    def test_shared_lease_is_acquired_then_reviewed_again(self):
        command = LeaseCommand(self.live)
        lease = LabLease(command, "c3-" + "a" * 32, clock=lambda: NOW)
        result = acquire_locked_preflight(lease=lease, **self.preflight_args())
        self.assertTrue(result["locked_preflight_ready"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["reason"], "executor_write_adapter_required")
        self.assertEqual(result["lease"]["uid"], "lease-uid")
        self.assertEqual(command.current["spec"]["holderIdentity"], lease.holder)
        self.assertEqual(self.live.calls, 4)
        self.assertTrue(lease.release(recovery_verified=True, scenario_started=False)["released"])

    def test_old_proof_after_lock_is_rejected(self):
        command = LeaseCommand(self.live)
        command.refresh_proof = False
        lease = LabLease(command, "c3-" + "f" * 32, clock=lambda: NOW)
        with self.assertRaisesRegex(ApprovalRejected, "locked_preflight_failed_lock_retained"):
            acquire_locked_preflight(lease=lease, **self.preflight_args())
        self.assertEqual(command.current["spec"]["holderIdentity"], lease.holder)

    def test_revoked_review_after_lock_keeps_lock_for_inspection(self):
        command = LeaseCommand(self.live)
        command.on_replace = lambda: self.reader.data[REVIEWS][0].update(state="DISMISSED")
        lease = LabLease(command, "c3-" + "b" * 32, clock=lambda: NOW)
        with self.assertRaisesRegex(ApprovalRejected, "locked_preflight_failed_lock_retained"):
            acquire_locked_preflight(lease=lease, **self.preflight_args())
        self.assertEqual(command.current["spec"]["holderIdentity"], lease.holder)

    def test_target_change_after_lock_keeps_lock_for_inspection(self):
        command = LeaseCommand(self.live)
        command.on_replace = lambda: self.live.value["deployment"]["metadata"].update(uid="c" * 36)
        lease = LabLease(command, "c3-" + "c" * 32, clock=lambda: NOW)
        with self.assertRaisesRegex(ApprovalRejected, "locked_preflight_failed_lock_retained"):
            acquire_locked_preflight(lease=lease, **self.preflight_args())
        self.assertEqual(command.current["spec"]["holderIdentity"], lease.holder)

    def test_competing_lease_rejects_before_own_acquisition(self):
        command = LeaseCommand(self.live)
        command.current["spec"]["holderIdentity"] = "ci-" + "d" * 32
        lease = LabLease(command, "c3-" + "e" * 32, clock=lambda: NOW)
        with self.assertRaisesRegex(ApprovalRejected, "shared_lock_acquire_unconfirmed_inspect_lease"):
            acquire_locked_preflight(lease=lease, **self.preflight_args())
        self.assertIsNone(lease.uid)
        self.assertEqual(command.current["spec"]["holderIdentity"], "ci-" + "d" * 32)

    def test_executor_must_be_distinct_and_scoped(self):
        self.executor["actor_id"] = AUTHOR
        with self.assertRaisesRegex(ApprovalRejected, "executor_identity_not_separate"):
            self.check()
        self.executor["actor_id"] = 33
        self.executor["permissions"]["contents"] = "read"
        with self.assertRaisesRegex(ApprovalRejected, "executor_identity_not_separate"):
            self.check()


if __name__ == "__main__":
    unittest.main()
