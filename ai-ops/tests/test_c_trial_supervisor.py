"""help가 아닌 실제 전체 인자가 경로 정책을 통과하는지 검사한다."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class SupervisorArgumentsTest(unittest.TestCase):
    def module(self):
        path=Path(__file__).resolve().parents[1]/'scripts/supervise_c4_trial.py'
        spec=importlib.util.spec_from_file_location('c04_supervisor_arguments',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_all_actual_path_arguments_have_explicit_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);root.chmod(0o700)
            for name in ('plan','authorization','preflight','projection-probe'):
                (root/(name+'.json')).write_text('{}')
            for name in ('runtime','guard'):(root/name).mkdir()
            argv=[]
            for name in ('plan','authorization','preflight','projection-probe'):
                argv+=['--'+name,str(root/(name+'.json'))]
            argv+=['--runtime-root',str(root/'runtime'),'--guard-source-root',str(root/'guard'),
                   '--run-dir',str(root/'live'),'--operator-state-root','/private/tmp/c04-202611110004',
                   '--kubeconfig','/private/tmp/existing-kubeconfig','--normal-run-id','42954657436',
                   '--review-notifier-socket','/private/tmp/c04-202611110004/review.sock',
                   '--execute-approved-trial']
            with patch.dict(os.environ,{'AI_OPS_FILES_ROOT':str(root)}):
                parsed=self.module().parse_args(argv)
            self.assertEqual(parsed.runtime_root,(root/'runtime').resolve())
            self.assertEqual(parsed.run_dir,(root/'live').resolve())
            self.assertFalse((root/'live').exists())


if __name__=='__main__':unittest.main()
