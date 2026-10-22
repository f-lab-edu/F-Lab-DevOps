"""CI 전달·비공개 큐·worker의 인증 분리와 재시작/중복/정합성을 검증한다."""

import contextlib
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_ops.ci_handoff import TARGET, pack_history
from ai_ops.ci_history import LAB_RUNS_PATH, PREFIX
from ai_ops.ci_relay import CIRelay
from ai_ops.collection import SnapshotBuilder
from ai_ops.job_queue import JobQueue
from ai_ops.lab_ci_history import verify_lab_ci_history
from ai_ops.live_collectors.deployment import _import_ci_history
from ai_ops.worker import Worker
import test_lab_ci_history as fixture


class Reader:
    def __init__(self, source=None):
        self.source = source or fixture.Reader()
        self.runs = [dict(self.source.run)]
        self.count = 1
        self.cache = {}

    def get(self, path, **options):
        if path == LAB_RUNS_PATH:
            return json.dumps({"total_count": self.count, "workflow_runs": self.runs}).encode()
        if path not in self.cache:
            self.cache[path] = self.source.get(path, **options)
        return self.cache[path]


class CIQueueTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        env = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.time = 1000.0
        self.queue = JobQueue(self.root / "store", clock=lambda: self.time)
        builder, self.workload, self.app, self.history = fixture.LabCIHistoryTest().fixture()
        self.settings = builder.settings
        self.settings.bindings.bindings[0].alias = TARGET
        self.clock = builder.clock
        quiet = contextlib.redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.reader = Reader()
        self.relay = CIRelay(self.queue, self.settings, reader=self.reader,
                             clock=lambda: datetime(2026, 10, 22, 4, 6, tzinfo=timezone.utc))

    def collect(self, settings, target, *, ci_history, **options):
        builder = SnapshotBuilder(settings, target, clock=self.clock)
        _import_ci_history(builder, self.workload, self.app, ci_history)
        return builder.finish()

    def worker(self, collector=None):
        return Worker(self.queue, self.settings, collector=collector or self.collect)

    def test_verified_ci_flows_through_queue_and_worker_without_github_or_model_auth(self):
        receipt = self.relay.submit_run(fixture.RUN, fixture.HEAD)
        self.assertFalse(receipt["duplicate"])
        with patch("ai_ops.ci_history.GitHubReader.get", side_effect=AssertionError("worker GitHub 호출 금지")), \
             patch.object(self.queue.engine, "load_api_key", side_effect=AssertionError("모델 키 조회 금지")):
            result = self.worker().run_once()
        self.assertEqual((result["state"], result["attempts"]), ("completed", 1))
        record = self.queue.engine.show(result["incident_id"], result["run_id"])
        self.assertEqual(record.report.execution.engine, "rules-captured-ci-v1")
        self.assertEqual(record.report.current_state, "unknown")
        self.assertIn("자동 원복 단계", record.markdown)
        self.assertEqual(self.relay.submit_run(fixture.RUN, fixture.HEAD)["job_id"], receipt["job_id"])
        self.assertIsNone(self.worker().run_once())

    def test_discovery_only_enqueues_recent_completed_failures_and_deduplicates(self):
        self.assertEqual(self.relay.scan_once()["submitted"], 1)
        self.assertEqual(self.relay.scan_once()["duplicates"], 1)
        self.assertEqual(self.queue_rows("SELECT count(*) FROM jobs")[0][0], 1)
        self.relay.clock = lambda: datetime(2026, 10, 22, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(self.relay.scan_once()["skipped"], 1)

    def test_wrong_run_artifact_or_incomplete_discovery_never_enqueues(self):
        bad = fixture.Reader()
        bad.bad_digest = True
        relay = CIRelay(self.queue, self.settings, reader=Reader(bad), clock=self.relay.clock)
        self.assertEqual(relay.scan_once()["rejected"], 1)
        self.assertEqual(self.queue_rows("SELECT count(*) FROM jobs")[0][0], 0)
        self.reader.count = 2
        with self.assertRaises(ValueError):
            self.relay.scan_once()
        self.reader.count = 21
        self.reader.runs = [dict(self.reader.runs[0]) for _ in range(20)]
        result = self.relay.scan_once()
        self.assertTrue(result["listing_limited"])

    def queue_rows(self, query):
        with self.queue.transaction() as db:
            return db.execute(query).fetchall()

    def test_same_attempt_conflict_is_rejected_and_new_attempt_has_new_job(self):
        first, _ = self.queue.submit_ci(self.settings, self.history)
        altered = replace(self.history, archive_digest="sha256:" + "a" * 64)
        with self.assertRaisesRegex(ValueError, "다른 내용"):
            self.queue.submit_ci(self.settings, altered)
        source = fixture.Reader()
        source.run["run_attempt"] = 2
        source.artifact["name"] = f"b6-lab-observation-{fixture.RUN}-2"
        for record in source.records.values():
            record["run_attempt"] = "2"
        history = verify_lab_ci_history(fixture.RUN, fixture.HEAD, reader=source)
        second, duplicate = self.queue.submit_ci(self.settings, history)
        self.assertNotEqual(first, second)
        self.assertFalse(duplicate)

    def test_plain_json_or_wrong_binding_is_not_accepted_as_verified_history(self):
        with self.assertRaises(ValueError):
            self.queue.submit_ci(self.settings, pack_history(self.history))
        self.settings.bindings.bindings[0].target.namespace = "url-shortener"
        with self.assertRaises(ValueError):
            self.queue.submit_ci(self.settings, self.history)
        self.assertEqual(self.queue_rows("SELECT count(*) FROM jobs")[0][0], 0)

    def test_queue_payload_tampering_is_held_before_collection(self):
        job, _ = self.queue.submit_ci(self.settings, self.history)
        with self.queue.transaction() as db:
            payload = json.loads(db.execute("SELECT payload FROM jobs WHERE job_id=?", (job,)).fetchone()[0])
            payload["ci_history"]["artifact_id"] = "99"
            db.execute("UPDATE jobs SET payload=? WHERE job_id=?", (json.dumps(payload), job))
        collector = Mock(side_effect=AssertionError("변조 입력 수집 금지"))
        result = self.worker(collector).run_once()
        self.assertEqual((result["state"], result["reason"]), ("needs_review", "integrity_error"))
        collector.assert_not_called()

    def test_wrong_current_uid_is_held_instead_of_saving_false_report(self):
        self.queue.submit_ci(self.settings, self.history)
        self.workload.deployment["metadata"]["uid"] = "recreated-deployment"
        result = self.worker().run_once()
        self.assertEqual((result["state"], result["reason"]), ("needs_review", "integrity_error"))
        self.assertIsNone(result["run_id"])

    def test_stale_history_remains_partial_and_never_claims_automatic_recovery(self):
        builder, self.workload, self.app, self.history = fixture.LabCIHistoryTest().fixture(age=1800)
        self.clock = builder.clock
        self.queue.submit_ci(self.settings, self.history)
        result = self.worker().run_once()
        record = self.queue.engine.show(result["incident_id"], result["run_id"])
        self.assertEqual((result["state"], record.report.current_state), ("partial", "unknown"))
        self.assertFalse(record.report.facts)
        self.assertFalse(record.report.hypotheses)
        self.assertIn("판단의 근거에서 제외", record.markdown)

    def test_restart_uses_frozen_snapshot_and_never_collects_again(self):
        self.queue.submit_ci(self.settings, self.history)
        with patch.object(self.queue, "finish", side_effect=OSError("commit interrupted")):
            result = self.worker().run_once()
        self.assertEqual(result["state"], "analyzing")
        self.time += 61
        new_queue = JobQueue(self.queue.root, clock=lambda: self.time)
        collector = Mock(side_effect=AssertionError("완료 snapshot 재수집 금지"))
        Worker(new_queue, self.settings, collector=collector).recover()
        final = new_queue.show(result["job_id"])
        self.assertEqual((final["state"], final["attempts"]), ("completed", 1))
        collector.assert_not_called()

    def test_reopened_queue_preserves_ci_history_without_a_github_reader(self):
        job, _ = self.queue.submit_ci(self.settings, self.history)
        self.queue = JobQueue(self.queue.root, clock=lambda: self.time)
        with patch("ai_ops.ci_history.GitHubReader.get", side_effect=AssertionError("복원 시 GitHub 호출 금지")):
            result = self.worker().run_once()
        self.assertEqual(result["job_id"], job)
        self.assertEqual(result["state"], "completed")


if __name__ == "__main__":
    unittest.main()
