"""저장된 S10 검증 기록으로 CI v2 정제·원문 제외·보고서 경계를 검사한다."""

import copy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from ai_ops.collection import CapturedSnapshot
from ai_ops.continuous_model import MockTransport
from ai_ops.contracts import build_evidence_bundle, canonical_json_bytes
from ai_ops.live_collectors.common import add
from ai_ops.live_collectors.deployment import _import_ci_history
from ai_ops.model_ci_projection import (CIModelView, CIProjection, CIViewItem,
    parse_ci_projection, project_ci_snapshot)
from ai_ops.model_comparison import rules_projection
from ai_ops.model_projection import ModelView, SourceIdentity, project_snapshot
from ai_ops.projected_ci_model_engine import (request_ci_templates, validate_ci_report)
from ai_ops.projected_model_engine import generate_once
import test_lab_ci_history as lab_fixtures
from test_model_projection import SENTINEL, example_plan


def saved_s10_case(*, age=30):
    builder, workload, app, history = lab_fixtures.LabCIHistoryTest().fixture(age=age)
    _import_ci_history(builder, workload, app, history)
    uid = workload.deployment["metadata"]["uid"]
    at = builder.clock.now().isoformat().replace("+00:00", "Z")
    builder.workload_expected_pods = 1
    add(builder, "current-deployment", "deployment", at=at,
        payload={"image": SENTINEL + ":v1", "status": "unknown"},
        context={"resource_uid": uid, "resource_version": "1"})
    add(builder, "pod-status", "workload_status", at=at,
        payload={"image": SENTINEL + ":v1", "phase": "Running", "ready": True, "restarts": 0},
        context={"resource_uid": "pod-1", "resource_version": "1", "deployment_uid": uid,
                 "replicaset_uid": "rs-1"})
    add(builder, "pod-config", "config_summary", at=at,
        payload={"read_engine_role": "unknown", "redis_url_configured": False},
        context={"resource_uid": "pod-1", "resource_version": "1"})
    snapshot = builder.finish()
    identity = SourceIdentity(cluster_fingerprint="a" * 64, deployment_uid=uid,
        config_hash=builder.settings.config_hash, incident_id="inc-" + "1" * 32,
        run_id="run-" + "2" * 32, exporter_sha256="b" * 64)
    return snapshot, builder.settings, identity, history


class CIProjectionTest(unittest.TestCase):
    def test_saved_pair_adds_only_two_finite_states_and_preserves_v1(self):
        snapshot, settings, identity, history = saved_s10_case()
        original = canonical_json_bytes(snapshot.bundle.model_dump(mode="json"))
        v1 = project_snapshot(snapshot, settings, identity)
        self.assertFalse(any(isinstance(item, CIViewItem) for item in v1.view.items))
        projected = project_ci_snapshot(snapshot, settings, identity, history)
        stages = [item for item in projected.view.items if isinstance(item, CIViewItem)]
        self.assertEqual(projected.view.schema_version, 2)
        self.assertEqual([item.payload.stage for item in stages],
                         ["failed_deployment", "rollback_verification"])
        self.assertEqual([item.payload.ready_replicas for item in stages], [0, 1])
        self.assertEqual([item.payload.image_pull_failure_observed for item in stages], [True, False])
        self.assertEqual([item.observed_at for item in stages],
                         [record.observed_at for record in history.parsed()[:2]])
        self.assertEqual(projected.source_count, v1.source_count)
        self.assertEqual(sum(item.count for item in projected.view.excluded),
                         sum(item.count for item in v1.view.excluded) - 2)
        self.assertEqual(parse_ci_projection(canonical_json_bytes(projected.model_dump(mode="json"))), projected)
        with self.assertRaises(ValueError):
            ModelView.model_validate(projected.view.model_dump(mode="json"))
        self.assertEqual(original, canonical_json_bytes(snapshot.bundle.model_dump(mode="json")))
        requests = request_ci_templates(projected.view, example_plan())
        self.assertEqual(requests["/v1/responses/input_tokens"]["input"],
                         requests["/v1/responses"]["input"])
        self.assertEqual(requests["/v1/responses/input_tokens"]["instructions"],
                         requests["/v1/responses"]["instructions"])
        sent = canonical_json_bytes(requests)
        for private in (history.run_id, history.source_revision, history.archive_digest,
                        history.parsed()[0].observed.image, history.parsed()[1].observed.image,
                        history.parsed()[0].observed.deployment_uid,
                        history.parsed()[0].observed.application_uid,
                        SENTINEL, "source_ref", "ci_run_id", "application_uid", "deployment_uid"):
            self.assertNotIn(private.encode(), sent)
        self.assertNotIn(b'"tools"', sent)
        self.assertFalse(requests["/v1/responses"]["store"])

    def test_one_stale_stage_never_becomes_cause_evidence(self):
        snapshot, settings, identity, history = saved_s10_case(age=890)
        self.assertEqual([item.status for item in snapshot.bundle.evidence[:2]], ["stale", "available"])
        projected = project_ci_snapshot(snapshot, settings, identity, history)
        self.assertFalse(any(isinstance(item, CIViewItem) for item in projected.view.items))
        reasons = {(item.kind, item.source_status, item.reason): item.count
                   for item in projected.view.excluded}
        self.assertEqual(reasons[("deployment", "stale", "ci_pair_unverified")], 1)
        self.assertEqual(reasons[("deployment", "available", "ci_pair_unverified")], 1)
        self.assertEqual(reasons[("deployment", "available", "excluded_by_model_policy")], 1)
        base = rules_projection(projected.view).model_dump(mode="json")
        base.update(diagnosis_status="suspected_cause", hypotheses=[{
            "cause": "이미지 취득 실패", "supporting_evidence_ids": [projected.view.items[0].evidence_id],
            "contradicting_evidence_ids": []}])
        with self.assertRaisesRegex(ValueError, "requires included failure"):
            validate_ci_report(base, projected.view)

    def test_wrong_history_identity_or_audit_fails_closed(self):
        snapshot, settings, identity, history = saved_s10_case()
        for altered in (replace(history, run_id="999"),
                        replace(history, archive_digest="sha256:" + "c" * 64),
                        replace(history, source_revision="e" * 40),
                        replace(history, records=(history.records[1], history.records[0], history.records[2]))):
            with self.subTest(altered=altered.run_id), self.assertRaises(ValueError):
                project_ci_snapshot(snapshot, settings, identity, altered)
        with self.assertRaises(ValueError):
            project_ci_snapshot(snapshot, settings,
                identity.model_copy(update={"deployment_uid": "another"}), history)
        audit = snapshot.audit.model_copy(deep=True)
        audit.entries[0].ci_archive_digest = "d" * 64
        with self.assertRaises(ValueError):
            project_ci_snapshot(CapturedSnapshot(snapshot.bundle, audit), settings, identity, history)
        changed = [item.model_copy(update={"source_ref": "deployment/other.json"})
                   if item.evidence_id == "ev-deployment-ci-deployment" else item
                   for item in snapshot.bundle.evidence]
        bundle = build_evidence_bundle(target=snapshot.bundle.target, window=snapshot.bundle.window,
            created_at=snapshot.bundle.created_at, evidence=changed)
        with self.assertRaisesRegex(ValueError, "evidence source mismatch"):
            project_ci_snapshot(CapturedSnapshot(bundle, snapshot.audit), settings, identity, history)

    def test_missing_or_invalid_ci_pair_never_produces_v2_view(self):
        builder, workload, app, history = lab_fixtures.LabCIHistoryTest().fixture()
        uid = workload.deployment["metadata"]["uid"]
        now = builder.clock.now().isoformat().replace("+00:00", "Z")
        add(builder, "current-deployment", "deployment", at=now,
            payload={"image": SENTINEL + ":v1", "status": "unknown"},
            context={"resource_uid": uid, "resource_version": "1"})
        snapshot = builder.finish()
        identity = SourceIdentity(cluster_fingerprint="a" * 64, deployment_uid=uid,
            config_hash=builder.settings.config_hash, incident_id="inc-" + "1" * 32,
            run_id="run-" + "2" * 32, exporter_sha256="b" * 64)
        with self.assertRaises(ValueError):
            project_ci_snapshot(snapshot, builder.settings, identity, history)
        builder, workload, app, history = lab_fixtures.LabCIHistoryTest().fixture()
        workload.deployment["metadata"]["uid"] = "wrong-current-target"
        _import_ci_history(builder, workload, app, history)
        snapshot = builder.finish()
        with self.assertRaises(ValueError):
            project_ci_snapshot(snapshot, builder.settings, identity, history)

    def test_v2_view_rejects_raw_fields_wrong_pair_and_false_recovery(self):
        snapshot, settings, identity, history = saved_s10_case()
        projected = project_ci_snapshot(snapshot, settings, identity, history)
        original = projected.view.model_dump(mode="json")
        for change in (
            lambda view: view["items"][-2]["payload"].update(image="private"),
            lambda view: view["items"][-2]["payload"].update(ready_replicas=True),
            lambda view: view["items"][-1]["payload"].update(user_path_recovery_verified=True),
            lambda view: view["items"].pop(),
            lambda view: view.update(policy_version="captured-practice-projection-v1"),
        ):
            view = copy.deepcopy(original)
            change(view)
            with self.subTest(view=view), self.assertRaises(ValueError):
                CIModelView.model_validate_json(json.dumps(view))
        changed = projected.model_dump(mode="json")
        changed["view"]["items"][-2]["payload"]["ready_replicas"] = 1
        with self.assertRaises(ValueError):
            CIProjection.model_validate(changed)

    def test_same_view_mock_report_needs_failure_citation_and_human_review(self):
        snapshot, settings, identity, history = saved_s10_case()
        view = project_ci_snapshot(snapshot, settings, identity, history).view
        failed = next(item for item in view.items if isinstance(item, CIViewItem)
                      and item.payload.stage == "failed_deployment")
        report = rules_projection(view).model_dump(mode="json")
        report.update(diagnosis_status="suspected_cause", hypotheses=[{
            "cause": "당시 이미지 취득 실패가 배포 실패의 후보 원인",
            "supporting_evidence_ids": [failed.evidence_id], "contradicting_evidence_ids": []}])
        self.assertEqual(validate_ci_report(report, view).current_state, "unknown")
        mock = MockTransport({"object": "response.input_tokens", "input_tokens": 100},
            {"status": "completed", "model": "gpt-6-sol", "usage": {"input_tokens": 100,
             "output_tokens": 100}, "output": [{"type": "message", "content": [
             {"type": "output_text", "text": json.dumps(report)}]}]})
        draft, _, _ = generate_once(request_ci_templates(view, example_plan()),
                                    example_plan(), mock, clock=lambda: 0)
        self.assertEqual(validate_ci_report(draft, view).analysis_status, "partial")
        self.assertEqual(mock.calls, ["/v1/responses/input_tokens", "/v1/responses"])
        altered = copy.deepcopy(report)
        altered["hypotheses"][0]["supporting_evidence_ids"] = [view.items[0].evidence_id]
        with self.assertRaisesRegex(ValueError, "requires included failure"):
            validate_ci_report(altered, view)
        altered = copy.deepcopy(report)
        altered["current_state"] = "healthy"
        with self.assertRaises(ValueError):
            validate_ci_report(altered, view)

    def test_request_size_is_checked_before_any_transport(self):
        snapshot, settings, identity, history = saved_s10_case()
        view = project_ci_snapshot(snapshot, settings, identity, history).view
        with patch("ai_ops.projected_ci_model_engine.CI_INSTRUCTIONS", "가" * 32000):
            with self.assertRaises(ValueError):
                request_ci_templates(view, example_plan())


if __name__ == "__main__":
    unittest.main()
