"""R4/R7의 실제 병합·동기화 경계와 디스크 재시작 연결을 모의 검증한다."""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.c_execution_control import ExecutionControl, ExecutionCancelled, verified_resume_merge


class ExecutionControlTest(unittest.TestCase):
    def setUp(self):
        from test_c_trial_runtime import TrialRuntimeTest
        self.fixture = f = TrialRuntimeTest()
        f.setUp(); self.addCleanup(f.doCleanups)
        f.fixture.fixture.executor['actor_id'] = 354870499
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = patch.dict(os.environ, {'AI_OPS_FILES_ROOT': str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.owner = {'trial_id': f.plan.trial_id, 'plan_hash': f.plan.digest(), 'pid': 999999,
            'lease_uid': f.plan.lease_uid, 'injection_holder': f.executor.lease.holder,
            'executor_holder': f.executor.executor_holder}
        self.control = f.executor.control = ExecutionControl(self.root, f.plan, self.owner,
                                                            clock=lambda: f.fixture.now)

    def cancel(self):
        value = {'trial_id': self.fixture.plan.trial_id, 'plan_hash': self.fixture.plan.digest(),
                 'actor_github_id': 149856960, 'cancel_execution': True}
        path = self.root/'cancel-execution.json'
        path.write_text(json.dumps(value));path.chmod(0o600)

    def recover(self):
        return self.fixture.executor.recover(**self.fixture.fixture.fixture.preflight_args())

    def test_cancel_before_merge_does_not_open_execution_credentials(self):
        self.cancel()
        with self.assertRaises(ExecutionCancelled): self.recover()
        self.assertEqual(self.fixture.merge_calls, 0)
        self.assertFalse(self.fixture.closed)
        self.assertTrue(self.control.summary()['cancelled'])

    def test_cancel_after_merge_keeps_git_fact_and_stops_argo_writes(self):
        f = self.fixture; original = f.merge
        def merge(request):
            response = original(request); self.cancel(); return response
        # The session's callable resolves self.merge dynamically.
        f.merge = merge
        with self.assertRaises(ExecutionCancelled): self.recover()
        self.assertEqual(f.merge_calls, 1)
        self.assertEqual(f.argo_calls, [])
        data = json.loads(self.control.path.read_text())
        self.assertEqual(data['confirmed']['merge'], f.merge_sha)
        self.assertTrue(data['cancelled'])
        self.assertEqual(data['verified_merge']['response']['sha'], f.merge_sha)

    def test_cancel_after_set_stops_sync_and_records_what_was_applied(self):
        f = self.fixture; original = f.argo
        def argo(role, command):
            original(role, command)
            if command[1] == 'set': self.cancel()
        f.transport.argo = argo
        with self.assertRaises(ExecutionCancelled): self.recover()
        self.assertEqual([c[1][1] for c in f.argo_calls], ['set'])
        self.assertEqual(self.control.summary()['confirmed_changes']['argo_set'], f.merge_sha)
        self.assertNotIn('argo_sync', self.control.summary()['confirmed_changes'])

    def test_cancel_does_not_block_owned_safe_rollback_or_revert_git(self):
        f=self.fixture;original=f.merge
        def merge(request):
            result=original(request);self.cancel();return result
        f.merge=merge
        with self.assertRaises(ExecutionCancelled):self.recover()
        f.executor.restore()
        self.assertTrue(f.executor.finished)
        self.assertFalse(f.fixture.server.value['spec']['holderIdentity'])
        self.assertEqual(self.control.summary()['confirmed_changes']['rollback'],f.plan.normal_revision)
        main=f.fixture.fixture.reader.data[f.fixture.repo_api+'/git/ref/heads/main']['object']['sha']
        self.assertEqual(main,f.merge_sha)
        self.assertEqual(f.merge_calls,1)

    def test_lost_merge_response_is_recorded_as_uncertain_and_never_retried(self):
        f = self.fixture; f.lose_response = True
        with self.assertRaises(OSError): self.recover()
        self.assertEqual(f.merge_calls, 1)
        data = json.loads(self.control.path.read_text())
        self.assertEqual(data['pending_operation'], 'merge')
        self.assertNotIn('merge', data['confirmed'])
        self.assertFalse(self.control.summary()['automatic_resume_allowed'])

    def test_verified_merge_survives_process_restart_and_rechecks_github(self):
        f = self.fixture; original = f.argo
        def stopped(role, command):
            original(role, command)
            raise OSError('synthetic stopped process after set')
        f.transport.argo = stopped
        with self.assertRaises(OSError): self.recover()
        reader = f.fixture.fixture.reader
        merge_sha = verified_resume_merge(self.control.path, plan=f.plan, owner=self.owner, reader=reader)
        self.assertEqual(merge_sha, f.merge_sha)
        reader.data[f.fixture.repo_api+'/git/ref/heads/main']['object']['sha'] = 'f'*40
        with self.assertRaises(ValueError):
            verified_resume_merge(self.control.path, plan=f.plan, owner=self.owner, reader=reader)
        self.assertEqual(f.merge_calls, 1)

    def test_restart_after_set_uses_verified_journal_for_owned_rollback_only(self):
        f=self.fixture;original=f.argo
        def stopped(role,command):
            original(role,command)
            raise OSError('synthetic crash after set')
        f.transport.argo=stopped
        with self.assertRaises(OSError):self.recover()
        f.transport.argo=original;f.transport.clock=lambda:f.fixture.now
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        import sys
        with patch.object(sys,'path',[str(scripts),*sys.path]):
            spec=importlib.util.spec_from_file_location('resume_control_test',scripts/'restore_c4_trial.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from ai_ops.c_trial_execution import TrialAuthorization
        from datetime import timedelta
        approval=TrialAuthorization(trial_id=f.plan.trial_id,plan_hash=f.plan.digest(),
            actor_github_id=149856960,approved_at=f.plan.approval_cutoff-timedelta(seconds=1),fault_injection_approved=True)
        with patch('os.kill',side_effect=ProcessLookupError):
            result=module.resume(f.plan,approval,self.owner,f.transport,handoff=f.executor.handoff,
                journal=self.control.path,reader=f.fixture.fixture.reader)
        self.assertEqual(result['previously_merged_sha'],f.merge_sha)
        self.assertFalse(result['new_merge_performed'])
        self.assertEqual(f.merge_calls,1)
        self.assertFalse(f.fixture.server.value['spec']['holderIdentity'])
        self.assertEqual(f.fixture.fixture.live.value['application']['spec']['source']['targetRevision'],f.plan.normal_revision)
        count=len(f.argo_calls)
        with patch('os.kill',side_effect=ProcessLookupError),self.assertRaises(ValueError):
            module.resume(f.plan,approval,self.owner,f.transport,handoff=f.executor.handoff,
                journal=self.control.path,reader=f.fixture.fixture.reader)
        self.assertEqual(len(f.argo_calls),count)

    def test_changed_journal_or_cancel_actor_stops_before_new_writes(self):
        self.cancel()
        p = self.root/'cancel-execution.json';d=json.loads(p.read_text());d['actor_github_id']=1;p.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError, 'cancel_binding'): self.control.check()
        p.unlink()
        d=json.loads(self.control.path.read_text());d['version']=999;self.control.path.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError, 'journal_changed'):self.control.before('merge')

    def test_authenticated_proposal_cancel_is_rechecked_after_merge(self):
        f=self.fixture;original=f.merge;args=f.fixture.fixture.preflight_args()
        def merge(request):
            response=original(request)
            args['drafts'].cancel(args['proposal_id'],args['allowed_github_id'],now=f.fixture.now)
            return response
        f.merge=merge
        with self.assertRaises(ExecutionCancelled):f.executor.recover(**args)
        self.assertEqual(f.merge_calls,1);self.assertEqual(f.argo_calls,[])

    def test_cancel_request_is_idempotent_and_cannot_claim_rollback(self):
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        spec=importlib.util.spec_from_file_location('cancel_control_test',scripts/'cancel_c4_trial.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        p=self.root/'result-owner.json';p.write_text(json.dumps(self.owner));p.chmod(0o600)
        with self.assertRaises(ValueError):module.request_cancel(self.fixture.plan,self.root,1)
        one=module.request_cancel(self.fixture.plan,self.root,149856960)
        before=(self.root/'cancel-execution.json').read_bytes()
        self.assertEqual(one,module.request_cancel(self.fixture.plan,self.root,149856960))
        self.assertEqual(before,(self.root/'cancel-execution.json').read_bytes())
        self.assertFalse(one['already_applied_changes_reverted'])


if __name__ == '__main__': unittest.main()
