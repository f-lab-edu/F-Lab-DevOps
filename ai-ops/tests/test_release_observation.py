"""배포 관측의 원문 제거·조회 상한과 CI의 기존 실패·롤백 동작을 검증한다."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / ".github/scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observe = load("deployment_observation", "capture-deployment-observation.py")
image_record = load("agent_image_record", "write-ai-ops-image-record.py")
SHA = "a" * 40
ROLLBACK = "b" * 40
SECRET = "raw-secret-sentinel-not-for-artifacts"
REPO = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener"


def yaml_file(path):
    result = subprocess.check_output(["ruby", "-rjson", "-ryaml", "-e", "puts JSON.generate(YAML.load_file(ARGV[0]))", str(path)], text=True)
    return json.loads(result)


class ReleaseObservationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = dict(os.environ, GITHUB_REPOSITORY=observe.REPOSITORY, GITHUB_SHA=SHA,
            GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2", OBS_STAGE="deployment_verification", OBS_RESULT="failure",
            OBS_EXPECTED_REVISION=SHA, OBS_EXPECTED_IMAGE=REPO + ":sha-aaaaaaa", OBS_IMAGE_REPOSITORY=REPO,
            OBS_STARTED_AT="2026-10-16T10:00:00+09:00", OBS_FINISHED_AT="2026-10-16T01:01:00Z",
            ARGOCD_SERVER="https://argocd.validation.invalid", ARGOCD_AUTH_TOKEN=SECRET)
        self.doc = {"metadata": {"name": "url-shortener", "namespace": "argocd", "annotations": {"ignored": SECRET}},
            "spec": {"source": {"repoURL": observe.REPO_URL, "path": observe.CHART_PATH, "helm": {"values": SECRET}},
                "destination": {"namespace": "url-shortener", "server": "https://kubernetes.default.svc"}},
            "status": {"sync": {"revision": SHA, "status": "OutOfSync"}, "health": {"status": "Degraded", "message": SECRET},
                "operationState": {"phase": "Failed", "message": SECRET, "syncResult": {"revision": SHA}},
                "reconciledAt": "2026-10-16T10:01:01+09:00", "summary": {"images": [REPO + ":sha-aaaaaaa", REPO + ":" + SECRET]}}}

    def raw(self):
        return json.dumps(self.doc).encode()

    def test_only_allowlisted_fields_survive_and_time_is_utc(self):
        record = observe.build_record(self.env, self.raw(), "available")
        self.assertEqual(record["collection_status"], "available")
        self.assertEqual(record["verification_result"], "failure")
        self.assertEqual(record["verification_started_at"], "2026-10-16T01:00:00Z")
        self.assertEqual(record["observed"]["reconciled_at"], "2026-10-16T01:01:01Z")
        self.assertEqual(record["observed"]["images"], [REPO + ":sha-aaaaaaa"])
        self.assertEqual(record["observed"]["images_omitted"], 1)
        path = self.root / "artifact" / "deployment.json"
        observe.write_record(path, record)
        self.assertNotIn(SECRET, path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["deployment.json"])

    def test_current_healthy_does_not_overwrite_verification_failure(self):
        self.doc["status"]["health"]["status"] = "Healthy"
        self.doc["status"]["sync"]["revision"] = ROLLBACK
        record = observe.build_record(self.env, self.raw(), "available")
        self.assertEqual(record["verification_result"], "failure")
        self.assertEqual(record["expected_revision"], SHA)
        self.assertEqual(record["observed"]["revision"], ROLLBACK)
        self.assertNotIn("recovered", json.dumps(record))

    def test_other_namespace_repo_or_application_is_unavailable(self):
        for section, key, value in [("metadata", "name", "other"), ("metadata", "namespace", "other")]:
            doc = json.loads(self.raw()); doc[section][key] = value
            record = observe.build_record(self.env, json.dumps(doc).encode(), "available")
            self.assertEqual(record["reason"], "scope_mismatch")
            self.assertIsNone(record["observed"])
        self.doc["spec"]["source"]["repoURL"] = "https://other.invalid/" + SECRET
        record = observe.build_record(self.env, self.raw(), "available")
        self.assertEqual(record["reason"], "scope_mismatch")
        self.assertNotIn(SECRET, json.dumps(record))

    def test_malformed_duplicate_unknown_states_and_oversize_are_not_copied(self):
        values = [b'{"status":{},"status":{}}', b'{"value":NaN}', b"not-json-" + SECRET.encode(), b"x" * (observe.MAX_RESPONSE + 1)]
        self.doc["status"]["health"]["status"] = SECRET
        values.append(self.raw())
        for raw in values:
            with self.subTest(size=len(raw)):
                record = observe.build_record(self.env, raw, "available")
                self.assertEqual(record["reason"], "invalid_payload")
                self.assertIsNone(record["observed"])
                self.assertNotIn(SECRET, json.dumps(record))

    def test_missing_observation_and_early_rollback_failure_stay_unknown(self):
        for status in ("unconfigured", "read_failed", "timeout", "too_large"):
            env = dict(self.env, OBS_STAGE="rollback_verification", OBS_EXPECTED_REVISION="", OBS_STARTED_AT="", OBS_FINISHED_AT="")
            record = observe.build_record(env, None, status)
            self.assertEqual(record["verification_result"], "failure")
            self.assertIsNone(record["expected_revision"])
            self.assertEqual(record["collection_status"], "unavailable")
            self.assertEqual(record["reason"], status)

    def cli(self, body):
        path = self.root / "argocd"
        path.write_text("#!/usr/bin/env python3\n" + body)
        path.chmod(0o700)
        return dict(self.env, ARGOCD_CLI=str(path))

    def test_bounded_cli_passes_token_only_in_environment(self):
        body = "import os,sys\nassert os.environ['ARGOCD_AUTH_TOKEN'] not in sys.argv\nprint(" + repr(json.dumps(self.doc)) + ")\n"
        status, raw = observe.read_argo(self.cli(body))
        self.assertEqual(status, "available")
        self.assertEqual(json.loads(raw), self.doc)
        self.assertEqual(observe.read_argo(self.cli("import sys\nprint('private failure',file=sys.stderr)\nsys.exit(1)"))[0], "read_failed")

    def test_cli_timeout_size_and_non_tls_are_bounded(self):
        self.assertEqual(observe.read_argo(self.cli("import time\ntime.sleep(5)"), timeout=0.05)[0], "timeout")
        self.assertEqual(observe.read_argo(self.cli("import sys\nsys.stdout.write('x' * 1100000)"))[0], "too_large")
        with patch.object(observe.subprocess, "Popen", side_effect=AssertionError("호출하면 안 된다")):
            status, raw = observe.read_argo(dict(self.env, ARGOCD_SERVER="http://argocd.validation.invalid"))
        self.assertEqual((status, raw), ("unconfigured", None))

    def test_image_identity_record_separates_local_id_from_registry_digest(self):
        env = dict(self.env, IMAGE_ID="sha256:" + "1" * 64, IMAGE_TAG=f"sha-{SHA}-r123-a2")
        path = self.root / "image.json"
        image_record.write_record(path, env, ROOT)
        record = json.loads(path.read_text())
        self.assertFalse(record["published"])
        self.assertIsNone(record["registry_digest"])
        self.assertIsNone(record["repository"])
        env.update(IMAGE_PUBLISHED="true", IMAGE_REPOSITORY=REPO + "-ai-ops", IMAGE_DIGEST="sha256:" + "2" * 64)
        image_record.write_record(path, env, ROOT)
        record = json.loads(path.read_text())
        self.assertTrue(record["published"])
        self.assertNotEqual(record["image_id"], record["registry_digest"])
        self.assertEqual(record["source_revision"], SHA)
        self.assertNotIn(SECRET, path.read_text())
        env["IMAGE_REPOSITORY"] = REPO
        with self.assertRaises(ValueError):
            image_record.write_record(path, env, ROOT)

    def test_workflow_observation_cannot_hide_or_block_original_failure(self):
        workflow = yaml_file(ROOT / ".github/workflows/ci-cd.yml")
        steps = workflow["jobs"]["build-and-deploy"]["steps"]
        ids = {step["id"]: (index, step) for index, step in enumerate(steps) if "id" in step}
        self.assertTrue(ids["verify-deployment"][1]["continue-on-error"])
        self.assertLess(ids["verify-deployment"][0], ids["observe-deployment"][0])
        self.assertLess(ids["observe-deployment"][0], ids["rollback-deployment"][0])
        self.assertLess(ids["rollback-deployment"][0], ids["observe-rollback"][0])
        for step in steps:
            if step.get("id", "").startswith("observe-") or step.get("uses", "").startswith("actions/upload-artifact@"):
                self.assertTrue(step["continue-on-error"])
                self.assertIn("always()", step["if"])
                self.assertEqual(step["timeout-minutes"], 1)
        rollback = ids["rollback-deployment"][1]
        self.assertNotIn("continue-on-error", rollback)
        self.assertIn("steps.previous-image.outputs.available == 'true'", rollback["if"])
        final = steps[-1]
        self.assertIn("always()", final["if"])
        self.assertIn("steps.verify-deployment.outcome == 'failure'", final["if"])
        self.assertIn("exit 1", final["run"])

    def test_actual_verify_wrapper_preserves_success_and_failure_exit_codes(self):
        steps = yaml_file(ROOT / ".github/workflows/ci-cd.yml")["jobs"]["build-and-deploy"]["steps"]
        verify = next(s for s in steps if s.get("id") == "verify-deployment")["run"]
        for phase, code in [("Succeeded", 0), ("Failed", 1)]:
            status = {"status": {"sync": {"revision": SHA, "status": "Synced"}, "health": {"status": "Healthy"},
                      "operationState": {"phase": phase, "syncResult": {"revision": SHA}}}}
            env = self.cli("print(" + repr(json.dumps(status)) + ")")
            output = self.root / "outputs"; output.write_text("")
            env.update(GITOPS_COMMIT=SHA, GITHUB_OUTPUT=str(output), ARGOCD_APPLICATION="url-shortener", ARGOCD_WAIT_TIMEOUT="1")
            result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", verify], env=env, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertRegex(output.read_text(), r"started_at=.*Z\nfinished_at=.*Z\n")
            self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_registration_workflow_is_manual_default_false_no_gitops_write(self):
        workflow = yaml_file(ROOT / ".github/workflows/ai-ops-image.yml")
        trigger = workflow.get("on", workflow.get("true"))
        self.assertEqual(set(trigger), {"workflow_dispatch"})
        self.assertIs(trigger["workflow_dispatch"]["inputs"]["publish"]["default"], False)
        job = workflow["jobs"]["image"]
        self.assertEqual(job["environment"], "${{ inputs.publish && 'ai-ops-image' || 'ai-ops-image-build' }}")
        self.assertEqual(job["permissions"], {"contents": "read", "id-token": "write"})
        for step in job["steps"]:
            if step.get("uses", "").startswith("aws-actions/") or "docker push" in step.get("run", ""):
                self.assertEqual(step["if"], "inputs.publish")
        scripts = "\n".join(step.get("run", "") for step in job["steps"])
        self.assertIn('"$GITHUB_REF" == refs/heads/main', scripts)
        self.assertNotIn("git push", scripts)
        self.assertNotIn("kubectl", scripts)
        self.assertIn('docker tag "$IMAGE_ID"', scripts)
        self.assertIn('--format \'{{.Id}}\')" == "$IMAGE_ID"', scripts)

    def test_real_rollback_body_keeps_success_failure_and_early_push_failure(self):
        steps = yaml_file(ROOT / ".github/workflows/ci-cd.yml")["jobs"]["build-and-deploy"]["steps"]
        rollback = next(s for s in steps if s.get("id") == "rollback-deployment")["run"]
        final = steps[-1]["run"].replace("${{ steps.previous-image.outputs.available }}", "true")
        fake_bin = self.root / "bin"; fake_bin.mkdir()
        git = fake_bin / "git"
        git.write_text("#!/bin/sh\ncase \"$1\" in\ndiff) exit 1;;\nrev-parse) echo " + ROLLBACK + ";;\npush) exit \"${PUSH_FAILURE:-0}\";;\nesac\n")
        yq = fake_bin / "yq"; yq.write_text("#!/bin/sh\nexit 0\n")
        git.chmod(0o700); yq.chmod(0o700)
        for phase, push_failure, expected in [("Succeeded", "0", 0), ("Failed", "0", 1), ("Succeeded", "1", 1)]:
            with self.subTest(phase=phase, push_failure=push_failure):
                status = {"status": {"sync": {"revision": ROLLBACK, "status": "Synced"}, "health": {"status": "Healthy"},
                    "operationState": {"phase": phase, "syncResult": {"revision": ROLLBACK}}}}
                env = self.cli("print(" + repr(json.dumps(status)) + ")")
                output = self.root / "rollback-outputs"; output.write_text("")
                env.update(PATH=str(fake_bin) + os.pathsep + env["PATH"], GITHUB_REF="refs/heads/main",
                    GITHUB_OUTPUT=str(output), PREVIOUS_TAG="sha-bbbbbbb", ARGOCD_APPLICATION="url-shortener",
                    ARGOCD_WAIT_TIMEOUT="1", PUSH_FAILURE=push_failure)
                result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", rollback], env=env, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)
                if push_failure == "0":
                    self.assertIn("commit=" + ROLLBACK, output.read_text())
                    self.assertIn("finished_at=", output.read_text())
                else:
                    self.assertEqual(output.read_text(), "")
                final_result = subprocess.run(["bash", "-e", "-c", final], env=env, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(final_result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
