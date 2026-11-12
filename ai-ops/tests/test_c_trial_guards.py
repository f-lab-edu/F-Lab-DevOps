"""준비 로그의 늦은 관찰과 현재 감시 종료/소스 변경을 차단한다."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.c_trial_guards import GuardReader
import test_c_trial_execution as fixtures


class GuardReaderTest(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.TrialExecutionTest();fixture.setUp();self.plan=fixture.plan
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.log=self.root/'local.log';self.log.write_text('C-02 별도 원복 감시 준비 완료\n');self.log.chmod(0o600)
        self.anchor={'plan_hash':self.plan.digest(),'actual_ready_markers_verified':True,
            'workflow_main':self.plan.workflow_main,'rollback_deadline':self.plan.rollback_deadline.isoformat(),
            'ready_at':(self.plan.approval_cutoff-timedelta(seconds=1)).isoformat(),
            'guard_source_root':str(self.root),'guard_code_sha256':{},'local_plan':str(self.root/'plan.json'),
            'local_pid':123,'local_ready_log':str(self.log),'remote_run_id':456}
        self.rollback={'schema_version':2,'target':'urlshortener/ai-ops-lab/lab-api/practice',
            'source_repository':self.plan.repository,'application_uid':self.plan.application_uid,
            'deployment_uid':self.plan.deployment_uid,'baseline_revision':self.plan.normal_revision,
            'failure_revision':self.plan.failure_revision,'normal_image':self.plan.normal_image,
            'failure_image':self.plan.failure_image,'deadline_utc':self.plan.rollback_deadline.isoformat()}
        self.remote={'id':456,'status':'in_progress','event':'workflow_dispatch','workflow_id':401385940,
            'head_branch':'main','head_sha':self.plan.workflow_main}
        self.jobs={'total_count':1,'jobs':[{'status':'in_progress','steps':[
            {'name':'별도 기한 원복과 정상 상태 검증','status':'in_progress'}]}]}
        self.reader=GuardReader(self.root/'anchor.json',github=lambda path: deepcopy(self.jobs if '/jobs?' in path else self.remote))
        self.patches=[patch('ai_ops.c_trial_guards.private_json',side_effect=lambda p:deepcopy(self.rollback if Path(p).name=='plan.json' else self.anchor)),
            patch('ai_ops.c_trial_guards.permitted_path',return_value=self.root),
            patch('ai_ops.c_trial_guards.verify_guard_sources',return_value=lambda *a,**k:None),
            patch('ai_ops.c_trial_guards.os.kill'),
            patch('ai_ops.c_trial_guards.subprocess.run')]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.patches[-1].target.run.return_value.stdout=(str(self.root/'ai-ops/scripts/run_c2_rollback_guard.py')+' '+self.anchor['local_plan']).encode()

    def test_exact_live_guard_sources_and_current_job_are_required(self):
        self.assertEqual(self.reader(self.plan)['remote_run_id'],456)

    def test_late_ready_timestamp_does_not_count_as_earlier_preparation(self):
        self.anchor['ready_at']=self.plan.approval_cutoff.isoformat()
        with self.assertRaises(ValueError):self.reader(self.plan)

    def test_cancelled_remote_and_wrong_sha_are_refused(self):
        for key,value in [('status','completed'),('head_sha','f'*40)]:
            original=self.remote[key];self.remote[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.reader(self.plan)
            self.remote[key]=original

    def test_truncated_jobs_or_missing_actual_local_marker_are_refused(self):
        self.jobs['total_count']=2
        with self.assertRaises(ValueError):self.reader(self.plan)
        self.jobs['total_count']=1;self.log.write_text('not ready')
        with self.assertRaises(ValueError):self.reader(self.plan)

    def test_local_process_exit_and_source_mismatch_are_not_swallowed(self):
        with patch('ai_ops.c_trial_guards.os.kill',side_effect=ProcessLookupError),self.assertRaises(ProcessLookupError):self.reader(self.plan)
        with patch('ai_ops.c_trial_guards.verify_guard_sources',side_effect=ValueError),self.assertRaises(ValueError):self.reader(self.plan)


if __name__=='__main__':unittest.main()
