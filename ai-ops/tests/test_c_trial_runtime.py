"""실행 adapter가 실제 C-03 승인·병합·동기화 검증 함수를 호출하는지 확인한다."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_ops.c_trial_execution import C4TrialExecutor, TrialPlan


class TrialRuntimeTest(unittest.TestCase):
    def setUp(self):
        from test_c_failure_handoff_runtime import FailureHandoffRuntimeTest

        self.fixture = FailureHandoffRuntimeTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        fx = self.fixture
        record = fx.record
        self.plan = TrialPlan(trial_id=record.trial_id, application_uid=record.application_uid,
            deployment_uid=record.deployment_uid, lease_uid=record.lease_uid, worker_uid="worker", pvc_uid="pvc",
            normal_revision=record.normal_revision, failure_revision=record.failure_revision,
            seed_main=fx.fixture.link.pr_base_sha, workflow_main="d"*40, normal_image=record.normal_image,
            failure_image=record.failure_image, planned_at=fx.now, approval_cutoff=fx.now-timedelta(minutes=5),
            manual_recovery_at=fx.now+timedelta(seconds=180), rollback_deadline=fx.now+timedelta(seconds=300))
        self.merge_calls = 0
        self.closed = False
        self.lose_response = False
        self.argo_calls = []
        self.merge_sha = "e"*40

        @contextmanager
        def merge_session():
            try:
                yield SimpleNamespace(identity=fx.fixture.executor, merge=self.merge)
            finally:
                self.closed = True

        self.transport = SimpleNamespace(lease_command=fx.owner.command, snapshot=fx.read,
            merge_session=merge_session, argo=self.argo, wait_healthy=lambda *args: fx.read())
        self.executor = C4TrialExecutor(self.plan, self.transport, clock=lambda: fx.now)
        self.executor.lease = fx.owner
        self.executor.handoff = record
        self.executor.executor_holder = record.executor_holder
        self.executor.injected = True

    def merge(self, request):
        self.merge_calls += 1
        fx = self.fixture
        reader = fx.fixture.reader
        prefix = fx.repo_api
        reader.data[prefix+"/git/ref/heads/main"]["object"]["sha"] = self.merge_sha
        reader.data[prefix+"/git/commits/"+self.merge_sha] = {"sha": self.merge_sha,
            "parents": [{"sha": fx.fixture.link.pr_base_sha}], "tree": {"sha": "2"*40}}
        reader.data[prefix+"/pulls/123"].update(state="closed", merged=True,
            merge_commit_sha=self.merge_sha, merged_by={"id": fx.fixture.executor["actor_id"], "type": "Bot"})
        if self.lose_response:
            raise OSError("synthetic response loss")
        return {"merged": True, "sha": self.merge_sha}

    def argo(self, role, command):
        self.argo_calls.append((role, deepcopy(command)))
        value = self.fixture.fixture.live.value
        app = value["application"]
        app["spec"]["source"]["targetRevision"] = command[4]
        if command[1] == "sync":
            app["status"]["sync"]["revision"] = command[4]
            app["status"]["health"]["status"] = "Healthy"
            app["status"]["operationState"]["syncResult"]["revision"] = command[4]
            dep = value["deployment"]
            dep["spec"]["template"]["spec"]["containers"][0]["image"] = self.plan.normal_image
            dep["status"]["readyReplicas"] = 1

    def test_real_gates_merge_once_sync_confirmed_sha_then_release(self):
        from ai_ops.c_sync_guard import verify_sync_result

        def handoff(**args):
            clock = args.pop("clock")
            result = verify_sync_result(**args, now=clock())
            return SimpleNamespace(sync_verified=result["sync_verified"], merge_sha=result["merge_sha"])

        args = self.fixture.fixture.preflight_args()
        with patch("ai_ops.c_verification_handoff.verify_c03_handoff", side_effect=handoff):
            receipt = self.executor.recover(**args)
        self.assertTrue(receipt.sync_verified)
        self.assertEqual(receipt.merge_sha, self.merge_sha)
        self.assertEqual(self.merge_calls, 1)
        self.assertTrue(self.closed)
        self.assertEqual(self.argo_calls[0][1][4], self.merge_sha)
        self.assertFalse(self.fixture.server.value["spec"]["holderIdentity"])
        self.assertTrue(self.executor.finished)

    def test_review_rejection_precedes_credential_session_and_transfer(self):
        from ai_ops.c_approval import ApprovalRejected

        self.fixture.fixture.reader.data[self.fixture.review_path][0]["commit_id"] = "f"*40
        with self.assertRaises(ApprovalRejected):
            self.executor.recover(**self.fixture.fixture.preflight_args())
        self.assertFalse(self.closed)
        self.assertEqual(self.merge_calls, 0)
        self.assertEqual(self.fixture.server.value["spec"]["holderIdentity"], self.fixture.record.owner_holder)

    def test_lost_merge_response_is_not_retried_and_session_is_closed(self):
        self.lose_response = True
        with self.assertRaises(OSError):
            self.executor.recover(**self.fixture.fixture.preflight_args())
        self.assertEqual(self.merge_calls, 1)
        self.assertTrue(self.closed)
        self.assertEqual(self.argo_calls, [])
        self.assertEqual(self.fixture.server.value["spec"]["holderIdentity"], self.fixture.record.executor_holder)

    def test_manual_cutoff_stops_merge_before_credentials(self):
        self.fixture.now = self.plan.manual_recovery_at
        with self.assertRaises(ValueError):
            self.executor.recover(**self.fixture.fixture.preflight_args())
        self.assertEqual(self.merge_calls, 0)
        self.assertFalse(self.closed)

    def test_new_plan_rejects_missing_notice_before_merge_credentials(self):
        fx=self.fixture
        self.executor.plan=self.plan.model_copy(update={'schema_version':2,
            'manual_recovery_at':self.plan.planned_at+timedelta(seconds=240),
            'review_deadline':self.plan.planned_at+timedelta(seconds=210),'minimum_review_seconds':120})
        with patch('ai_ops.c_approval.verify_review',return_value={'head_sha':'e'*40,'approved_at':fx.now}),self.assertRaises(KeyError):
            self.executor.recover(**fx.fixture.preflight_args())
        self.assertEqual(self.merge_calls,0)
        self.assertFalse(self.closed)
