"""합성 API 응답으로 S10 출처·관측의 무결성과 시각·대상 경계를 검사한다."""

import copy
import hashlib
import io
import json
import unittest
import zipfile
from dataclasses import replace
from datetime import timedelta

from ai_ops.b_contracts import BindingRegistry
from ai_ops.collection import SnapshotBuilder
from ai_ops.lab_ci_history import CLUSTER, PREFIX, REPOSITORY, WORKFLOW, verify_lab_ci_history
from ai_ops.live_collectors.deployment import _import_ci_history
from ai_ops.live_collectors.kubernetes import WorkloadContext
from test_live_collectors import Clock, ROOT
from ai_ops.b_contracts import load_b_settings


PROOF = json.loads((ROOT / "docs/assets/B6-s10-ci-validation.json").read_text())
HEAD = PROOF["artifact"]["source_revision"]
RUN = PROOF["artifact"]["run_id"]


class Reader:
    def __init__(self):
        self.records = copy.deepcopy(PROOF["observations"])
        self.run = {"id": RUN, "run_attempt": 1, "head_sha": HEAD, "head_branch": "main", "path": WORKFLOW,
                    "head_repository": {"full_name": REPOSITORY}, "repository": {"full_name": REPOSITORY},
                    "event": "workflow_dispatch", "status": "completed", "conclusion": "failure",
                    "run_started_at": "2026-10-22T04:00:00Z", "updated_at": "2026-10-22T04:05:00Z"}
        self.artifact = {"id": 9, "name": f"b6-lab-observation-{RUN}-1", "expired": False,
                         "workflow_run": {"id": RUN, "head_sha": HEAD, "head_branch": "main"}}
        self.count = 1
        self.extra = None
        self.bad_digest = False

    def get(self, path, **kwargs):
        if path == f"{PREFIX}/runs/{RUN}":
            return json.dumps(self.run).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for stage, record in self.records.items():
                archive.writestr(stage + ".json", json.dumps(record))
            if self.extra:
                archive.writestr(self.extra, "{}")
        raw = stream.getvalue()
        if path.endswith("/zip"):
            return raw
        artifact = {**self.artifact, "size_in_bytes": len(raw),
                    "digest": "sha256:" + ("0" * 64 if self.bad_digest else hashlib.sha256(raw).hexdigest())}
        return json.dumps({"total_count": self.count, "artifacts": [artifact]}).encode()


class LabCIHistoryTest(unittest.TestCase):
    def verify(self, reader=None):
        return verify_lab_ci_history(RUN, HEAD, reader=reader or Reader())

    def test_verified_pair_preserves_original_failure(self):
        history = self.verify()
        self.assertEqual(history.parsed()[0].verification_result, "failure")
        self.assertEqual(history.summary()["pair_status"], "failure_then_rollback_verification_success")
        self.assertFalse(history.summary()["user_path_recovery_verified"])

    def test_run_identity_conflicts_rejected(self):
        for field, value in (("id", True), ("run_attempt", 2), ("head_sha", "a" * 40),
                             ("head_branch", "other"), ("path", ".github/workflows/ci-cd.yml"),
                             ("event", "push"), ("conclusion", "success"), ("status", "in_progress"),
                             ("repository", {"full_name": "other/repo"})):
            with self.subTest(field=field):
                reader = Reader()
                reader.run[field] = value
                with self.assertRaises(ValueError):
                    self.verify(reader)

    def test_artifact_integrity_conflicts_rejected(self):
        for field, value in (("expired", True), ("id", True), ("name", "other"),
                             ("workflow_run", {"id": RUN, "head_sha": "a" * 40})):
            reader = Reader()
            reader.artifact[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(reader)
        for field, value in (("bad_digest", True), ("count", 2), ("extra", "../unexpected.json")):
            reader = Reader()
            setattr(reader, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(reader)

    def test_record_identity_and_time_conflicts_rejected(self):
        for field, value in (("schema_version", True), ("evidence_import_enabled", 0),
                             ("cluster_arn", CLUSTER.replace("urlshortener", "other")),
                             ("namespace", "url-shortener"), ("run_attempt", "2"),
                             ("source_revision", "a" * 40), ("baseline_revision", "a" * 40),
                             ("started_at", "2026-10-22T04:04:00Z"),
                             ("observed_at", "2026-10-22T04:06:00Z")):
            reader = Reader()
            reader.records["deployment"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify(reader)

    def test_contradictory_observations_rejected(self):
        for stage, field, value in (("rollback", "deployment_uid", "different"),
                                   ("rollback", "application_uid", "different"),
                                   ("rollback", "ready_replicas", 0),
                                   ("rollback", "health_status", "Progressing"),
                                   ("rollback", "image_pull_reasons", ["ErrImagePull"]),
                                   ("rollback", "operation_revision", "a" * 40),
                                   ("deployment", "generation_observed", False),
                                   ("deployment", "health_status", "Healthy"),
                                   ("deployment", "image_pull_reasons", [])):
            reader = Reader()
            reader.records[stage]["observed"][field] = value
            with self.subTest(stage=stage, field=field), self.assertRaises(ValueError):
                self.verify(reader)

    def fixture(self, *, age=30):
        settings = load_b_settings(ROOT / "config/targets.practice.example.json", ROOT / "config/b-runtime.example.json",
                                   ROOT / "config/metric-queries.json")
        doc = settings.bindings.model_dump()
        binding = doc["bindings"][0]
        binding["target"].update(cluster_id="urlshortener", environment="practice", namespace="ai-ops-lab", workload="lab-api", service="lab-api")
        binding["pod_selector"] = {"app": "lab-api"}
        binding["argo"].update(namespace="argocd", application="lab-target", path="ai-ops/lab", repo_url="https://github.com/f-lab-edu/F-Lab-DevOps.git")
        settings = replace(settings, bindings=BindingRegistry.model_validate(doc))
        history = self.verify()
        rollback = history.parsed()[1]
        clock = Clock()
        now = rollback.observed_at + timedelta(seconds=age)
        clock.now = lambda: now
        builder = SnapshotBuilder(settings, settings.bindings.bindings[0].target, clock=clock)
        workload = WorkloadContext(deployment={"metadata": {"uid": rollback.observed.deployment_uid}},
                                   image=rollback.observed.image, trusted=True)
        app = {"metadata": {"uid": rollback.observed.application_uid}, "status": {"sync": {"revision": rollback.baseline_revision}}}
        return builder, workload, app, history

    def test_fresh_pair_becomes_two_bound_evidence_items(self):
        builder, workload, app, history = self.fixture()
        _import_ci_history(builder, workload, app, history)
        snapshot = builder.finish()
        self.assertEqual([item.status for item in snapshot.bundle.evidence], ["available", "available"])
        self.assertEqual([item.payload["status"] for item in snapshot.bundle.evidence], ["failed", "healthy_after_rollback"])
        entry = snapshot.audit.entries[0]
        self.assertEqual(entry.ci_run_id, str(RUN))
        self.assertEqual(entry.ci_archive_digest, history.archive_digest[7:])
        self.assertEqual(entry.observed_raw, history.parsed()[0].observed_at.isoformat().replace("+00:00", "Z"))

    def test_old_pair_stays_stale_without_refreshing_timestamp(self):
        builder, workload, app, history = self.fixture(age=1800)
        _import_ci_history(builder, workload, app, history)
        snapshot = builder.finish()
        self.assertEqual([entry.status for entry in snapshot.audit.entries], ["stale", "stale"])
        self.assertTrue(all(entry.reason == "outside_window" for entry in snapshot.audit.entries))
        self.assertTrue(all(item.payload is None for item in snapshot.bundle.evidence))
        self.assertEqual(snapshot.audit.entries[0].ci_run_id, str(RUN))

    def test_current_target_conflicts_rejected(self):
        for change in ("uid", "app", "image", "revision", "trust", "unverified"):
            builder, workload, app, history = self.fixture()
            if change == "uid": workload.deployment["metadata"]["uid"] = "recreated"
            if change == "app": app["metadata"]["uid"] = "recreated"
            if change == "image": workload.image = "different"
            if change == "revision": app["status"]["sync"]["revision"] = "a" * 40
            if change == "trust": workload.trusted = False
            if change == "unverified": history = history.summary()
            _import_ci_history(builder, workload, app, history)
            with self.subTest(change=change):
                self.assertEqual(builder.entries[0].reason, "scope_mismatch")


if __name__ == "__main__":
    unittest.main()
