"""실제 임시 Git 저장소로 재실행과 main 진전 시 덮어쓰기 방지를 검증한다."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/update-app-image.sh"
VALUES = "url-shortener/url-shortener-chart/values.prod.yaml"
TAG = "sha-aaaaaaa"


class GitOpsTests(unittest.TestCase):
    """AWS나 운영 저장소 대신 임시 bare remote와 두 checkout을 사용한다."""

    def git(self, root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE, text=True).strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote = root / "remote.git"
        self.seed = root / "seed"
        self.job = root / "job"
        subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.seed)], check=True, capture_output=True)
        self.git(self.seed, "config", "user.name", "CI test")
        self.git(self.seed, "config", "user.email", "ci@example.invalid")
        self.write_values("sha-1111111")
        self.commit("initial")
        subprocess.run(["git", "clone", str(self.remote), str(self.job)], check=True, capture_output=True)
        self.initial = self.git(self.job, "rev-parse", "HEAD")
        tools = root / "bin"
        tools.mkdir()
        # yq의 파일 입출력만 대체하고 Git 명령은 실제 실행한다.
        yq = tools / "yq"
        yq.write_text('''#!/usr/bin/env python3
import pathlib, re, sys
if sys.argv[-1] == '-':
    print(re.search(r'tag: (\\S+)', sys.stdin.read()).group(1))
else:
    p=pathlib.Path(sys.argv[-1]); tag=re.search(r'"([^\"]+)"', sys.argv[2]).group(1)
    p.write_text(re.sub(r'tag: \\S+', 'tag: '+tag, p.read_text()))
''')
        yq.chmod(0o700)
        self.env = dict(os.environ, IMAGE_TAG=TAG, PATH=str(tools) + os.pathsep + os.environ["PATH"])

    def write_values(self, tag):
        file = self.seed / VALUES
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"api:\n  image:\n    tag: {tag}\n")

    def commit(self, message):
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", message)
        self.git(self.seed, "push", "origin", "main")

    def update(self):
        return subprocess.run(["bash", str(SCRIPT)], cwd=self.job, env=self.env, capture_output=True, text=True)

    def test_current_main_only_changes_local_image_tag(self):
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(TAG, (self.job / VALUES).read_text())
        self.assertEqual(self.git(self.job, "rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git(self.remote, "rev-parse", "main"), self.initial)

    def test_retry_accepts_existing_same_image_gitops_commit(self):
        self.write_values(TAG)
        self.commit("same image")
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.job, "rev-parse", "HEAD"), self.git(self.remote, "rev-parse", "main"))
        self.assertEqual(self.git(self.job, "status", "--porcelain"), "")

    def test_new_app_commit_is_not_overwritten(self):
        (self.seed / "app.py").write_text("version = 2\n")
        self.commit("new app")
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.job, "rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git(self.job, "status", "--porcelain"), "")

    def test_another_image_deployment_is_not_overwritten(self):
        self.write_values("sha-bbbbbbb")
        self.commit("another deployment")
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.job, "rev-parse", "HEAD"), self.initial)
        self.assertIn("sha-1111111", (self.job / VALUES).read_text())

    def test_invalid_tag_is_rejected_without_changes(self):
        self.env["IMAGE_TAG"] = "latest"
        self.assertNotEqual(self.update().returncode, 0)
        self.assertEqual(self.git(self.job, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
