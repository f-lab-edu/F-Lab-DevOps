"""비공개 보존 폴더나 미커밋 파일 없이 Git commit만으로 묶음을 재현한다."""

from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.c_trial_bundle import build_repository_runtime


class RepositoryBundleTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name); self.repo = self.root/'repo'; self.repo.mkdir()
        self.env = {**os.environ, 'GIT_AUTHOR_NAME': 'Local test', 'GIT_AUTHOR_EMAIL': 'local@example.invalid',
                    'GIT_COMMITTER_NAME': 'Local test', 'GIT_COMMITTER_EMAIL': 'local@example.invalid'}
        self.git('init', '-q')
        self.files = ['ai-ops/src/ai_ops/c_approval.py', 'ai-ops/src/ai_ops/c_execution_preflight.py',
                      'ai-ops/src/ai_ops/c_trial_ready.py', '.github/workflows/ai-ops-c4-ready-rollback.yml']
        for name in self.files:
            p = self.repo/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('committed source\n')
        self.git('add', '.'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'local fixture')
        self.sha = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True, env=self.env).stdout

    def build(self):
        with patch('ai_ops.c_trial_bundle.importlib.metadata.version', return_value='local-fixture'):
            return build_repository_runtime(self.repo, self.sha, self.root/'runtime')

    def test_only_reviewed_commit_is_copied_and_untracked_credentials_are_excluded(self):
        (self.repo/'ai-ops/local-credentials.json').write_text('SYNTHETIC TEST ONLY')
        (self.repo/self.files[0]).write_text('uncommitted replacement')
        result = self.build()
        self.assertEqual(set(result['files']), set(self.files))
        self.assertEqual(result['source_base_sha'], self.sha)
        self.assertEqual((self.root/'runtime'/self.files[0]).read_text(), 'committed source\n')
        self.assertFalse((self.root/'runtime/ai-ops/local-credentials.json').exists())

    def test_symlink_in_commit_is_rejected_before_runtime_creation(self):
        (self.repo/'ai-ops/leak').symlink_to('/etc/passwd')
        self.git('add', '.'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'symlink fixture')
        self.sha = self.git('rev-parse', 'HEAD').strip()
        with self.assertRaises(ValueError): self.build()
        self.assertFalse((self.root/'runtime').exists())

    def test_workflow_and_observation_helpers_needed_by_full_tests_are_included(self):
        names = ['.github/scripts/capture-deployment-observation.py', '.github/workflows/ci-cd.yml']
        for name in names:
            p = self.repo/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('public helper')
        self.git('add', '.'); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'helpers')
        self.sha = self.git('rev-parse', 'HEAD').strip()
        result = self.build()
        self.assertTrue(set(names).issubset(result['files']))

    def test_arbitrary_revision_and_missing_dependencies_are_rejected(self):
        self.sha = '--all'
        with self.assertRaises(ValueError): self.build()
        self.git('rm', self.files[0]); self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'missing dependency')
        self.sha = self.git('rev-parse', 'HEAD').strip()
        with self.assertRaises(ValueError): self.build()
        self.assertFalse((self.root/'runtime').exists())


if __name__ == '__main__': unittest.main()
