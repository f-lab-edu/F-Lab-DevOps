"""검증된 실행 소스의 누락·추가·변경을 실행 전에 거부한다."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.c_trial_bundle import verify_runtime_bundle,build_runtime,verify_ready_runtime
from types import SimpleNamespace


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.file=self.root/'code.py';self.file.write_text('original')
        self.manifest={'schema_version':1,'credentials_included':False,'sdk_versions':{},
            'files':{'code.py':hashlib.sha256(self.file.read_bytes()).hexdigest()}}
        self.save()

    def save(self):
        (self.root/'runtime-manifest.json').write_text(json.dumps(self.manifest))

    def test_valid_bundle_passes_but_changed_source_does_not(self):
        verify_runtime_bundle(self.root)
        self.file.write_text('changed')
        with self.assertRaises(ValueError):verify_runtime_bundle(self.root)

    def test_changed_code_and_matching_new_manifest_cannot_replace_ready_approval(self):
        intent = SimpleNamespace(runtime_manifest_sha256=hashlib.sha256((self.root/'runtime-manifest.json').read_bytes()).hexdigest())
        verify_ready_runtime(self.root, intent)
        self.file.write_text('new code')
        self.manifest['files']['code.py'] = hashlib.sha256(self.file.read_bytes()).hexdigest(); self.save()
        verify_runtime_bundle(self.root)
        with self.assertRaises(ValueError): verify_ready_runtime(self.root, intent)

    def test_unindexed_code_or_missing_source_is_refused(self):
        extra=self.root/'unreviewed.py';extra.write_text('extra')
        with self.assertRaises(ValueError):verify_runtime_bundle(self.root)
        extra.unlink();self.file.unlink()
        with self.assertRaises(ValueError):verify_runtime_bundle(self.root)

    def test_wrong_sdk_version_cannot_be_used(self):
        self.manifest['sdk_versions']={'boto3':'synthetic'};self.save()
        with patch('importlib.metadata.version',return_value='different'),self.assertRaises(ValueError):verify_runtime_bundle(self.root)

    def test_builder_preserves_c03_contract_instead_of_overlaying_old_main(self):
        base=self.root/'base';overlay=self.root/'overlay';base.mkdir();overlay.mkdir()
        old=base/'ai-ops/src/ai_ops/c_recovery.py';old.parent.mkdir(parents=True);old.write_text('RECOVERY_REPOSITORIES = "preserved"')
        stale=overlay/'ai-ops/src/ai_ops/c_recovery.py';stale.parent.mkdir(parents=True);stale.write_text('old main without required contract')
        (stale.parent/'c_trial_schedule.py').write_text('new scheduling code')
        for name in ('pyproject.toml','requirements.lock','requirements-trial.lock'):
            (overlay/'ai-ops'/name).write_text('local fixture')
        before={'base_sha':'a'*40,'credentials_included':False,
            'files':{'ai-ops/src/ai_ops/c_recovery.py':hashlib.sha256(old.read_bytes()).hexdigest()}}
        meta=self.root/'base-manifest.json';meta.write_text(json.dumps(before))
        with patch('importlib.metadata.version',return_value='synthetic'):
            build_runtime(base,meta,overlay,self.root/'built')
            result=build_runtime(self.root/'built',self.root/'built/runtime-manifest.json',overlay,self.root/'rebuilt')
        self.assertEqual(result['source_base_sha'],'a'*40)
        self.assertEqual((self.root/'built/ai-ops/src/ai_ops/c_recovery.py').read_text(),old.read_text())
        self.assertEqual((self.root/'rebuilt/ai-ops/src/ai_ops/c_recovery.py').read_text(),old.read_text())
        self.assertEqual((self.root/'built/ai-ops/src/ai_ops/c_trial_schedule.py').read_text(),'new scheduling code')


if __name__=='__main__':unittest.main()
