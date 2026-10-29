"""native 이미지 이벤트의 소유·이미지·시각 경계와 버전별 재조회 호환성을 검사한다."""

from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.b_contracts import load_b_settings
from ai_ops.collection import ReadFailure, SnapshotBuilder
from ai_ops.contracts import canonical_json_bytes
from ai_ops.incidents import IncidentStore
from ai_ops.live_collectors.common import add
from ai_ops.rules import analyze_snapshot_rules_v1, analyze_snapshot_rules_v2
from test_live_collectors import AT, Clock, stamp


ROOT = Path(__file__).resolve().parents[1]


class CapturedRulesV3Test(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        policy = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": self.directory.name})
        policy.start()
        self.addCleanup(policy.stop)
        self.settings = load_b_settings(ROOT / "config/targets.practice.example.json", ROOT / "config/b-runtime.example.json",
                                       ROOT / "config/metric-queries.json")
        self.target = self.settings.bindings.bindings[0].target
        self.store = IncidentStore(Path(self.directory.name) / "store")

    def snapshot(self, *, reason="Failed", message="failed to pull ErrImagePull", involved="pod-1", ready=False,
                 phase="Pending", event_age=10, pod_age=0, deployment_age=0, deployment_uid="dep-1",
                 deployment_image="registry.invalid/app:bad", pod_image="registry.invalid/app:bad",
                 event_type="Warning", missing_owner=False, event_missing=False,
                 pod_deployment_uid="dep-1"):
        builder = SnapshotBuilder(self.settings, self.target, clock=Clock())
        builder.workload_expected_pods = 1
        context = {"resource_uid": "pod-1", "resource_version": "2", "deployment_uid": pod_deployment_uid,
                   "replicaset_uid": "rs-1", "replicaset_resource_version": "3"}
        if missing_owner:
            context.pop("replicaset_uid")
        add(builder, "pod-status", "workload_status", at=stamp(AT-timedelta(seconds=pod_age)),
            payload={"image": pod_image, "phase": phase, "ready": ready, "restarts": 0}, context=context)
        add(builder, "deployment-object", "deployment", at=stamp(AT-timedelta(seconds=deployment_age)),
            payload={"image": deployment_image, "status": "unknown"},
            context={"resource_uid": deployment_uid, "resource_version": "4"})
        if event_missing:
            add(builder, "event-image", "event", failure=ReadFailure("permission_denied", "permission_denied"))
        else:
            add(builder, "event-image", "event", at=stamp(AT-timedelta(seconds=event_age)), time_field="lastTimestamp",
                payload={"reason": reason, "type": event_type, "message": message, "involved_uid": involved},
                context={"resource_uid": "event-1", "resource_version": "5"})
        add(builder, "missing-log", "log", failure=ReadFailure("missing", "not_found"))
        return builder.finish()

    def investigate(self, snapshot, request="v3"):
        return self.store.investigate_snapshot(snapshot=snapshot, settings=self.settings,
            question="수집된 상태와 부족한 근거를 확인해 주세요.", request_id=request)

    def test_native_failed_marker_explains_image_issue_without_progressing_argo(self):
        record = self.investigate(self.snapshot())
        report = record.report
        self.assertEqual(report.execution.engine, "rules-captured-v3")
        self.assertEqual((report.analysis_status, report.diagnosis_status, report.current_state),
                         ("partial", "suspected_cause", "unknown"))
        self.assertEqual(report.hypotheses[0].supporting_evidence_ids,
                         ["ev-pod-status", "ev-event-image", "ev-deployment-object"])
        self.assertIn("레지스트리", report.recommended_checks[0])
        self.assertIn("확정하지", report.limitations[-1])
        self.assertFalse(report.suggested_actions)
        self.assertEqual(self.store.show(record.incident_id, record.run_id).report, report)

    def test_native_image_reason_or_bounded_marker_can_be_used(self):
        for index, updates in enumerate((
            {"reason": "ImagePullBackOff", "message": "no free text retained"},
            {"reason": "ErrImagePull", "message": "no free text retained"},
            {"reason": "BackOff", "message": "ImagePullBackOff"},
        )):
            with self.subTest(updates=updates):
                record = self.investigate(self.snapshot(**updates), request="positive-"+str(index))
                self.assertEqual(record.report.diagnosis_status, "suspected_cause")

    def test_unrelated_old_future_ready_or_mismatched_evidence_is_not_a_cause(self):
        cases = [
            {"message": "failed scheduling for unrelated issue"},
            {"message": "prefixErrImagePullsuffix"},
            {"involved": "other-pod"}, {"ready": True}, {"phase": "Running"},
            {"event_age": 121}, {"event_age": 0, "pod_age": 10},
            {"deployment_age": 121}, {"deployment_uid": "other-dep"},
            {"deployment_image": "registry.invalid/app:different"},
            {"event_type": "Normal"}, {"missing_owner": True}, {"event_missing": True},
            {"reason": "Unhealthy", "message": "ImagePullBackOff"},
        ]
        for index, updates in enumerate(cases):
            with self.subTest(updates=updates):
                report = self.investigate(self.snapshot(**updates), request="negative-"+str(index)).report
                self.assertEqual(report.diagnosis_status, "insufficient_evidence")
                self.assertEqual(report.hypotheses, [])
                self.assertEqual(report.current_state, "unknown")

    def test_missing_audit_never_enables_native_image_correlation(self):
        from ai_ops.rules import analyze_snapshot_rules
        record = self.investigate(self.snapshot())
        report = analyze_snapshot_rules(record.request, record.bundle, record.run_id)
        self.assertFalse(report.hypotheses)

    def test_v1_v2_saved_reports_are_unchanged_after_v3_default(self):
        snapshot = self.snapshot()
        for version, analyzer in (("v1", analyze_snapshot_rules_v1), ("v2", analyze_snapshot_rules_v2)):
            with self.subTest(version=version):
                with patch("ai_ops.incidents.analyze_snapshot_rules", side_effect=analyzer):
                    record = self.investigate(snapshot, request="saved-"+version)
                path = self.store.root / record.incident_id / record.run_id
                before = {name: (path/name).read_bytes() for name in ("report.json", "input.json", "collection-audit.json")}
                shown = self.store.show(record.incident_id, record.run_id)
                self.assertEqual(shown.report.execution.engine, "rules-captured-"+version)
                self.assertFalse(shown.report.hypotheses)
                self.assertEqual({name: (path/name).read_bytes() for name in before}, before)

    def test_v3_facts_hypotheses_checks_limitations_cannot_be_rewritten(self):
        record = self.investigate(self.snapshot())
        path = self.store.root / record.incident_id / record.run_id / "report.json"
        original = path.read_bytes()
        for field in ("facts", "hypotheses", "recommended_checks", "limitations"):
            with self.subTest(field=field):
                data = json.loads(original)
                data[field] = []
                if field == "hypotheses":
                    data[field] = [{"cause": "검증하지 않은 원인", "supporting_evidence_ids": ["ev-pod-status"], "contradicting_evidence_ids": []}]
                path.write_bytes(canonical_json_bytes(data))
                with self.assertRaisesRegex(ValueError, "interpretation contradicts"):
                    self.store.show(record.incident_id, record.run_id)
                path.write_bytes(original)
