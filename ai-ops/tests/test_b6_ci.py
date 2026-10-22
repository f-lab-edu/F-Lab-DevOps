"""실습 CI의 변경 범위·최초 실패 보존·원복 실패 잠금과 UID 경계를 검사한다."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_ops.b6_ci import LabCI, ECR, validate_sources


class B6CITest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve(); self.root.chmod(0o700)
        self.env = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root), "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.ci = LabCI(self.root)
        self.state = {"run_id": "123", "run_attempt": "1", "source_revision": "a"*40,
            "baseline_revision": "b"*40, "failure_revision": "c"*40, "good_image": ECR+"@sha256:"+"1"*64,
            "bad_image": ECR+":b6-missing-"+"2"*32, "deployment_uid": "deployment", "application_uid": "application",
            "normal_template": {"spec": {"containers": [{"image": ECR+"@sha256:"+"1"*64}]}},
            "holder": "ci-"+"d"*32, "lease": {"uid": "lease"}, "injection_requested": False,
            "deployment_verified_failure": False, "rollback_verified": False}
        self.ci.save(self.state)

    def sources(self):
        baseline=json.loads((Path(__file__).resolve().parents[1]/"lab/deployment.json").read_text())
        failure=deepcopy(baseline);failure['spec']['template']['spec']['containers'][0]['image']=self.state['bad_image']
        return baseline,failure

    def test_only_missing_image_change_is_accepted(self):
        baseline,failure=self.sources();self.assertEqual(validate_sources(baseline,failure)[1],self.state['bad_image'])
        for mutate in (lambda x:x['metadata'].update(namespace='url-shortener'),
                       lambda x:x['spec'].update(replicas=0),
                       lambda x:x['spec']['template']['spec'].update(serviceAccountName='other'),
                       lambda x:x['spec']['template']['spec']['containers'][0].update(image=ECR+':v1')):
            changed=deepcopy(failure);mutate(changed)
            with self.assertRaises(ValueError):validate_sources(baseline,changed)

    def test_non_main_or_automatic_run_rejected_before_commands(self):
        for env in ({'GITHUB_REF':'refs/heads/other'},{'GITHUB_EVENT_NAME':'push'},{'GITHUB_REPOSITORY':'other/repo'}):
            with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REPOSITORY':'f-lab-edu/F-Lab-DevOps',**env}),patch('ai_ops.b6_ci.command') as cmd:
                with self.assertRaises(ValueError):self.ci.prepare()
                cmd.assert_not_called()

    def failure_observation(self):
        return {'image':self.state['bad_image'],'ready_replicas':0,'image_pull_reasons':['ImagePullBackOff'],
                'operation_revision':self.state['failure_revision'],'generation_observed':True}

    def test_confirmed_failure_is_saved_before_rollback_and_returns_failure(self):
        with patch.object(self.ci,'argo'),patch.object(self.ci,'observe',return_value=self.failure_observation()):
            self.assertEqual(self.ci.deploy(),1)
        state=self.ci.load();self.assertTrue(state['injection_requested']);self.assertTrue(state['deployment_verified_failure'])
        self.assertEqual(json.loads((self.ci.records/'deployment.json').read_text())['verification_result'],'failure')

    def test_sync_failure_still_leaves_recovery_requested_and_inconclusive_record(self):
        with patch.object(self.ci,'argo',side_effect=ValueError('sync response lost')):
            with self.assertRaises(ValueError):self.ci.deploy()
        self.assertTrue(self.ci.load()['injection_requested'])
        self.assertEqual(json.loads((self.ci.records/'deployment.json').read_text())['verification_result'],'inconclusive')

    def test_healthy_without_ready_matching_revision_does_not_finish_rollback(self):
        self.state['injection_requested']=True;self.ci.save(self.state)
        observed={'image':self.state['good_image'],'ready_replicas':1,'sync_status':'Synced','health_status':'Healthy',
                  'operation_phase':'Succeeded','operation_revision':'c'*40,'generation_observed':True}
        with patch.object(self.ci,'argo'),patch.object(self.ci,'observe',return_value=observed):
            with self.assertRaises(ValueError):self.ci.rollback()
        self.assertFalse(self.ci.load()['rollback_verified'])
        self.assertEqual(json.loads((self.ci.records/'rollback.json').read_text())['verification_result'],'failure')

    def test_verified_rollback_keeps_original_failure_and_only_then_releases(self):
        self.state.update(injection_requested=True,deployment_verified_failure=True);self.ci.save(self.state)
        observed={'image':self.state['good_image'],'ready_replicas':1,'sync_status':'Synced','health_status':'Healthy',
                  'operation_phase':'Succeeded','operation_revision':self.state['baseline_revision'],'generation_observed':True}
        with patch.object(self.ci,'argo'),patch.object(self.ci,'observe',return_value=observed):self.ci.rollback()
        with patch('ai_ops.b6_ci.LabLease') as lease:
            lease.return_value.release.return_value={'released':True}
            self.assertEqual(self.ci.finish(),1)
            lease.return_value.release.assert_called_once_with(recovery_verified=True)
        record=json.loads((self.ci.records/'result.json').read_text());self.assertEqual(record['verification_result'],'recovered_after_failure')
        self.assertFalse(record['evidence_import_enabled']);self.assertFalse(record['user_path_recovery_verified'])

    def test_unverified_recovery_keeps_lease_and_cross_attempt_state_is_rejected(self):
        with patch('ai_ops.b6_ci.LabLease') as lease:
            self.assertEqual(self.ci.finish(),1);lease.assert_not_called()
        with patch.dict(os.environ,{'GITHUB_RUN_ATTEMPT':'2'}):
            with self.assertRaises(ValueError):self.ci.load()

    def test_changed_target_uid_or_application_source_revision_rejected(self):
        self.ci.client=Mock()
        for uid,revision in [('other',self.state['baseline_revision']),('application','e'*40)]:
            with patch.object(self.ci,'app',return_value={'metadata':{'uid':uid},'spec':{'source':{'targetRevision':revision}}}):
                self.ci.client.get.return_value={'metadata':{'uid':'deployment'}}
                with self.assertRaises(ValueError):self.ci.observe(self.state)


if __name__=='__main__':unittest.main()
