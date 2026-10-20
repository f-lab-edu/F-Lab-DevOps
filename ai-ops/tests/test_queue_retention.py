"""연계 삭제의 기본 미리보기·소유권·재시작·중복 방지 경계를 검증한다."""

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import patch
import unittest

from ai_ops.cli import main
from ai_ops.job_queue import JobQueue, QueueBusy, RETENTION_SECONDS
from ai_ops.retention import QueueRetention
import test_job_queue as queue_tests


class QueueRetentionTest(unittest.TestCase):
    setUp = queue_tests.JobQueueTest.setUp
    submit = queue_tests.JobQueueTest.submit
    worker = queue_tests.JobQueueTest.worker

    def complete(self, key="request-1"):
        job, _ = self.submit(key)
        self.assertEqual(self.worker().run_once()["state"], "partial")
        return job

    def expire(self):
        self.time = 1000 + RETENTION_SECONDS

    def retention(self, **options):
        return QueueRetention(self.queue).run(**options)

    def test_default_preview_lists_deadline_without_question_or_deletion(self):
        job = self.complete()
        self.expire()
        before = {str(p): p.read_bytes() for p in self.store.glob("inc-*/run-*/*")}
        result = self.retention()
        self.assertEqual(result["mode"], "preview")
        self.assertEqual(result["entries"][0]["action"], "would_purge")
        self.assertEqual(result["entries"][0]["linked_runs"], 1)
        self.assertNotIn("question", json.dumps(result))
        self.assertNotIn("private-secret", json.dumps(result))
        self.assertEqual({str(p): p.read_bytes() for p in self.store.glob("inc-*/run-*/*")}, before)
        self.assertEqual(self.queue.show(job)["state"], "partial")
        with self.queue.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM purges").fetchone()[0], 0)

    def test_expired_apply_deletes_linked_data_and_keeps_dedup_and_budget(self):
        job = self.complete()
        old = self.queue.show(job)
        other = self.queue.engine.investigate_snapshot(snapshot=self.snapshot, settings=self.settings,
                    question="별도 수동 조사", request_id="unrelated-manual-request")
        protected = {"ai-budget.json": b'{"days":{"today":1}}', ".openai-api-key": b"key-sentinel",
                     ".telemetry.sqlite3": (self.store / ".telemetry.sqlite3").read_bytes(),
                     ".fingerprint-key": (self.store / ".fingerprint-key").read_bytes()}
        for name, content in protected.items():
            path = self.store / name
            path.write_bytes(content)
            path.chmod(0o600)
        self.expire()
        result = self.retention(apply=True, job_id=job)
        self.assertEqual(result["entries"][0]["action"], "purged")
        self.assertEqual(self.queue.show(job)["state"], "purged")
        self.assertFalse((self.store / old["incident_id"] / old["run_id"]).exists())
        self.assertFalse((self.queue.directory / (job + ".json")).exists())
        self.assertFalse(self.queue.engine._index_path("queue-" + job[4:] + "-a1").exists())
        self.assertIsNotNone(self.queue.engine.show(other.incident_id, other.run_id).report)
        for name, content in protected.items():
            self.assertEqual((self.store / name).read_bytes(), content)
        with self.queue.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM episodes").fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "삭제"):
            self.submit()
        self.assertEqual(self.retention(apply=True, job_id=job)["entries"][0]["action"], "already_purged")

    def test_before_deadline_keeps_data_and_retry_does_not_extend_deadline(self):
        job = self.complete()
        initial = self.queue.show(job)["retention_deadline"]
        self.time += RETENTION_SECONDS - 1
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "keep")
        self.assertEqual(self.queue.show(job)["retention_deadline"], initial)
        self.assertEqual(self.submit(), (job, True))

    def test_unfinished_and_expired_lease_are_not_forcibly_deleted(self):
        job, _ = self.submit()
        self.expire()
        self.assertEqual(self.retention(apply=True)["entries"][0]["reason"], "unfinished_job")
        self.queue.claim()
        self.time += 61
        self.assertEqual(self.retention(apply=True)["entries"][0]["reason"], "unfinished_job")
        self.assertEqual(self.queue.show(job)["state"], "collecting")

    def test_queued_cancelled_job_without_report_can_expire(self):
        job, _ = self.submit()
        self.queue.cancel(job)
        self.expire()
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "purged")

    def test_same_store_worker_lock_blocks_cleanup(self):
        self.complete()
        self.expire()
        with self.queue.worker_lock(), self.assertRaises(QueueBusy):
            self.retention(apply=True)

    def test_symlink_and_unknown_file_block_before_deletion(self):
        job = self.complete()
        old = self.queue.show(job)
        run = self.store / old["incident_id"] / old["run_id"]
        foreign = self.root / "foreign"
        foreign.write_text("preserve")
        (run / "extra").symlink_to(foreign)
        self.expire()
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "blocked")
        self.assertEqual(foreign.read_text(), "preserve")
        self.assertTrue((run / "report.md").exists())

    def test_foreign_request_index_is_not_deleted(self):
        job = self.complete()
        path = self.queue.engine._index_path("queue-" + job[4:] + "-a1")
        original = path.read_text()
        path.write_text(original.replace("run-", "run-0", 1))
        self.expire()
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "blocked")
        self.assertTrue(path.exists())
        self.assertEqual(self.queue.show(job)["state"], "partial")

    def test_unattributed_snapshot_temporary_file_blocks_cleanup(self):
        job = self.complete()
        temporary = self.queue.directory / ".tmp-interrupted"
        temporary.write_text("unattributed evidence")
        temporary.chmod(0o600)
        self.expire()
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "blocked")
        self.assertEqual(self.queue.show(job)["state"], "partial")
        self.assertEqual(temporary.read_text(), "unattributed evidence")

    def test_v1_migration_preserves_jobs_and_key_and_publishes_version_two(self):
        job, _ = self.submit()
        key = (self.store / ".fingerprint-key").read_bytes()
        with self.queue.transaction() as db:
            db.execute("DROP TABLE purges")
            db.execute("PRAGMA user_version=1")
        reopened = JobQueue(self.store)
        self.assertEqual(reopened.show(job)["state"], "queued")
        self.assertEqual((self.store / ".fingerprint-key").read_bytes(), key)
        self.assertTrue(self.submit()[1])
        with reopened.transaction() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_failed_attempts_are_all_deleted_not_only_last_report(self):
        job, _ = self.submit()
        with patch("ai_ops.incidents.analyze_snapshot_rules", side_effect=RuntimeError("analysis failed")):
            for _ in range(3):
                self.worker().run_once()
                self.time += 30
        self.assertEqual(self.queue.show(job)["state"], "failed")
        self.assertEqual(len(list(self.store.glob("inc-*/run-*/metadata.json"))), 3)
        self.expire()
        result = self.retention(apply=True)
        self.assertEqual(result["entries"][0]["linked_runs"], 3)
        self.assertEqual(result["entries"][0]["action"], "purged")
        self.assertFalse(list(self.store.glob("inc-*/run-*/metadata.json")))

    def test_missing_request_index_does_not_orphan_owned_run(self):
        job = self.complete()
        self.queue.engine._index_path("queue-" + job[4:] + "-a1").unlink()
        self.expire()
        result = self.retention(apply=True)
        self.assertEqual(result["entries"][0]["action"], "purged")
        self.assertFalse(list(self.store.glob("inc-*/run-*/metadata.json")))

    def test_tampered_restart_plan_cannot_escape_store(self):
        job = self.complete()
        self.expire()
        with patch("ai_ops.retention.unlink_private", side_effect=OSError("stop")):
            self.retention(apply=True)
        with self.queue.transaction() as db:
            plan = json.loads(db.execute("SELECT plan FROM purges WHERE job_id=?", (job,)).fetchone()[0])
            plan["plan"]["runs"][0]["run_id"] = "../../foreign"
            db.execute("UPDATE purges SET plan=? WHERE job_id=?", (json.dumps(plan), job))
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "blocked")
        self.assertTrue(list((self.queue.directory / "expired" / job).glob("*/report.md")))

    def test_partial_filesystem_failure_is_resumable_without_resurrection(self):
        job = self.complete()
        self.expire()
        with patch("ai_ops.retention.unlink_private", side_effect=OSError("private-error")):
            result = self.retention(apply=True)
        self.assertEqual(result["entries"][0]["action"], "blocked")
        self.assertNotIn("private-error", json.dumps(result))
        self.assertEqual(self.queue.show(job)["state"], "purging")
        with self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "purged")
        self.assertEqual(self.queue.show(job)["state"], "purged")

    def test_actual_process_exit_after_staging_resumes_same_plan(self):
        job = self.complete()
        self.expire()
        code = '''from pathlib import Path
import os,sys
from ai_ops.job_queue import JobQueue
from ai_ops.retention import QueueRetention
import ai_ops.retention as module
q=JobQueue(Path(sys.argv[1]),clock=lambda:float(sys.argv[2]))
original=module.unlink_private
count=0
def interrupted(path):
 global count
 original(path);count+=1
 if count==4:os._exit(73)
module.unlink_private=interrupted
QueueRetention(q).run(apply=True)
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.store), str(self.time)], capture_output=True)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual(self.queue.show(job)["state"], "purging")
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "purged")
        self.assertFalse(list(self.store.glob("inc-*/run-*/metadata.json")))

    def test_files_deleted_but_database_commit_failed_can_resume(self):
        job = self.complete()
        self.expire()
        retention = QueueRetention(self.queue)
        original = retention._delete
        failure = patch.object(self.queue, "transaction", side_effect=sqlite3.OperationalError("commit failed"))
        def delete_then_fail(plan):
            original(plan)
            failure.start()
        try:
            with patch.object(retention, "_delete", side_effect=delete_then_fail), self.assertRaises(sqlite3.Error):
                retention.run(apply=True)
        finally:
            failure.stop()
        self.assertEqual(self.queue.show(job)["state"], "purging")
        self.assertEqual(self.retention(apply=True)["entries"][0]["action"], "purged")

    def test_cli_default_preview_and_apply_blocked_exit_code(self):
        job, _ = self.submit()
        args = ["queue-retention", "--store", str(self.store), "--job", job]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "preview")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([*args, "--apply"]), 2)
        self.assertEqual(json.loads(output.getvalue())["entries"][0]["reason"], "unfinished_job")


if __name__ == "__main__":
    unittest.main()
