"""실제 adapter의 주입·조사·복귀 순서와 실행별 차단을 대역으로 검증한다."""

from copy import deepcopy
from datetime import timedelta
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_ops.c_trial_execution import C4TrialExecutor, TrialAuthorization, TrialPlan
from ai_ops.c_trial_source import TrialSource
import test_c_failure_handoff as handoff_fixtures
from test_c2_active_capture import BAD, NOW, REVISION
from test_c2_live_job import worker_deployment


class Transport:
    def __init__(self, fixture, plan):
        self.fixture = fixture
        self.clock = lambda: fixture.at
        self.plan = plan
        self.lease_command = fixture.server.command
        self.calls = []
        self.images = True
        self.guards = True
        self.fail_command = False
        self.wait_until = Mock()
        self.before_wait = None
        app = fixture.snapshot["application"]
        app["spec"]["source"]["targetRevision"] = plan.normal_revision
        app["status"].update(sync={"status": "Synced", "revision": plan.normal_revision},
                             health={"status": "Healthy"})
        app["status"]["operationState"].update(phase="Succeeded",
                                              syncResult={"revision": plan.normal_revision})
        fixture.snapshot["deployment"]["status"]["readyReplicas"] = 1
        fixture.snapshot["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] = plan.normal_image
        fixture.server.value["spec"] = {}

    def snapshot(self):
        return self.fixture.read()

    def require_images(self, plan):
        if not self.images:
            raise ValueError("image evidence missing")

    def require_guards(self, plan):
        if not self.guards:
            raise ValueError("guards missing")

    def argo(self, role, command):
        self.calls.append((role, deepcopy(command)))
        if self.fail_command:
            raise OSError("response lost")
        app = self.fixture.snapshot["application"]
        app["spec"]["source"]["targetRevision"] = command[4]
        if command[1] == "sync":
            failed = command[4] == self.plan.failure_revision
            image = self.plan.failure_image if failed else self.plan.normal_image
            app["status"].update(sync={"status": "Synced", "revision": command[4]},
                                  health={"status": "Degraded" if failed else "Healthy"})
            app["status"]["operationState"].update(phase="Succeeded",
                startedAt=NOW.isoformat(), operation={"sync": {"revision": command[4]}},
                syncResult={"revision": command[4]})
            dep = self.fixture.snapshot["deployment"]
            dep["spec"]["template"]["spec"]["containers"][0]["image"] = image
            dep["status"]["readyReplicas"] = 0 if failed else 1
            pod = self.fixture.snapshot["pods"][0]
            pod["metadata"]["creationTimestamp"] = NOW.isoformat()
            pod["spec"]["containers"][0]["image"] = image
            pod["status"]["containerStatuses"][0].update(image=image, ready=not failed)

    def wait_failure(self, plan):
        return self.snapshot()

    def wait_healthy(self, revision, image):
        if self.before_wait:
            self.before_wait()
        return self.snapshot()

    def get(self, key):
        if key == "worker":
            worker = worker_deployment()
            worker["metadata"]["uid"] = "worker"
            return worker
        if key == "pvc":
            return {"metadata": {"uid": "pvc"}}
        raise AssertionError(key)

    def create_capture_job(self, job):
        self.job = job
        return {"name": job["metadata"]["name"], "uid": "job-uid"}


class TrialExecutionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = handoff_fixtures.FailureHandoffTest()
        self.fixture.setUp()
        self.plan = TrialPlan(trial_id=789, application_uid="app-uid", deployment_uid="dep",
            lease_uid=self.fixture.server.value["metadata"]["uid"], worker_uid="worker", pvc_uid="pvc",
            normal_revision="a"*40, failure_revision=REVISION, seed_main="c"*40, workflow_main="d"*40,
            normal_image=BAD.replace("0"*64, "1"*64), failure_image=BAD, planned_at=NOW,
            approval_cutoff=NOW-timedelta(seconds=300), manual_recovery_at=NOW+timedelta(seconds=180),
            rollback_deadline=NOW+timedelta(seconds=300))
        self.authorization = TrialAuthorization(trial_id=789, plan_hash=self.plan.digest(),
            actor_github_id=149856960, approved_at=NOW-timedelta(seconds=301), fault_injection_approved=True)
        self.transport = Transport(self.fixture, self.plan)
        self.executor = C4TrialExecutor(self.plan, self.transport, clock=lambda: self.fixture.at)
        self.fixture.at = NOW-timedelta(seconds=301)
        self.executor.prepare(self.authorization)
        self.fixture.at = NOW

    def test_missing_or_late_preparation_never_acquires_lock(self):
        self.executor.prepared_at = None
        with self.assertRaises(ValueError):
            self.executor.inject(self.authorization)
        self.fixture.at = self.plan.approval_cutoff
        with self.assertRaises(ValueError):
            self.executor.prepare(self.authorization)
        self.assertFalse(self.fixture.server.value['spec'].get('holderIdentity'))
        self.assertEqual(self.transport.calls, [])

    def test_preparation_that_crosses_cutoff_is_not_latched(self):
        self.executor.prepared_at = None
        self.fixture.at = self.plan.approval_cutoff-timedelta(seconds=1)
        original = self.transport.snapshot
        def slow_snapshot():
            value = original()
            self.fixture.at += timedelta(seconds=2)
            return value
        self.transport.snapshot = slow_snapshot
        with self.assertRaises(ValueError):
            self.executor.prepare(self.authorization)
        self.assertIsNone(self.executor.prepared_at)
        self.assertEqual(self.transport.calls, [])

    def test_approved_injection_keeps_owner_then_creates_bound_capture_job(self):
        record = self.executor.inject(self.authorization)
        self.assertEqual(record.trial_id, 789)
        self.assertEqual(self.fixture.server.value["spec"]["holderIdentity"], self.executor.lease.holder)
        self.assertEqual(self.transport.calls[1][1][-1], "--async")
        job = self.executor.investigate()
        self.assertEqual(job["name"], "c2-source-capture-789")
        self.assertEqual(self.transport.job["metadata"]["annotations"]["ai-ops/failure-handoff-sha256"],
                         record.digest())

    def test_scheduled_wait_occurs_after_lock_acquisition_before_final_checks(self):
        self.fixture.at = NOW-timedelta(seconds=20)

        def scheduled_wait(moment):
            self.assertEqual(self.executor.lease.uid, self.plan.lease_uid)
            self.assertEqual(self.fixture.server.value["spec"]["holderIdentity"], self.executor.lease.holder)
            self.fixture.at = moment

        self.transport.wait_until.side_effect = scheduled_wait
        record = self.executor.inject(self.authorization)
        self.assertEqual(record.injected_at, NOW)
        self.assertLess(record.lease_acquired_at, NOW)

    def test_old_approval_or_wrong_plan_never_injects(self):
        for change in ({"trial_id": 123}, {"plan_hash": "f"*64},
                       {"approved_at": NOW}, {"approved_at": NOW+timedelta(seconds=1)}):
            authorization = self.authorization.model_copy(update=change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.executor.inject(authorization)
        self.assertEqual(self.transport.calls, [])

    def test_numeric_or_forged_false_approval_is_rejected(self):
        data = self.authorization.model_dump()
        data["fault_injection_approved"] = 1
        with self.assertRaises(ValueError):
            TrialAuthorization.model_validate(data)
        with self.assertRaises(ValueError):
            self.executor.inject(self.authorization.model_copy(update={"fault_injection_approved": False}))
        self.assertEqual(self.transport.calls, [])

    def test_normal_unstarted_owner_can_cancel_without_cooldown(self):
        self.executor.lease.acquire()
        self.executor.cancel_before_injection()
        self.assertFalse(self.fixture.server.value["spec"]["holderIdentity"])
        self.assertTrue(self.executor.finished)
        self.assertEqual(self.transport.calls, [])

    def test_guards_and_images_are_required_before_lock_or_write(self):
        for field in ("guards", "images"):
            setattr(self.transport, field, False)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.executor.inject(self.authorization)
            setattr(self.transport, field, True)
        self.assertFalse(self.fixture.server.value["spec"].get("holderIdentity"))
        self.assertEqual(self.transport.calls, [])

    def test_wrong_uid_prevents_injection(self):
        self.fixture.snapshot["deployment"]["metadata"]["uid"] = "other"
        with self.assertRaises(ValueError):
            self.executor.inject(self.authorization)
        self.assertEqual(self.transport.calls, [])

    def test_late_start_is_rejected_without_rescheduling(self):
        self.fixture.at = NOW+timedelta(seconds=6)
        with self.assertRaises(ValueError):
            self.executor.inject(self.authorization)
        self.assertEqual(self.transport.calls, [])

    def test_lost_first_write_response_cannot_repeat_injection(self):
        self.transport.fail_command = True
        with self.assertRaises(OSError):
            self.executor.inject(self.authorization)
        with self.assertRaises(ValueError):
            self.executor.inject(self.authorization)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertTrue(self.fixture.server.value["spec"]["holderIdentity"])

    def test_failed_ownership_or_changed_worker_blocks_investigation(self):
        self.executor.inject(self.authorization)
        self.executor.plan = self.plan.model_copy(update={"worker_uid": "other"})
        with self.assertRaises(ValueError):
            self.executor.investigate()
        self.executor.plan = self.plan
        self.fixture.server.value["spec"]["holderIdentity"] = "manual-"+"9"*32
        with self.assertRaises(ValueError):
            self.executor.investigate()

    def test_same_owner_can_restore_and_release_after_verified_normal(self):
        self.executor.inject(self.authorization)
        self.executor.restore()
        self.assertTrue(self.executor.finished)
        self.assertFalse(self.fixture.server.value["spec"]["holderIdentity"])
        self.assertEqual(self.transport.calls[-1][0], "rollback")
        self.assertEqual(self.transport.calls[-1][1][4], self.plan.normal_revision)

    def resume_module(self):
        scripts = Path(__file__).resolve().parents[1]/"scripts"
        with patch.object(sys, "path", [str(scripts), *sys.path]):
            spec = importlib.util.spec_from_file_location("restore_c04_test", scripts/"restore_c4_trial.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def owner_record(self):
        return {"trial_id": self.plan.trial_id, "plan_hash": self.plan.digest(), "pid": 999999,
            "lease_uid": self.plan.lease_uid, "injection_holder": self.executor.lease.holder,
            "executor_holder": self.executor.executor_holder}

    def test_alive_original_process_blocks_manual_resume(self):
        self.executor.inject(self.authorization)
        writes = len(self.transport.calls)
        with patch("os.kill", return_value=None), self.assertRaises(ValueError):
            self.resume_module().resume(self.plan, self.authorization, self.owner_record(), self.transport)
        self.assertEqual(len(self.transport.calls), writes)

    def test_dead_exact_owner_can_resume_rollback_without_changing_holder(self):
        self.executor.inject(self.authorization)
        with patch("os.kill", side_effect=ProcessLookupError):
            result = self.resume_module().resume(self.plan, self.authorization, self.owner_record(),
                                                 self.transport, handoff=self.executor.handoff)
        self.assertEqual(result["status"], "verified_owned_rollback")
        self.assertFalse(self.fixture.server.value["spec"]["holderIdentity"])

    def test_dead_process_does_not_allow_unknown_revision_rollback(self):
        self.executor.inject(self.authorization)
        writes = len(self.transport.calls)
        for revision in ("f"*40, None):
            self.fixture.snapshot["application"]["spec"]["source"]["targetRevision"] = revision
            with self.subTest(revision=revision), patch("os.kill", side_effect=ProcessLookupError), self.assertRaises(ValueError):
                self.resume_module().resume(self.plan, self.authorization, self.owner_record(), self.transport)
        self.assertEqual(len(self.transport.calls), writes)

    def test_foreign_lease_or_new_uid_blocks_restore_without_writes(self):
        self.executor.inject(self.authorization)
        writes = len(self.transport.calls)
        self.fixture.server.value["spec"]["holderIdentity"] = "manual-"+"9"*32
        with self.assertRaises(ValueError):
            self.executor.restore()
        self.assertEqual(len(self.transport.calls), writes)

    def test_normal_verification_failure_keeps_lock(self):
        self.executor.inject(self.authorization)
        self.transport.before_wait = lambda: self.fixture.snapshot["deployment"]["status"].update(readyReplicas=0)
        with self.assertRaises(ValueError):
            self.executor.restore()
        self.assertTrue(self.fixture.server.value["spec"]["holderIdentity"])

    def test_readiness_query_does_not_replace_real_c03_source(self):
        record = self.executor.inject(self.authorization)
        source = TrialSource(SimpleNamespace(), record, self.transport, clock=lambda: NOW,
            git_reader=Mock(),history_reader=Mock())
        source.audit._collect = Mock(return_value=({"incident_ready": True}, None, None, b"",
            {key: self.transport.snapshot()[key] for key in ("application", "deployment", "lease")}))
        result, _ = source.collect()
        self.assertTrue(result["incident_ready"])
        self.assertEqual(source.audit._collect.call_args.kwargs["expected_lease_holder"], record.owner_holder)
        self.fixture.server.value["spec"]["holderIdentity"] = "manual-"+"9"*32
        with self.assertRaises(ValueError):
            source.collect()
        self.assertEqual(source.audit._collect.call_count, 1)


if __name__ == "__main__":
    unittest.main()
