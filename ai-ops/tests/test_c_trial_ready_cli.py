"""새 CLI의 미래 승인 파일·경로 경계와 공통 복구 함수의 실제 연결을 검증한다."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


def load(name):
    scripts = Path(__file__).resolve().parents[1]/'scripts'
    import sys
    with patch.object(sys, 'path', [str(scripts), *sys.path]):
        spec = importlib.util.spec_from_file_location('test_'+name, scripts/(name+'.py'))
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class ReadyCLITest(unittest.TestCase):
    def test_resume_owner_and_fixed_deadlines_are_published_before_guard_activation(self):
        import test_c_trial_ready as fixtures
        f = fixtures.ReadyStartTest(); f.setUp(); self.addCleanup(f.doCleanups)
        module = load('run_c4_ready_trial')
        root = Path(f.root.name)
        original_arm = f.port.arm_ready_guards
        def arm(activation, lease):
            with patch.dict(os.environ, {'AI_OPS_FILES_ROOT': str(root)}):
                module.publish_execution(activation, f.controller.executor, root, root/'activation.json')
            owner = json.loads((root/'result-owner.json').read_text())
            self.assertEqual(owner['injection_holder'], lease.holder)
            self.assertEqual(owner['plan_hash'], activation.execution.digest())
            self.assertEqual(owner['pid'], os.getpid())
            self.assertEqual(f.port.calls, [])
            original_arm(activation, lease)
        f.port.arm_ready_guards = arm
        executor, _ = f.start()
        self.assertTrue((root/'active-plan.json').exists())
        self.assertEqual(len(f.port.calls), 2)

    def test_actual_arguments_allow_future_approval_but_no_calendar_start(self):
        module = load('run_c4_ready_trial')
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            root = Path(folder); root.chmod(0o700); run = root/'run'; argv = []
            for n in ('intent', 'preflight', 'guard-evidence', 'normal-binding', 'projection-probe', 'c2-kubeconfig'):
                p = root/(n+'.json'); p.write_text('{}'); argv += ['--'+n, str(p)]
            for n in ('approval', 'readiness', 'activation', 'output'):
                argv += ['--'+n, str(run/(n+'.json'))]
            argv += ['--run-dir', str(run), '--operator-state-root', '/private/tmp/c04-789',
                '--kubeconfig', '/private/tmp/existing.json', '--normal-run-id', '123',
                '--review-notifier-socket', '/private/tmp/c04-789/review.sock', '--execute-approved-trial']
            with patch.dict(os.environ, {'AI_OPS_FILES_ROOT': str(root)}):
                parsed = module.parse_args(argv)
            self.assertFalse(parsed.approval.exists()); self.assertFalse(run.exists())
            self.assertNotIn('planned_at', vars(parsed)); self.assertNotIn('approval_cutoff', vars(parsed))

    def test_shared_recovery_really_investigates_then_uses_owned_restore_on_ended_review(self):
        module = load('run_c4_trial')
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder); (p/'post-state.json').write_text(json.dumps({'stage': 'review_deadline_passed_no_execution_context'}))
            executor = Mock(finished=False)
            from datetime import datetime, timedelta, timezone
            executor.plan = SimpleNamespace(manual_recovery_at=datetime.now(timezone.utc)+timedelta(seconds=30))
            executor.handoff = Mock()
            executor.investigate.return_value = {'name': 'capture', 'uid': 'owned'}
            evidence = {}
            with patch.object(module, 'private_json', return_value={'stage': 'review_deadline_passed_no_execution_context'}):
                module.recover_and_observe(SimpleNamespace(output=p/'result.json',
                    recovery_context=p/'missing.json', kubeconfig='synthetic'), executor, Mock(), evidence)
            executor.investigate.assert_called_once(); executor.restore.assert_called_once()
            self.assertEqual(evidence['status'], 'restored_without_c03_completion')



if __name__ == '__main__': unittest.main()
