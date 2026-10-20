"""영속 큐의 중복·lease·프로세스 중단·완료 파일 조정 경계를 검증한다."""

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_ops.b_contracts import load_b_settings
from ai_ops.cli import main
from ai_ops.job_queue import JobQueue, LeaseLost, QueueBusy
from ai_ops.worker import Worker, snapshot_value
from ai_ops.collection import CapturedSnapshot, CollectionAudit
from ai_ops.contracts import parse_evidence_bundle_json, canonical_json_bytes
from ai_ops.live_collectors import collect_snapshot
from test_live_collectors import API, Clock

ROOT = Path(__file__).resolve().parents[1]


class JobQueueTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.store = self.root / "store"
        self.time = 1000.0
        self.queue = JobQueue(self.store, clock=lambda: self.time)
        self.settings = load_b_settings(ROOT / "config/targets.practice.example.json",
            ROOT / "config/b-runtime.example.json", ROOT / "config/metric-queries.json")
        self.target = self.settings.bindings.bindings[0]
        clock = Clock()
        api = API(self.settings, clock)
        api.docs["deployment"]["spec"]["replicas"] = 1
        self.snapshot = collect_snapshot(self.settings, self.target.target, transport=api, clock=clock)
        self.quiet = contextlib.redirect_stderr(io.StringIO())
        self.quiet.__enter__(); self.addCleanup(self.quiet.__exit__, None, None, None)

    def submit(self, key="request-1", **changes):
        options = {"target": self.target.alias, "question": "현재 상태 token=private-secret-value", "request_id": key}
        options.update(changes)
        return self.queue.submit(self.settings, **options)

    def worker(self, collector=None):
        return Worker(self.queue, self.settings, collector=collector or (lambda *a, **kw: self.snapshot))

    def test_private_storage_duplicate_conflict_and_reopen(self):
        job, duplicate = self.submit()
        self.assertFalse(duplicate)
        self.assertEqual(self.submit(), (job, True))
        with self.assertRaisesRegex(ValueError, "다른 내용"):
            self.submit(question="다른 질문")
        with self.assertRaises(ValueError):
            self.submit(target="foreign-target")
        with self.assertRaises(ValueError):
            self.submit(route="/unregistered")
        self.assertEqual(JobQueue(self.store).show(job)["state"], "queued")
        self.assertEqual(self.queue.path.stat().st_mode & 0o777, 0o600)
        with self.queue.transaction() as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], 2)
            payload = db.execute("SELECT payload FROM jobs").fetchone()[0]
            self.assertNotIn("private-secret-value", payload)
            self.assertEqual(db.execute("SELECT count(*) FROM episodes").fetchone()[0], 1)
        self.assertNotIn("question", json.dumps(self.queue.show(job)))

    def test_queue_capacity_dedup_still_works_and_failed_commit_not_accepted(self):
        for index in range(100):
            self.submit("request-" + str(index))
        self.assertTrue(self.submit("request-0")[1])
        self.assertIsNotNone(self.queue.claim())
        with self.assertRaisesRegex(ValueError, "가득"):
            self.submit("overflow")
        with self.queue.transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs").fetchone()[0], 100)
        with patch.object(self.queue, "transaction", side_effect=sqlite3.OperationalError("disk-error")):
            with self.assertRaises(sqlite3.Error):
                self.submit("not-accepted")

    def test_rules_partial_maps_to_job_report_and_show_without_model(self):
        job, _ = self.submit()
        with patch("ai_ops.cli.analyze_ai", side_effect=AssertionError("모델 호출 금지")), \
                patch.object(self.queue.engine, "load_api_key", side_effect=AssertionError("키 조회 금지")):
            result = self.worker().run_once()
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["attempts"], 1)
        record = self.queue.engine.show(result["incident_id"], result["run_id"])
        self.assertEqual(record.report.execution.mode, "rules")
        self.assertEqual(record.bundle.bundle_hash, result["bundle_hash"])
        self.assertEqual(self.submit(), (job, True))
        self.assertIsNone(self.worker().run_once())

    def test_failed_collection_backoff_and_maximum_three_attempts(self):
        job, _ = self.submit()
        worker = self.worker(collector=lambda *a, **kw: (_ for _ in ()).throw(OSError("secret-upstream")))
        for number in range(1, 4):
            result = worker.run_once()
            self.assertEqual(result["attempts"], number)
            self.assertEqual(result["state"], "failed" if number == 3 else "queued")
            self.assertNotIn("secret-upstream", json.dumps(result))
            self.assertIsNone(worker.run_once())
            self.time += 30
        self.assertEqual(self.queue.show(job)["attempt_history"][1]["parent_generation"], 1)
        self.assertEqual(len({item[0] for item in self.queue_rows("SELECT request_id FROM attempts")}), 3)

    def queue_rows(self, sql):
        with self.queue.transaction() as db:
            return db.execute(sql).fetchall()

    def test_generation_fencing_expired_lease_and_heartbeat(self):
        job, _ = self.submit()
        first = self.queue.claim()
        self.assertIsNone(self.queue.claim())
        self.time += 40
        self.queue.heartbeat(first)
        self.time += 30
        self.assertEqual(self.queue.expired(), [])
        self.time += 31
        with self.assertRaises(LeaseLost):
            self.queue.heartbeat(first)
        self.worker().recover()
        self.time += 6
        second = self.queue.claim()
        self.assertEqual(second["generation"], 2)
        with self.assertRaises(LeaseLost):
            self.queue.checkpoint(first, "0" * 64)
        self.assertEqual(self.queue.show(job)["generation"], 2)

    def test_cancel_queued_and_active_prevents_late_report(self):
        job, _ = self.submit()
        self.queue.cancel(job)
        self.assertIsNone(self.worker().run_once())
        self.assertEqual(self.submit(), (job, True))
        job, _ = self.submit("active")
        def collect(*a, **kw):
            self.queue.cancel(job)
            return self.snapshot
        result = self.worker(collect).run_once()
        self.assertEqual(result["state"], "cancelled")
        self.assertIsNone(result["run_id"])
        self.assertFalse(list(self.store.glob("inc-*")))

    def test_config_change_holds_for_review_before_network(self):
        job, _ = self.submit()
        changed = self.settings.__class__(self.settings.bindings, self.settings.runtime, self.settings.metrics, "0" * 64)
        with patch("socket.create_connection", side_effect=AssertionError("실제 통신 금지")):
            result = Worker(self.queue, changed).run_once()
        self.assertEqual(result["state"], "needs_review")
        self.assertEqual(result["reason"], "config_changed")
        self.assertIsNone(self.worker().run_once())

    def test_worker_lock_is_exclusive_in_another_process(self):
        code = "from pathlib import Path; from ai_ops.job_queue import JobQueue,QueueBusy; import sys\nq=JobQueue(Path(sys.argv[1]))\ntry:\n with q.worker_lock(): sys.exit(3)\nexcept QueueBusy: sys.exit(0)\n"
        with self.queue.worker_lock():
            result = subprocess.run([sys.executable, "-c", code, str(self.store)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.queue.worker_lock():
            pass

    def crash_process(self, phase):
        path = self.root / "snapshot.json"
        path.write_bytes(canonical_json_bytes(snapshot_value(self.snapshot)))
        code = '''from pathlib import Path
import json,os,sys
from ai_ops.b_contracts import load_b_settings
from ai_ops.job_queue import JobQueue
from ai_ops.worker import Worker
from ai_ops.collection import CapturedSnapshot,CollectionAudit
from ai_ops.contracts import parse_evidence_bundle_json,canonical_json_bytes
r=Path(sys.argv[1]);q=JobQueue(Path(sys.argv[2]),clock=lambda:1000.0)
s=load_b_settings(r/'config/targets.practice.example.json',r/'config/b-runtime.example.json',r/'config/metric-queries.json')
v=json.loads(Path(sys.argv[3]).read_text());snapshot=CapturedSnapshot(parse_evidence_bundle_json(canonical_json_bytes(v['bundle'])),CollectionAudit.model_validate(v['audit']))
w=Worker(q,s,collector=lambda *a,**kw:snapshot)
if sys.argv[4]=='claimed':
 with q.worker_lock():
  q.claim();os._exit(73)
elif sys.argv[4]=='report_saved':
 q.finish=lambda *a:os._exit(73)
 w.run_once()
elif sys.argv[4]=='checkpoint':
 q.engine.investigate_snapshot=lambda **kw:os._exit(73)
 w.run_once()
'''
        result = subprocess.run([sys.executable, "-c", code, str(ROOT), str(self.store), str(path), phase], capture_output=True)
        self.assertEqual(result.returncode, 73, result.stderr)

    def test_process_killed_after_claim_retries_with_new_attempt(self):
        job, _ = self.submit()
        self.crash_process("claimed")
        self.assertEqual(self.queue.show(job)["state"], "collecting")
        self.time = 1061
        self.assertIsNone(self.worker().run_once())
        self.time += 6
        result = self.worker().run_once()
        self.assertEqual((result["state"], result["attempts"]), ("partial", 2))

    def test_process_killed_after_checkpoint_reuses_frozen_input(self):
        job, _ = self.submit()
        self.crash_process("checkpoint")
        self.time = 1061
        self.worker().run_once()
        self.time += 6
        def no_collect(*a, **kw):
            raise AssertionError("저장 증거를 다시 수집하면 안 됩니다")
        result = self.worker(no_collect).run_once()
        self.assertEqual((result["state"], result["attempts"]), ("partial", 2))

    def test_report_saved_before_queue_commit_recovers_without_new_collection_or_run(self):
        job, _ = self.submit()
        self.crash_process("report_saved")
        self.assertEqual(self.queue.show(job)["state"], "analyzing")
        self.assertEqual(len(list(self.store.glob("inc-*/run-*"))), 1)
        self.time = 1061
        def no_collect(*a, **kw):
            raise AssertionError("완료 파일이 있으면 재수집하면 안 됩니다")
        self.assertIsNone(self.worker(no_collect).run_once())
        result = self.queue.show(job)
        self.assertEqual((result["state"], result["attempts"]), ("partial", 1))
        self.assertEqual(len(list(self.store.glob("inc-*/run-*"))), 1)

    def test_tampered_checkpoint_or_report_is_held_for_review(self):
        job, _ = self.submit()
        self.crash_process("report_saved")
        report = next(self.store.glob("inc-*/run-*/report.md"))
        report.write_text("변조된 보고서")
        self.time = 1061
        self.worker().run_once()
        self.assertEqual(self.queue.show(job)["state"], "needs_review")
        self.assertEqual(self.queue.show(job)["attempts"], 1)

    def test_save_then_queue_error_waits_for_recovery_instead_of_recollecting(self):
        job, _ = self.submit()
        with patch.object(self.queue, "finish", side_effect=RuntimeError("queue-commit-interrupted")):
            result = self.worker().run_once()
        self.assertEqual(result["state"], "analyzing")
        self.time += 61
        self.worker().run_once()
        self.assertEqual(self.queue.show(job)["state"], "partial")
        self.assertEqual(self.queue.show(job)["attempts"], 1)

    def test_corrupt_checkpoint_stops_retries_and_graceful_stop_preserves_checkpoint(self):
        job, _ = self.submit()
        stopped = threading.Event()
        def collect(*a, **kw):
            stopped.set()
            return self.snapshot
        result = Worker(self.queue, self.settings, collector=collect, stopped=stopped).run_once()
        self.assertEqual(result["state"], "queued")
        self.assertEqual(result["reason"], "operator_stopped")
        path = self.queue.directory / (job + ".json")
        self.assertTrue(path.exists())
        path.write_text('{"modified":true}')
        self.time += 6
        result = self.worker().run_once()
        self.assertEqual(result["state"], "needs_review")
        self.assertEqual(result["reason"], "integrity_error")

    def test_unsupported_schema_and_symlink_rejected(self):
        with self.queue.transaction() as db:
            db.execute("PRAGMA user_version=3")
        with self.assertRaises(ValueError):
            JobQueue(self.store)
        outside = self.root / "preserve.sqlite3"
        outside.write_text("preserve")
        self.queue.path.unlink()
        self.queue.path.symlink_to(outside)
        with self.assertRaises(OSError):
            JobQueue(self.store)
        self.assertEqual(outside.read_text(), "preserve")

    def test_cli_submit_show_cancel_and_storage_failure(self):
        args = ['queue-submit','--bindings',str(ROOT/'config/targets.practice.example.json'),
            '--runtime',str(ROOT/'config/b-runtime.example.json'),'--metric-queries',str(ROOT/'config/metric-queries.json'),
            '--target',self.target.alias,'--question','상태 확인 token=private-secret-value','--request-id','cli-job','--store',str(self.store)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args), 0)
        result = json.loads(out.getvalue())
        self.assertFalse(result['duplicate'])
        self.assertNotIn('private-secret-value', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(['queue-cancel','--store',str(self.store),'--job',result['job_id']]), 0)
        self.assertEqual(json.loads(out.getvalue())['state'], 'cancelled')
        with patch('ai_ops.cli.JobQueue', side_effect=sqlite3.OperationalError('private-error')), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(args), 2)
        self.assertNotIn('private-error', err.getvalue())


if __name__ == '__main__':
    unittest.main()
