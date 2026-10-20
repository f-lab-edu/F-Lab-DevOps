"""외부 AWS·Docker 호출 없이 이미지 복구의 실패 경계와 재사용을 검증한다."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/app-image.py"
SPEC = importlib.util.spec_from_file_location("app_image", SCRIPT)
image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image)
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
REGISTRY = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com"


class ImageTests(unittest.TestCase):
    """동일 태그의 덮어쓰기와 조회 오류 후 잘못된 빌드를 막는다."""

    def setUp(self):
        self.commands = []
        self.present = True
        self.label = SHA
        self.arch = "amd64"
        self.published_digest = DIGEST
        self.built_digest = DIGEST
        self.lookup_error = None
        self.build_error = False

    def command(self, args, **kwargs):
        self.commands.append(args)
        if args[:3] == ["aws", "ecr", "describe-images"]:
            if self.lookup_error:
                return subprocess.CompletedProcess(args, 254, "", self.lookup_error)
            if not self.present:
                return subprocess.CompletedProcess(args, 254, "", "An error occurred (ImageNotFoundException) when calling DescribeImages")
            return subprocess.CompletedProcess(args, 0, json.dumps({"imageDetails": [{"imageDigest": self.published_digest}]}), "")
        if args[:3] == ["docker", "buildx", "build"]:
            if self.build_error:
                return subprocess.CompletedProcess(args, 1, "", "build failed")
            Path(args[args.index("--metadata-file") + 1]).write_text(json.dumps({"containerimage.digest": self.built_digest}))
            self.present = True
        if args[:3] == ["docker", "image", "inspect"]:
            data = [{"Os": "linux", "Architecture": self.arch, "Config": {"Labels": {"org.opencontainers.image.revision": self.label}}}]
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def ensure(self):
        with patch.object(image.subprocess, "run", side_effect=self.command):
            return image.ensure_image(REGISTRY, "urlshortener", SHA, Path("url-shortener"))

    def test_existing_image_is_pulled_by_digest_without_build_or_push(self):
        result = self.ensure()
        self.assertTrue(result["reused"])
        self.assertEqual(result["image_digest"], DIGEST)
        self.assertFalse(any(c[:3] == ["docker", "buildx", "build"] for c in self.commands))
        self.assertIn(["docker", "pull", "--platform", "linux/amd64", f"{REGISTRY}/urlshortener@{DIGEST}"], self.commands)

    def test_missing_image_is_built_once_with_full_revision(self):
        self.present = False
        result = self.ensure()
        self.assertFalse(result["reused"])
        builds = [c for c in self.commands if c[:3] == ["docker", "buildx", "build"]]
        self.assertEqual(len(builds), 1)
        self.assertIn(f"org.opencontainers.image.revision={SHA}", builds[0])
        self.assertEqual(result["image_tag"], "sha-aaaaaaa")
        self.assertEqual(result["source_sha"], SHA)

    def test_second_attempt_reuses_first_attempt_image(self):
        self.present = False
        self.assertFalse(self.ensure()["reused"])
        self.commands.clear()
        self.assertTrue(self.ensure()["reused"])
        self.assertFalse(any("buildx" in c for c in self.commands))

    def test_permissions_network_and_repository_errors_never_build(self):
        for error in ["(AccessDeniedException)", "Could not connect to endpoint", "(RepositoryNotFoundException)"]:
            with self.subTest(error=error):
                self.commands.clear()
                self.lookup_error = error
                with self.assertRaisesRegex(RuntimeError, "ECR"):
                    self.ensure()
                self.assertEqual(len(self.commands), 1)

    def test_short_sha_collision_is_rejected(self):
        self.label = SHA[:7] + "c" * 33
        with self.assertRaises(ValueError):
            self.ensure()
        self.assertFalse(any("buildx" in c for c in self.commands))

    def test_legacy_image_without_revision_is_rejected(self):
        self.label = None
        with self.assertRaises(ValueError):
            self.ensure()

    def test_wrong_architecture_is_rejected(self):
        self.arch = "arm64"
        with self.assertRaises(ValueError):
            self.ensure()

    def test_build_failure_does_not_continue_to_success(self):
        self.present = False
        self.build_error = True
        with self.assertRaises(RuntimeError):
            self.ensure()
        self.assertFalse(any(c[:2] == ["docker", "pull"] for c in self.commands))

    def test_published_digest_must_match_build(self):
        self.present = False
        self.built_digest = "sha256:" + "c" * 64
        with self.assertRaises(ValueError):
            self.ensure()

    def test_invalid_ecr_digest_stops_before_docker(self):
        self.published_digest = "not-a-digest"
        with self.assertRaises(ValueError):
            self.ensure()
        self.assertEqual(len(self.commands), 1)

    def test_image_only_records_result_without_git_or_argocd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main", "REQUESTED_MODE": "image-only",
                   "GITHUB_SHA": SHA, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "ECR_REGISTRY": REGISTRY,
                   "ECR_REPOSITORY": "urlshortener", "RUNNER_TEMP": str(root), "GITHUB_OUTPUT": str(root / "output"),
                   "GITHUB_STEP_SUMMARY": str(root / "summary")}
            with patch.dict(os.environ, env), patch.object(image.sys, "argv", ["app-image.py", "image"]), patch.object(image.subprocess, "run", side_effect=self.command):
                image.main()
            record = json.loads((root / "app-image/image-registration.json").read_text())
            self.assertEqual(record["mode"], "image-only")
            self.assertEqual(record["image_digest"], DIGEST)
            self.assertFalse(any(c[0] in {"git", "argocd", "kubectl"} for c in self.commands))


class ModeTests(unittest.TestCase):
    """수동 복구의 기본 모드와 운영 브랜치 경계를 검증한다."""

    def test_push_keeps_automatic_deployment(self):
        self.assertEqual(image.execution_mode("push", "refs/heads/main", "image-only"), "deploy")

    def test_manual_modes(self):
        for mode in ["image-only", "deploy"]:
            self.assertEqual(image.execution_mode("workflow_dispatch", "refs/heads/main", mode), mode)

    def test_branch_tag_pr_and_unknown_mode_are_rejected(self):
        for event, ref, mode in [("workflow_dispatch", "refs/heads/develop", "deploy"), ("workflow_dispatch", "refs/tags/main", "deploy"),
                                 ("pull_request", "refs/heads/main", "deploy"), ("workflow_dispatch", "refs/heads/main", "unknown")]:
            with self.subTest(event=event, ref=ref, mode=mode), self.assertRaises(ValueError):
                image.execution_mode(event, ref, mode)


if __name__ == "__main__":
    unittest.main()
