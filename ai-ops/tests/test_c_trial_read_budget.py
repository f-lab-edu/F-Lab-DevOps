"""실제 조회기의 시간 예산과 고정 거절 코드의 정보 경계를 검증한다."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_ops.c_approval import ApprovalRejected
from ai_ops.c_trial_read_budget import recovery_github_reader, approval_rejection_codes


class TrialReadBudgetTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        self.repo = 'joungGo/c03-execution-lab'

    def reader(self, remaining):
        with patch('ai_ops.c_approval.time.monotonic', return_value=1000):
            return recovery_github_reader(self.now+timedelta(seconds=remaining),
                                          repository=self.repo, clock=lambda: self.now)

    def test_non_github_collection_over_45_seconds_keeps_reads_within_owned_deadline(self):
        reader = self.reader(100)
        real_popen = subprocess.Popen
        def fake_github(command, **kwargs):
            return real_popen([sys.executable, '-c', 'print("{\\"id\\":149856960}")'], **kwargs)
        # Kubernetes/ECR 수집에 50초가 걸린 뒤 실제 subprocess/JSON reader를 통과한다.
        with patch('ai_ops.c_approval.time.monotonic', return_value=1050), \
             patch('ai_ops.c_approval.subprocess.Popen', side_effect=fake_github):
            self.assertEqual(reader.get('user'), {'id': 149856960})
        with patch('ai_ops.c_approval.time.monotonic', return_value=1100), \
             patch('ai_ops.c_approval.subprocess.Popen') as process:
            with self.assertRaisesRegex(ApprovalRejected, '^github_read_timeout$'):
                reader.get('user')
            process.assert_not_called()

    def test_budget_keeps_180_second_cap_and_rounds_down_before_owned_deadline(self):
        self.assertEqual(self.reader(300).deadline, 1180)
        self.assertEqual(self.reader(8.9).deadline, 1008)
        for remaining in (7.99, 0, -1):
            with self.subTest(remaining=remaining), self.assertRaisesRegex(
                    ApprovalRejected, '^github_recovery_read_window_too_short$'):
                self.reader(remaining)

    def test_only_allowlisted_approval_codes_survive_nested_exceptions(self):
        try:
            try:
                raise ApprovalRejected('github_read_timeout')
            except ApprovalRejected as cause:
                raise ApprovalRejected('fresh_live_proof_unavailable') from cause
        except ApprovalRejected as error:
            self.assertEqual(approval_rejection_codes(error),
                             ['fresh_live_proof_unavailable', 'github_read_timeout'])
        secret = 'sensitive-response-not-for-results'
        self.assertEqual(approval_rejection_codes(ApprovalRejected(secret)), ['approval_rejected'])
        self.assertEqual(approval_rejection_codes(OSError(secret)), [])

    def test_cyclic_exception_chain_is_bounded(self):
        error = ApprovalRejected('github_read_timeout'); error.__cause__ = error
        self.assertEqual(approval_rejection_codes(error), ['github_read_timeout'])

    def test_recovery_cli_passes_its_actual_owner_deadline_to_reader(self):
        from test_c_failure_handoff_runtime import FailureHandoffRuntimeTest
        fixture = FailureHandoffRuntimeTest(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        script = Path(__file__).resolve().parents[1]/'scripts/run_c4_trial.py'
        spec = importlib.util.spec_from_file_location('budget_trial_cli', script)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); root.chmod(0o700)
            context = {'trial_id': 9, 'normal_binding': str(root/'binding.json'),
                'normal_run_id': 1, 'normal_head': 'a'*40, 'operator_state_root': str(root/'operator'),
                'incident_id': 'incident', 'incident_run_id': 'run', 'request_id': 'request',
                'proposal_id': 'proposal', 'proposal_audit': str(root/'audit.json'),
                'pr_link': str(root/'link.json'), 'pull_number': 1}
            for name, value in {'context.json': context, 'binding.json': {}, 'audit.json': {}, 'review-notified.json': {},
                                'link.json': fixture.fixture.link.model_dump(mode='json')}.items():
                path=root/name; path.write_text(json.dumps(value)); path.chmod(0o600)
            executor = SimpleNamespace(plan=SimpleNamespace(trial_id=9, schema_version=2,
                manual_recovery_at=self.now+timedelta(seconds=100)), handoff=object(),
                clock=lambda: self.now, read_caches={})
            with patch.dict(os.environ, {'AI_OPS_FILES_ROOT': str(root)}), \
                 patch.object(module, 'TrialSource'), \
                 patch('ai_ops.c_approval.time.monotonic', return_value=1000):
                actual = module.recovery_args(root/'context.json', executor, object(), 'synthetic')
            self.assertEqual(actual['reader'].deadline, 1100)
            self.assertEqual(actual['pull_number'], 1)


if __name__ == '__main__': unittest.main()
