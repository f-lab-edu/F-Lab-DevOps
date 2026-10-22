"""CI 보고서의 실행 연결·원복 범위·오래된 관측 제외와 저장 호환성을 확인한다."""

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from ai_ops.ci_rules import ENGINE
from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.incidents import IncidentStore
from ai_ops.live_collectors.common import add
from ai_ops.live_collectors.deployment import _import_ci_history
from ai_ops.reports import validate_report
from ai_ops.rules import analyze_snapshot_rules_v3
import test_lab_ci_history as history_fixture


class CIReportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = IncidentStore(Path(self.directory.name) / "store")

    def fixture(self, *, age=30, stages=None, edit=None):
        builder, workload, app, history = history_fixture.LabCIHistoryTest().fixture(age=age)
        _import_ci_history(builder, workload, app, history)
        if stages is not None:
            chosen = set(stages)
            builder.items = [item for item in builder.items if item.payload.get("status") in chosen]
            builder.entries = [entry for entry in builder.entries if entry.evidence_id in {item.evidence_id for item in builder.items}]
        if edit:
            edit(builder)
        return builder.finish(), builder.settings

    def investigate(self, snapshot=None, settings=None, *, request_id="ci-report"):
        if snapshot is None:
            snapshot, settings = self.fixture()
        return self.store.investigate_snapshot(snapshot=snapshot, settings=settings,
            question="CI 최초 실패와 자동 원복, 사용자 복구 확인 여부와 시각을 설명해 주세요.", request_id=request_id)

    def test_fresh_pair_explains_initial_failure_and_automatic_rollback_in_kst(self):
        record = self.investigate()
        report = record.report
        self.assertEqual(report.execution.engine, ENGINE)
        self.assertEqual(report.current_state, "unknown")
        self.assertEqual(report.diagnosis_status, "suspected_cause")
        self.assertIn("최초 배포 검증", report.facts[0].statement)
        self.assertIn("자동 원복 단계", report.facts[1].statement)
        self.assertIn("CI 최종 결과 failure", report.facts[2].statement)
        self.assertIn("13:02:13 KST", record.markdown)
        self.assertIn("13:02:33 KST", record.markdown)
        self.assertNotIn("04:02:13 UTC", record.markdown)
        self.assertIn("false", " ".join(report.limitations))
        self.assertIn("서비스 복구 완료를 주장하지", " ".join(report.limitations))
        self.assertEqual(report.suggested_actions, [])
        self.assertEqual(report.execution.tool_calls, 0)
        validate_report(report, record.request, record.bundle, record.run_id)
        self.assertEqual(self.store.show(record.incident_id, record.run_id).report, report)

    def test_current_ready_observation_does_not_erase_initial_failure(self):
        def edit(builder):
            add(builder, "current-pod", "workload_status", at=builder.started_at.isoformat().replace("+00:00", "Z"),
                payload={"image": builder.items[1].payload["image"], "phase": "Running", "ready": True, "restarts": 0})
            add(builder, "current-argo", "deployment", at=builder.started_at.isoformat().replace("+00:00", "Z"),
                payload={"gitops_revision": builder.items[1].payload["gitops_revision"], "status": "healthy"})
        snapshot, settings = self.fixture(edit=edit)
        record = self.investigate(snapshot, settings)
        self.assertIn("최초 배포 검증", record.report.facts[0].statement)
        self.assertEqual(record.report.current_state, "unknown")
        self.assertEqual(record.report.diagnosis_status, "suspected_cause")

    def test_import_cli_can_save_report_with_verified_snapshot_without_model_calls(self):
        snapshot, settings = self.fixture()
        settings.bindings.bindings[0].alias = "url-shortener-b6-lab"
        script = history_fixture.ROOT / "scripts/import_lab_ci_history.py"
        spec = importlib.util.spec_from_file_location("ci_import_operator", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root = Path(self.directory.name)
        for name in ("bindings.json", "runtime.json", "metrics.json"):
            (root / name).write_text("{}")
        # 경로 검사는 실제 실행하고 외부 조회만 모의 구현으로 대체한다.
        args = [str(script), "--run-id", str(history_fixture.RUN), "--expected-head", history_fixture.HEAD,
                "--bindings", str(root / "bindings.json"), "--runtime", str(root / "runtime.json"),
                "--metrics", str(root / "metrics.json"), "--output", str(root / "result.json"),
                "--store", str(root / "cli-store")]
        history = history_fixture.LabCIHistoryTest().verify()
        with patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(root)}), patch("sys.argv", args), \
             patch.object(module, "load_b_settings", return_value=settings), \
             patch.object(module, "verify_cluster"), patch.object(module, "verify_lab_ci_history", return_value=history), \
             patch.object(module, "collect_snapshot", return_value=snapshot), \
             patch("socket.create_connection", side_effect=AssertionError("외부 호출 금지")):
            self.assertEqual(module.main(), 0)
        result = json.loads((root / "result.json").read_text())
        identity = result["investigation"]
        shown = IncidentStore(root / "cli-store").show(identity["incident_id"], identity["run_id"])
        self.assertEqual(shown.report.execution.engine, ENGINE)
        self.assertEqual(identity["engine"], ENGINE)
        self.assertFalse((root / "cli-store/ai-budget.json").exists())
        self.assertEqual(result["external_model_calls"], 0)

    def test_single_stage_does_not_establish_failure_then_recovery(self):
        for stage in ("failed", "healthy_after_rollback"):
            with self.subTest(stage=stage):
                snapshot, settings = self.fixture(stages=[stage])
                report = self.investigate(snapshot, settings, request_id=stage).report
                self.assertEqual(report.current_state, "unknown")
                self.assertEqual(report.diagnosis_status, "insufficient_evidence")
                self.assertEqual(report.hypotheses, [])
                self.assertEqual(len(report.facts), 1)
                self.assertIn("단일 관측", report.facts[0].statement)
                self.assertNotIn("CI 최종 결과 failure", report.facts[0].statement)

    def test_stale_history_is_excluded_and_original_time_is_explained(self):
        snapshot, settings = self.fixture(age=1800)
        record = self.investigate(snapshot, settings)
        self.assertEqual(record.metadata["status"], "partial")
        self.assertEqual((record.report.diagnosis_status, record.report.current_state), ("insufficient_evidence", "unknown"))
        self.assertFalse(record.report.facts)
        self.assertFalse(record.report.hypotheses)
        self.assertEqual(len(record.report.missing_evidence), 2)
        self.assertTrue(all("오래된 자료" in missing.reason for missing in record.report.missing_evidence))
        self.assertIn("판단의 근거에서 제외", record.markdown)
        self.assertIn("13:02:13 KST", record.markdown)
        self.assertIn("13:02:33 KST", record.markdown)
        self.assertEqual(self.store.show(record.incident_id, record.run_id).report, record.report)

    def test_only_one_fresh_stage_does_not_use_stale_other_stage(self):
        snapshot, settings = self.fixture(age=890)
        record = self.investigate(snapshot, settings)
        self.assertEqual([item.status for item in record.bundle.evidence], ["stale", "available"])
        self.assertEqual(len(record.report.facts), 1)
        self.assertIn("원복 검증 success 단일 관측", record.report.facts[0].statement)
        self.assertFalse(record.report.hypotheses)
        self.assertEqual(record.report.current_state, "unknown")

    def test_different_run_attempt_artifact_hash_source_or_uid_cannot_form_a_pair(self):
        cases = {"ci_run_id": "2", "ci_run_attempt": "2", "ci_artifact_id": "10",
                 "ci_archive_digest": "a" * 64, "ci_source_revision": "a" * 40,
                 "deployment_uid": "other-deployment", "resource_uid": "other-application"}
        for field, value in cases.items():
            def edit(builder):
                setattr(builder.entries[1], field, value)
                item = builder.items[1]
                if field == "ci_run_id": item.payload["ci_run_id"] = value
                if field == "ci_source_revision": item.payload["source_sha"] = value
                item.content_hash = sha256_json(item.payload)
            snapshot, settings = self.fixture(edit=edit)
            with self.subTest(field=field):
                record = self.investigate(snapshot, settings, request_id=field)
                self.assertEqual(record.report.diagnosis_status, "insufficient_evidence")
                self.assertEqual(record.report.current_state, "unknown")
                self.assertFalse(record.report.hypotheses)
                self.assertFalse(any("CI 최종 결과 failure" in fact.statement for fact in record.report.facts))

    def test_reversed_times_or_same_revision_cannot_form_a_pair(self):
        for case in ("time", "revision"):
            def edit(builder):
                failed, restored = builder.items
                if case == "time":
                    moment = failed.observed_at - timedelta(seconds=1)
                    stamp = moment.isoformat().replace("+00:00", "Z")
                    restored.observed_at = moment
                    restored.payload["source_time"]["observed_raw"] = stamp
                    builder.entries[1].observed_raw = stamp
                else:
                    restored.payload["gitops_revision"] = failed.payload["gitops_revision"]
                    restored.payload["rollback_revision"] = failed.payload["gitops_revision"]
                restored.content_hash = sha256_json(restored.payload)
            snapshot, settings = self.fixture(edit=edit)
            with self.subTest(case=case):
                report = self.investigate(snapshot, settings, request_id=case).report
                self.assertFalse(report.hypotheses)
                self.assertEqual(report.current_state, "unknown")

    def test_missing_audit_fields_or_user_recovery_flag_do_not_enable_claims(self):
        for field, value in (("ci_workflow", None), ("ci_run_conclusion", None),
                             ("ci_stage", None), ("ci_verification_result", None),
                             ("ci_user_path_recovery_verified", None), ("ci_user_path_recovery_verified", True)):
            def edit(builder):
                for entry in builder.entries: setattr(entry, field, value)
            snapshot, settings = self.fixture(edit=edit)
            with self.subTest(field=field, value=value):
                report = self.investigate(snapshot, settings, request_id=field + str(value)).report
                self.assertFalse(report.facts)
                self.assertFalse(report.hypotheses)
                self.assertEqual(report.current_state, "unknown")

    def test_legacy_v3_report_remains_byte_preserved_and_replayable(self):
        snapshot, settings = self.fixture()
        with patch("ai_ops.incidents.analyze_snapshot_rules", side_effect=analyze_snapshot_rules_v3):
            record = self.investigate(snapshot, settings)
        directory = self.store.root / record.incident_id / record.run_id
        before = {name: (directory / name).read_bytes() for name in ("report.json", "input.json", "collection-audit.json")}
        shown = self.store.show(record.incident_id, record.run_id)
        self.assertEqual(shown.report.execution.engine, "rules-captured-v3")
        self.assertEqual({name: (directory / name).read_bytes() for name in before}, before)

    def test_saved_interpretation_including_missing_reasons_cannot_be_rewritten(self):
        snapshot, settings = self.fixture(age=1800)
        record = self.investigate(snapshot, settings)
        path = self.store.root / record.incident_id / record.run_id / "report.json"
        before = path.read_bytes()
        for field in ("facts", "missing_evidence", "limitations", "current_state"):
            document = json.loads(before)
            if field == "facts":
                document[field] = [{"statement": "서비스 복구 완료", "evidence_ids": ["ev-deployment-ci-rollback"]}]
            elif field == "current_state": document[field] = "recovered"
            else: document[field] = []
            path.write_bytes(canonical_json_bytes(document))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.show(record.incident_id, record.run_id)
            path.write_bytes(before)


if __name__ == "__main__":
    unittest.main()
