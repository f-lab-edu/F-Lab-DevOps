"""검증된 CI 완료와 규칙 보고서 이후에만 모델 대기열을 채우는지 검증한다."""

import json
import unittest

from ai_ops.collection import SnapshotBuilder
from ai_ops.continuous_ci_bridge import ContinuousCIBridge
from ai_ops.continuous_model import ContinuousModelStore
from ai_ops.live_collectors.common import add
from ai_ops.live_collectors.deployment import _import_ci_history
from ai_ops.worker import Worker
import test_ci_queue as queue_fixtures
from test_model_projection import example_plan


class ContinuousCIBridgeTest(unittest.TestCase):
    def setUp(self):
        self.fixture = queue_fixtures.CIQueueTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.queue = self.fixture.queue
        self.settings = self.fixture.settings
        self.store = ContinuousModelStore(self.fixture.root / "model", clock=lambda: self.fixture.time)
        self.campaign = self.store.prepare_campaign(
            deployment_uid=self.fixture.history.parsed()[0].observed.deployment_uid,
            config_hash=self.settings.config_hash, cluster_fingerprint="a"*64,
            plan=example_plan())
        self.bridge = ContinuousCIBridge(self.store, self.campaign,
            cluster_fingerprint="a"*64, exporter_sha256="b"*64)

    def collect(self, settings, target, *, ci_history, **options):
        builder = SnapshotBuilder(settings, target, clock=self.fixture.clock)
        _import_ci_history(builder, self.fixture.workload, self.fixture.app, ci_history)
        uid = ci_history.parsed()[0].observed.deployment_uid
        at = self.fixture.clock.now().isoformat().replace("+00:00", "Z")
        builder.workload_expected_pods = 1
        add(builder, "current-deployment", "deployment", at=at,
            payload={"image": "PRIVATE_SOURCE_SENTINEL:v1", "status": "unknown"},
            context={"resource_uid": uid, "resource_version": "1"})
        add(builder, "pod-status", "workload_status", at=at,
            payload={"image": "PRIVATE_SOURCE_SENTINEL:v1", "phase": "Running",
                     "ready": True, "restarts": 0},
            context={"resource_uid": "pod-1", "resource_version": "1",
                     "deployment_uid": uid, "replicaset_uid": "rs-1"})
        add(builder, "pod-config", "config_summary", at=at,
            payload={"read_engine_role": "unknown", "redis_url_configured": False},
            context={"resource_uid": "pod-1", "resource_version": "1"})
        return builder.finish()

    def test_verified_ci_completion_enqueues_separate_view_and_reconciles(self):
        job, _ = self.queue.submit_ci(self.settings, self.fixture.history)
        worker = Worker(self.queue, self.settings, collector=self.collect, model_bridge=self.bridge)
        result = worker.run_once()
        self.assertEqual(result["state"], "completed")
        record = self.queue.engine.show(result["incident_id"], result["run_id"])
        self.assertEqual(record.report.execution.engine, "rules-captured-ci-v1")
        before = record.markdown
        with self.store._transaction() as db:
            rows = db.execute("SELECT * FROM outbox").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_run"], result["run_id"])
        self.assertEqual(rows[0]["state"], "prepared")
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", rows[0]["view"])
        self.assertNotIn("ci_run_id", rows[0]["view"])
        self.assertNotIn("mapping", rows[0]["view"])
        worker.run_once()
        with self.store._transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 1)
        self.assertEqual(self.queue.engine.show(result["incident_id"], result["run_id"]).markdown, before)
        self.assertEqual(self.queue.show(job)["state"], "completed")

    def test_ci_only_snapshot_keeps_rules_and_records_projection_rejection(self):
        job, _ = self.queue.submit_ci(self.settings, self.fixture.history)
        result = Worker(self.queue, self.settings, collector=self.fixture.collect,
                        model_bridge=self.bridge).run_once()
        self.assertEqual(result["state"], "completed")
        with self.store._transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)
            rejected = db.execute("SELECT reason FROM rejections WHERE job_id=?", (job,)).fetchone()
        self.assertEqual(rejected["reason"], "projection_rejected")

    def test_manual_request_does_not_enqueue_and_tampered_ci_is_rejected(self):
        job, _ = self.queue.submit_ci(self.settings, self.fixture.history)
        with self.queue.transaction() as db:
            payload = json.loads(db.execute("SELECT payload FROM jobs WHERE job_id=?", (job,)).fetchone()[0])
            payload["ci_history"]["artifact_id"] = "99"
            db.execute("UPDATE jobs SET payload=? WHERE job_id=?", (json.dumps(payload), job))
        result = Worker(self.queue, self.settings, collector=self.collect,
                        model_bridge=self.bridge).run_once()
        self.assertEqual(result["state"], "needs_review")
        with self.store._transaction() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
