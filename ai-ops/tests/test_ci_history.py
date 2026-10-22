"""CI 자료의 다른 실행 혼입·압축 해시·시간·허위 복구 해석을 차단한다."""

import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from ai_ops.ci_history import GitHubReader, MAX_RECORD_BYTES, PREFIX, REPOSITORY, WORKFLOW, inspect_ci_history


HEAD = "a" * 40
BEFORE = "b" * 40
AFTER = "c" * 40
IMAGE = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener:sha-"


def archive(filename, record):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as value:
        value.writestr(filename, json.dumps(record).encode() if isinstance(record, dict) else record)
    return stream.getvalue()


def observation(rollback=False):
    revision = AFTER if rollback else BEFORE
    minute = "02" if rollback else "00"
    return {
        "schema_version": 1, "record_type": "deployment_observation", "repository": REPOSITORY,
        "run_id": "123", "run_attempt": "2", "source_revision": HEAD,
        "stage": "rollback_verification" if rollback else "deployment_verification",
        "verification_result": "success" if rollback else "failure",
        "verification_started_at": f"2026-10-21T01:{minute}:00Z",
        "verification_finished_at": f"2026-10-21T01:{minute}:20Z",
        "observed_at": f"2026-10-21T01:{minute}:21Z",
        "expected_revision": revision, "expected_image": IMAGE + revision[:7],
        "application": "url-shortener", "namespace": "url-shortener", "argo_namespace": "argocd",
        "collection_status": "available", "reason": None,
        "observed": {"revision": revision, "sync_status": "Synced", "health_status": "Healthy",
            "operation_revision": revision, "operation_phase": "Succeeded",
            "reconciled_at": f"2026-10-21T01:{minute}:19Z",
            "images": [IMAGE + revision[:7]], "images_omitted": 0},
    }


class Reader:
    def __init__(self):
        self.run = {"id": 123, "run_attempt": 2, "head_sha": HEAD, "head_branch": "main",
            "path": WORKFLOW, "head_repository": {"full_name": REPOSITORY},
            "repository": {"full_name": REPOSITORY}, "status": "completed", "event": "push",
            "conclusion": "failure", "run_started_at": "2026-10-21T00:59:00Z",
            "updated_at": "2026-10-21T01:03:00Z"}
        self.records = [observation(), observation(True)]
        self.artifacts = []
        self.bodies = {}
        self.calls = []
        self.pack()

    def pack(self):
        self.artifacts.clear()
        self.bodies.clear()
        for index, (suffix, record) in enumerate(zip(("deployment", "rollback"), self.records), start=1):
            raw = archive(suffix + ".json", record)
            self.artifacts.append({"id": index, "name": f"deployment-observation-123-2-{suffix}",
                "expired": False, "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "workflow_run": {"id": 123, "head_sha": HEAD, "head_branch": "main"}})
            self.bodies[index] = raw

    def get(self, path, **kwargs):
        self.calls.append(path)
        if path == PREFIX + "/runs/123":
            return json.dumps(self.run).encode()
        if path == PREFIX + "/runs/123/artifacts?per_page=100":
            return json.dumps({"total_count": len(self.artifacts), "artifacts": self.artifacts}).encode()
        return self.bodies[int(path.split("/")[-2])]


class CIHistoryTest(unittest.TestCase):
    def inspect(self, reader):
        return inspect_ci_history(123, HEAD, reader)

    def test_pair_preserves_failure_and_remains_outside_investigation(self):
        reader = Reader()
        result = self.inspect(reader)
        self.assertEqual(result["pair_status"], "failure_then_rollback_verification_success")
        self.assertEqual(result["records"][0]["verification_result"], "failure")
        self.assertEqual(result["records"][0]["observed"]["health_status"], "Healthy")
        self.assertEqual(result["missing_stages"], [])
        for key in ("cluster_identity_verified", "evidence_import_enabled", "lab_target_compatible", "user_path_recovery_verified"):
            self.assertIs(result[key], False)
        self.assertEqual(len(reader.calls), 4)

    def test_mixed_or_omitted_rollback_images_do_not_prove_recovery(self):
        for changes in ({"images": [IMAGE + AFTER[:7], IMAGE + BEFORE[:7]]}, {"images_omitted": 1}):
            with self.subTest(changes=changes):
                reader = Reader()
                record = observation(rollback=True)
                record["observed"].update(changes)
                reader.bodies[2] = archive("rollback.json", record)
                reader.artifacts[1]["digest"] = "sha256:" + hashlib.sha256(reader.bodies[2]).hexdigest()
                self.assertEqual(self.inspect(reader)["pair_status"], "inconclusive")

    def test_wrong_run_head_workflow_branch_repo_event_or_attempt_rejected_before_archive(self):
        cases = [("id", 124), ("id", True), ("head_sha", BEFORE), ("path", ".github/workflows/other.yml"),
            ("head_branch", "other"), ("head_repository", {"full_name": "other/repo"}),
            ("repository", {"full_name": "other/repo"}), ("event", "pull_request"),
            ("status", "in_progress"), ("run_attempt", True), ("conclusion", "skipped")]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                reader = Reader(); reader.run[key] = value
                with self.assertRaises(ValueError):
                    self.inspect(reader)
                self.assertEqual(len(reader.calls), 1)

    def test_metadata_identity_or_digest_mismatch_rejected(self):
        for key, value in [("digest", "sha256:" + "0" * 64), ("digest", None), ("expired", None),
                           ("size_in_bytes", 1048577), ("id", True)]:
            with self.subTest(key=key):
                reader = Reader(); reader.artifacts[0][key] = value
                with self.assertRaises(ValueError):
                    self.inspect(reader)
        for key, value in [("id", 124), ("head_sha", BEFORE), ("head_branch", "other")]:
            reader = Reader(); reader.artifacts[0]["workflow_run"][key] = value
            with self.assertRaises(ValueError):
                self.inspect(reader)

    def test_old_attempt_expired_missing_and_conflicting_artifacts(self):
        for variation in ("old", "expired", "missing"):
            reader = Reader()
            if variation == "old":
                reader.artifacts[1]["name"] = "deployment-observation-123-1-rollback"
            elif variation == "expired":
                reader.artifacts[1]["expired"] = True
            else:
                reader.artifacts.pop()
            result = self.inspect(reader)
            self.assertEqual(result["pair_status"], "incomplete")
            self.assertEqual(result["missing_stages"], ["rollback_verification"])
            self.assertEqual(len(reader.calls), 3)
        reader = Reader(); reader.artifacts.append(copy.deepcopy(reader.artifacts[0]))
        with self.assertRaises(ValueError):
            self.inspect(reader)

    def test_incomplete_paginated_listing_is_rejected(self):
        reader = Reader()
        original = reader.get
        def get(path, **kwargs):
            if "per_page" in path:
                return json.dumps({"total_count": 101, "artifacts": reader.artifacts}).encode()
            return original(path, **kwargs)
        reader.get = get
        with self.assertRaises(ValueError):
            self.inspect(reader)

    def test_zip_paths_extra_members_symlinks_and_large_records_rejected(self):
        bodies = [archive("../deployment.json", {}), archive("deployment.json", b"x" * (MAX_RECORD_BYTES + 1))]
        for symlink in (False, True):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as value:
                if symlink:
                    entry = zipfile.ZipInfo("deployment.json")
                    entry.create_system = 3
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                    value.writestr(entry, "outside")
                else:
                    value.writestr("deployment.json", "{}")
                    value.writestr("private.log", "secret")
            bodies.append(stream.getvalue())
        for raw in bodies:
            reader = Reader(); reader.bodies[1] = raw
            reader.artifacts[0]["digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
            with self.assertRaises(ValueError):
                self.inspect(reader)

    def test_record_run_attempt_scope_unknown_fields_and_duplicate_json_rejected(self):
        for key, value in [("run_id", "124"), ("run_attempt", "1"), ("source_revision", BEFORE),
                           ("namespace", "ai-ops-lab"), ("stage", "rollback_verification"),
                           ("private_message", "secret"), ("verification_result", "healthy"),
                           ("schema_version", True)]:
            reader = Reader(); reader.records[0][key] = value; reader.pack()
            with self.assertRaises(ValueError):
                self.inspect(reader)
        reader = Reader(); reader.records[0] = b'{"schema_version":1,"schema_version":1}'; reader.pack()
        with self.assertRaises(ValueError):
            self.inspect(reader)

    def test_invalid_time_and_final_outcome_conflict_rejected(self):
        for key, value in [("observed_at", "2026-10-21T03:00:00Z"),
                           ("verification_started_at", "2026-10-21T00:00:00Z"),
                           ("verification_finished_at", "2026-10-21T00:00:00Z"),
                           ("verification_finished_at", "2026-10-21T10:00:20+09:00")]:
            reader = Reader(); reader.records[0][key] = value; reader.pack()
            with self.assertRaises(ValueError):
                self.inspect(reader)
        reader = Reader(); reader.run["conclusion"] = "success"
        with self.assertRaises(ValueError):
            self.inspect(reader)

    def test_incomplete_early_rollback_or_unavailable_observation_not_recovered(self):
        for variation in ("early", "unavailable", "failed", "same_image", "same_revision", "reversed"):
            reader = Reader(); record = reader.records[1]
            if variation == "early":
                record.update(verification_started_at=None, verification_finished_at=None, expected_revision=None)
            elif variation == "unavailable":
                record.update(collection_status="unavailable", reason="timeout", observed=None)
            elif variation == "failed":
                record["verification_result"] = "failure"
            elif variation == "same_image":
                record["expected_image"] = reader.records[0]["expected_image"]
            elif variation == "same_revision":
                record["expected_revision"] = reader.records[0]["expected_revision"]
            else:
                record["verification_started_at"] = "2026-10-21T01:00:00Z"
            reader.pack()
            self.assertEqual(self.inspect(reader)["pair_status"], "inconclusive", variation)

    def test_healthy_without_matching_revision_image_operation_or_time_is_inconclusive(self):
        for key, value in [("revision", BEFORE), ("operation_revision", BEFORE),
                           ("images", [IMAGE + BEFORE[:7]]), ("operation_phase", "Failed"),
                           ("sync_status", "OutOfSync"), ("health_status", "Degraded"),
                           ("reconciled_at", "2026-10-21T01:00:19Z")]:
            reader = Reader(); reader.records[1]["observed"][key] = value; reader.pack()
            self.assertEqual(self.inspect(reader)["pair_status"], "inconclusive", key)

    def test_missing_stage_success_run_and_unavailable_fields_are_preserved(self):
        reader = Reader(); reader.records = reader.records[:1]
        reader.records[0].update(verification_result="success", collection_status="unavailable", reason="read_failed", observed=None)
        reader.run["conclusion"] = "success"; reader.pack()
        result = self.inspect(reader)
        self.assertEqual(result["pair_status"], "incomplete")
        self.assertEqual(result["records"][0]["reason"], "read_failed")

    def test_invalid_identifiers_or_api_paths_never_start_gh(self):
        for run_id, head in [(True, HEAD), (-1, HEAD), (123, "main"), (123, HEAD + "/other")]:
            reader = Reader()
            with self.assertRaises(ValueError):
                inspect_ci_history(run_id, head, reader)
            self.assertEqual(reader.calls, [])
        with patch("ai_ops.ci_history.subprocess.Popen") as start:
            for path in ("https://other.invalid", PREFIX + "/runs/123?other=true", "repos/other/repo/actions/runs/123"):
                with self.assertRaises(ValueError):
                    GitHubReader().get(path)
            start.assert_not_called()

    def test_reader_bounds_binary_response_failure_size_and_deadline(self):
        # 실제 자식 프로세스를 사용하지만 gh와 네트워크 대신 개인 임시 스크립트만 실행한다.
        original = subprocess.Popen
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fake_gh.py"
            cases = [("import sys;sys.stdout.buffer.write(b'PK\\x03\\x04')", 10, b"PK\x03\x04"),
                     ("import sys;sys.exit(1)", 10, None),
                     ("print('x' * 100)", 10, None),
                     ("import time;time.sleep(5)", 10, None)]
            for body, limit, expected in cases:
                script.write_text(body)
                def start(command, **kwargs):
                    self.assertEqual(command[:4], ["gh", "api", "--hostname", "github.com"])
                    self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                    return original([sys.executable, str(script)], **kwargs)
                with patch("ai_ops.ci_history.subprocess.Popen", side_effect=start), \
                     patch("ai_ops.ci_history.READ_TIMEOUT_SECONDS", 0.15):
                    if expected is None:
                        with self.assertRaises(ValueError):
                            GitHubReader().get(PREFIX + "/runs/123", max_bytes=limit)
                    else:
                        self.assertEqual(GitHubReader().get(PREFIX + "/runs/123", max_bytes=limit), expected)


if __name__ == "__main__":
    unittest.main()
