"""CLI 경로 경계·링크 우회·기존 파일 보호를 실제 파일과 명령으로 검증한다."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ops.cli import main
from ai_ops.path_policy import PACKAGE_ROOT, permitted_path, write_private_json


class PathPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.allowed = self.base / "approved"
        self.allowed.mkdir(mode=0o700)
        self.outside = self.base / "approved-other"
        self.outside.mkdir()
        self.policy = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.allowed)})
        self.policy.start()
        self.addCleanup(self.policy.stop)
        self.json = self.allowed / "input.json"
        self.json.write_text("{}")
        self.csv = self.allowed / "input.csv"
        self.csv.write_text("header\n")

    def test_outside_parent_traversal_and_prefix_sibling_are_denied(self):
        outside = self.outside / "source.json"
        outside.write_text("{}")
        for path in (outside, self.allowed / ".." / "approved-other" / "source.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                permitted_path(path, "input")
        with self.assertRaises(ValueError):
            permitted_path(PACKAGE_ROOT / "config" / "new-store", "store")

    def test_input_links_can_rotate_inside_the_mount_but_cannot_escape(self):
        internal = self.allowed / "projected.json"
        internal.symlink_to(self.json)
        self.assertEqual(permitted_path(internal, "input"), self.json.resolve())
        outside = self.outside / "secret.json"
        outside.write_text("{}")
        internal.unlink()
        internal.symlink_to(outside)
        with self.assertRaises(ValueError):
            permitted_path(internal, "input")

    def test_outputs_reject_target_and_parent_links_without_changing_the_victim(self):
        victim = self.allowed / "victim.json"
        victim.write_text('{"preserve":true}')
        link = self.allowed / "link.json"
        link.symlink_to(victim)
        parent = self.allowed / "linked-directory"
        parent.symlink_to(self.allowed, target_is_directory=True)
        for path in (link, parent / "new.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                write_private_json(path, {"changed": True})
        self.assertEqual(victim.read_text(), '{"preserve":true}')
        self.assertFalse((self.allowed / "new.json").exists())

    def test_private_result_creation_never_overwrites_an_existing_file(self):
        path = self.allowed / "results" / "score.json"
        write_private_json(path, {"score": 1})
        self.assertEqual(json.loads(path.read_text()), {"score": 1})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises((ValueError, FileExistsError)):
            write_private_json(path, {"score": 2})
        self.assertEqual(json.loads(path.read_text()), {"score": 1})

    def test_only_evaluation_checkpoint_names_allow_resume(self):
        checkpoint = self.allowed / "a7-v3-rules-results.json"
        checkpoint.write_text("{}")
        self.assertEqual(permitted_path(checkpoint, "checkpoint"), checkpoint.resolve())
        for name in ("ai-budget.json", "metadata.json", "arbitrary.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                permitted_path(self.allowed / name, "checkpoint")

    def test_main_validates_default_store_before_reading_any_key(self):
        with (patch("ai_ops.cli.DEFAULT_STORE", self.outside),
              patch("ai_ops.cli.IncidentStore") as store,
              contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result):
            main(["api-key-status"])
        self.assertEqual(result.exception.code, 2)
        store.assert_not_called()

    def test_all_operator_scripts_reject_outside_paths_before_processing(self):
        scripts = PACKAGE_ROOT / "scripts"
        suite = PACKAGE_ROOT / "evals/fixtures/holdout/suite.json"
        rubric = PACKAGE_ROOT / "evals/rubrics/holdout/scenarios.json"
        blocked = str(self.outside / "output.json")
        commands = [
            ["run_holdout.py", "--result", blocked, "--store", str(self.allowed / "store")],
            ["score_holdout.py", "--runs", str(self.json), "--output", blocked],
            ["score_manual_baseline.py", "--csv", str(self.csv), "--output", blocked],
            ["export_holdout_review.py", "--runs", str(self.json), "--suite", str(suite), "--rubric", str(rubric),
             "--packet", blocked, "--template", str(self.allowed / "template.json")],
            ["prepare_blind_review.py", "prepare", "--fixed-packet", str(self.json), "--ai-packet", str(self.json),
             "--html", str(PACKAGE_ROOT / "site" / "review.html"), "--mapping", blocked],
            ["prepare_manual_baseline.py", "--output", str(self.outside / "review.html")],
        ]
        for script, *args in commands:
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, str(scripts / script), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("파일 경로 정책 위반", result.stderr)
        self.assertFalse(Path(blocked).exists())

    def test_additional_roots_cannot_grant_repository_writes_or_use_shared_permissions(self):
        for root in (PACKAGE_ROOT, PACKAGE_ROOT / "config", self.base / "missing"):
            with self.subTest(root=root), patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(root)}):
                with self.assertRaises(ValueError):
                    permitted_path(self.json, "input")
        self.allowed.chmod(0o777)
        try:
            with self.assertRaises(ValueError):
                permitted_path(self.json, "input")
        finally:
            self.allowed.chmod(0o700)

    def test_broad_or_symbolic_policy_roots_cannot_expand_the_allowlist(self):
        link = self.base / "policy-link"
        link.symlink_to(self.allowed, target_is_directory=True)
        for root in ("/", str(Path.home()), "/tmp", str(link), "relative-folder"):
            with self.subTest(root=root), patch.dict(os.environ, {"AI_OPS_FILES_ROOT": root}):
                with self.assertRaises(ValueError):
                    permitted_path(self.json, "input")


if __name__ == "__main__":
    unittest.main()
