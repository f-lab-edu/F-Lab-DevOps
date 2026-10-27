"""정상 CI의 GitHub 출처·중복·큐 무결성과 worker의 정제 처리 경계를 검사한다."""

from datetime import timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from ai_ops.ci_history import NORMAL_RUNS_PATH, PREFIX
from ai_ops.ci_relay import CIRelay
from ai_ops.contracts import canonical_json_bytes
from ai_ops.job_queue import JobQueue
from ai_ops.normal_ci_history import verify_normal_ci_history
from ai_ops.normal_ci_inbox import NormalCIInbox
from test_normal_ci import fixture


class Reader:
    def __init__(self, receipt):
        self.receipt = receipt
        self.run = {"id": 12345, "run_attempt": 1, "head_sha": "a"*40,
            "head_branch": "main", "path": ".github/workflows/ai-ops-normal-ci.yml",
            "repository": {"full_name": "f-lab-edu/F-Lab-DevOps", "id": 99},
            "head_repository": {"full_name": "f-lab-edu/F-Lab-DevOps", "id": 99},
            "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
            "run_started_at": (receipt.started_at-timedelta(seconds=1)).isoformat(),
            "updated_at": (receipt.finished_at+timedelta(seconds=1)).isoformat()}
        self.artifact = {"id": 88, "name": "b6-normal-observation-12345-1", "expired": False,
            "workflow_run": {"id": 12345, "head_sha": "a"*40, "head_branch": "main",
                             "repository_id": 99, "head_repository_id": 99}}
        self.extra = None
        self.corrupt_digest = False

    def get(self, path, **_kwargs):
        if path == NORMAL_RUNS_PATH:
            return canonical_json_bytes({"total_count": 1, "workflow_runs": [self.run]})
        if path == f"{PREFIX}/runs/12345":
            return canonical_json_bytes(self.run)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr("result.json", self.receipt.model_dump_json())
            if self.extra:
                out.writestr(self.extra, "{}")
        raw = archive.getvalue()
        if path == f"{PREFIX}/runs/12345/artifacts?per_page=100":
            artifact = {**self.artifact, "size_in_bytes": len(raw),
                "digest": "sha256:" + ("0"*64 if self.corrupt_digest else hashlib.sha256(raw).hexdigest())}
            return canonical_json_bytes({"total_count": 1, "artifacts": [artifact]})
        if path == f"{PREFIX}/artifacts/88/zip":
            return raw
        raise AssertionError(path)


class NormalCIHandoffTest(unittest.TestCase):
    def setUp(self):
        _, self.settings, _, self.binding, _, self.receipt = fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        env.start(); self.addCleanup(env.stop)
        self.queue = JobQueue(self.root / "store")
        self.reader = Reader(self.receipt)

    def history(self):
        return verify_normal_ci_history(12345, "a"*40, self.binding, reader=self.reader)

    def test_realistic_provenance_to_worker_summary_is_durable_and_model_free(self):
        relay = CIRelay(self.queue, self.settings, reader=self.reader,
                        normal_binding=self.binding, clock=lambda: self.receipt.finished_at+timedelta(seconds=30))
        self.assertEqual(relay.scan_normal_once()["submitted"], 1)
        inbox = NormalCIInbox(JobQueue(self.root / "store"), self.binding)
        with patch("ai_ops.normal_ci_history.GitHubReader.get", side_effect=AssertionError("worker GitHub 금지")):
            self.assertEqual(inbox.process_one()["state"], "ready")
        value = inbox.status("12345", "1")
        self.assertEqual(value["state"], "ready")
        self.assertEqual(value["summary"]["record_type"], "normal_ci_rules_summary")
        self.assertEqual(value["summary"]["external_model_calls"], 0)
        self.assertEqual(relay.scan_normal_once()["duplicates"], 1)
        self.assertEqual(inbox.process_one(), None)
        self.assertNotIn("raw_logs", json.dumps(value))

    def test_run_artifact_and_binding_conflicts_are_rejected(self):
        for target, key, value in (("run", "conclusion", "failure"),
                                   ("run", "path", ".github/workflows/ai-ops-lab-ci.yml"),
                                   ("run", "head_branch", "feature"),
                                   ("run", "event", "push"),
                                   ("artifact", "name", "b6-lab-observation-12345-1"),
                                   ("artifact", "expired", True)):
            reader = Reader(self.receipt)
            getattr(reader, target)[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_normal_ci_history(12345, "a"*40, self.binding, reader=reader)
        for change in (lambda reader: setattr(reader, "corrupt_digest", True),
                       lambda reader: setattr(reader, "extra", "../unexpected.json"),
                       lambda reader: reader.artifact["workflow_run"].update(repository_id=42)):
            reader = Reader(self.receipt); change(reader)
            with self.assertRaises(ValueError):
                verify_normal_ci_history(12345, "a"*40, self.binding, reader=reader)
        changed = self.binding.model_copy(update={"cluster_fingerprint": "0"*64})
        with self.assertRaises(ValueError):
            verify_normal_ci_history(12345, "a"*40, changed, reader=Reader(self.receipt))

    def test_tamper_or_conflicting_duplicate_never_becomes_ready(self):
        inbox = NormalCIInbox(self.queue, self.binding)
        history = self.history()
        key, duplicate = inbox.submit(history)
        self.assertFalse(duplicate)
        changed = history.__class__(history.run_id, history.run_attempt, "89",
                                    history.archive_digest, history.source_revision, history.receipt)
        with self.assertRaises(ValueError): inbox.submit(changed)
        with self.queue.transaction() as db:
            db.execute("UPDATE normal_ci_receipts SET payload=? WHERE episode_key=?", ('{"forged":true}', key))
        self.assertEqual(inbox.process_one()["state"], "needs_review")
        self.assertIsNone(inbox.status("12345", "1")["summary"])


if __name__ == "__main__":
    unittest.main()
