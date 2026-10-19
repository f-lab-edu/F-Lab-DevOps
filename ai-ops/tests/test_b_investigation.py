"""B snapshot의 규칙 조사·개인 저장·재조회·모델 전송 거부를 검증한다."""

import os
import contextlib
import io
import json
import shutil
import stat
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from ai_ops.b_contracts import load_b_settings
from ai_ops.cli import main
from ai_ops.collection import CapturedSnapshot, ReadFailure, SnapshotBuilder
from ai_ops.contracts import TargetRegistry, build_evidence_bundle, sha256_json
from ai_ops.data_policy import ModelDataDenied, ensure_external_model_allowed, validate_snapshot
from ai_ops.fixed_summary import analyze_fixed_summary
from ai_ops.incidents import IncidentStore
from ai_ops.live_collectors import collect_snapshot
from ai_ops.live_collectors.common import add
from ai_ops.openai_engine import AIAnalysisError, AIConfig, analyze_ai
from ai_ops.replay_runner import run_replay
from test_live_collectors import API, AT, Clock, SENTINEL, stamp


ROOT = Path(__file__).resolve().parents[1]


class BInvestigationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        policy = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": self.directory.name})
        policy.start()
        self.addCleanup(policy.stop)
        self.store_root = Path(self.directory.name) / "store"
        self.store = IncidentStore(self.store_root)
        config = ROOT / "config"
        self.settings = load_b_settings(config / "targets.practice.example.json", config / "b-runtime.example.json", config / "metric-queries.json")
        self.target = self.settings.bindings.bindings[0].target
        self.registry = TargetRegistry(schema_version=1, targets=[self.target])
        clock = Clock()
        api = API(self.settings, clock)
        api.docs["deployment"]["spec"]["replicas"] = 1
        self.snapshot = collect_snapshot(self.settings, self.target, transport=api, clock=clock)

    def investigate(self, snapshot=None, request_id="b-rules"):
        return self.store.investigate_snapshot(snapshot=snapshot or self.snapshot, settings=self.settings,
            question="상태를 설명해줘 token=" + SENTINEL, request_id=request_id)

    def test_registered_production_collects_rules_report_but_never_calls_models(self):
        runtime = self.settings.runtime.model_dump()
        runtime["connections"] = {
            "eks-kubernetes": runtime["connections"]["practice-kubernetes"],
            "eks-prometheus": runtime["connections"]["practice-prometheus"],
        }
        runtime_path = Path(self.directory.name) / "runtime.json"
        runtime_path.write_text(json.dumps(runtime))
        self.settings = load_b_settings(ROOT / "config/targets.production.json", runtime_path,
                                        ROOT / "config/metric-queries.json")
        self.target = self.settings.bindings.bindings[0].target
        self.assertEqual((self.target.cluster_id, self.target.environment), ("urlshortener", "production"))
        for changes in ({"cluster_id": "other-cluster"}, {"environment": "practice"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "not registered"):
                SnapshotBuilder(self.settings, self.target.model_copy(update=changes), clock=Clock())
        clock = Clock()
        with patch("socket.create_connection", side_effect=AssertionError("실제 통신하면 안 된다")):
            snapshot = collect_snapshot(self.settings, self.target, transport=API(self.settings, clock), clock=clock)
            validate_snapshot(snapshot, self.settings)
            record = self.investigate(snapshot, request_id="production-rules")
            shown = self.store.show(record.incident_id, record.run_id)
        self.assertEqual(shown.request.target.environment, "production")
        self.assertEqual(shown.bundle.target, self.target)
        self.assertEqual(shown.report.execution.engine, "rules-captured-v1")
        transport, token_counter = Mock(), Mock()
        for engine in (analyze_ai, analyze_fixed_summary):
            with self.subTest(engine=engine.__name__), self.assertRaisesRegex(AIAnalysisError, "captured_data_not_approved"):
                engine(record.request, snapshot.bundle, record.run_id, AIConfig(account_label="production-test"),
                       "unused-key", transport, token_counter)
        transport.assert_not_called()
        token_counter.assert_not_called()
        self.assertFalse((self.store_root / "ai-budget.json").exists())

    def test_stale_history_keeps_audit_time_and_saves_partial_report(self):
        clock = Clock()
        builder = SnapshotBuilder(self.settings, self.target, clock=clock)
        old = AT - timedelta(hours=1)
        old_text = old.isoformat().replace("+00:00", "Z")
        add(builder, "old-history", "deployment", at=old_text,
            time_field="history_deployed_at", payload={"gitops_revision": "a" * 40, "status": "unknown"})
        snapshot = builder.finish()
        record = self.investigate(snapshot, request_id="stale-history")
        shown = self.store.show(record.incident_id, record.run_id)
        self.assertEqual(shown.metadata["status"], "partial")
        self.assertEqual(shown.metadata["failure_reason"], "collection_incomplete")
        item = shown.bundle.evidence[0]
        self.assertEqual((item.status, item.missing_reason), ("stale", "outside_window"))
        self.assertIsNone(item.observed_at)
        self.assertIsNone(item.payload)
        self.assertEqual(shown.collection_audit.entries[0].observed_raw, old_text)
        self.assertEqual(shown.collection_audit.entries[0].observed_offset, "Z")
        # 시각을 다시 끼워 넣은 묶음은 여전히 시간 계약 위반으로 거부한다.
        altered = build_evidence_bundle(target=shown.bundle.target, window=shown.bundle.window,
            created_at=shown.bundle.created_at,
            evidence=[item.model_copy(update={"observed_at": old})])
        with self.assertRaises(ValueError):
            validate_snapshot(CapturedSnapshot(altered, shown.collection_audit), self.settings)

    def complete_snapshot(self, *, ready=True, expected=2, observed=2, missing=False):
        clock = Clock()
        builder = SnapshotBuilder(self.settings, self.target, clock=clock)
        builder.workload_expected_pods = expected
        for index in range(observed):
            add(builder, f"pod-{index}-status", "workload_status", at=stamp(),
                payload={"image": "registry.invalid/app:v1", "phase": "Running", "ready": ready if index == 1 else True, "restarts": 0},
                context={"resource_uid": f"pod-{index}", "resource_version": "1", "deployment_uid": "deployment-1",
                         "replicaset_uid": "replicaset-1", "replicaset_resource_version": "1"})
        add(builder, "log", "log", at=stamp(), payload={"container": "api", "lines": [stamp() + " db_route=replica operation=select_one"]})
        add(builder, "event", "event", at=stamp(), payload={"reason": "Started", "type": "Normal", "message": "container started", "involved_uid": "pod-0"})
        add(builder, "deployment", "deployment", at=stamp(), payload={"image": "registry.invalid/app:v1", "status": "unknown"})
        add(builder, "config", "config_summary", at=stamp(), payload={"read_engine_role": "unknown"})
        metrics = {"cache_outcomes": ("count", {"hit": 10, "miss": 0, "error": 0, "unavailable": 0, "bypass": 0, "consistency_primary": 0}),
                   "cache_hit_ratio": ("ratio", {"ratio": 1, "requests": 10}),
                   "http_error_ratio": ("ratio", {"ratio": 0, "requests": 10}),
                   "api_latency": ("seconds", {"p95": 0.01, "count": 10}),
                   "db_latency": ("seconds", {"p99": 0.01, "count": 10})}
        for query, (unit, outcomes) in metrics.items():
            add(builder, query, "metric", at=stamp(), payload={"query_id": query, "unit": unit,
                "samples": [{"at": stamp(), "outcome": outcome, "value": value} for outcome, value in outcomes.items()]})
        if missing:
            add(builder, "missing-log", "log", failure=ReadFailure("permission_denied", "permission_denied"))
        return builder.finish()

    def test_collect_to_private_partial_report_and_show_preserves_utc_kst(self):
        record = self.investigate()
        self.assertEqual((record.metadata["status"], record.metadata["failure_reason"]), ("partial", "collection_incomplete"))
        self.assertEqual(record.report.execution.engine, "rules-captured-v1")
        self.assertEqual(record.request.source, "manual_snapshot")
        self.assertEqual(record.report.current_state, "unknown")
        self.assertIn("captured", record.markdown)
        self.assertIn("KST", record.markdown)
        self.assertNotIn("AI가 합성", record.markdown)
        self.assertNotIn(SENTINEL, record.markdown)
        run_dir = self.store_root / record.incident_id / record.run_id
        for name in ("input.json", "report.json", "report.md", "metadata.json", "collection-audit.json"):
            self.assertEqual(stat.S_IMODE((run_dir / name).stat().st_mode), 0o600)
            self.assertNotIn(SENTINEL, (run_dir / name).read_text())
        envelope = json.loads((run_dir / "collection-audit.json").read_text())
        self.assertEqual(envelope["bundle_hash"], record.bundle.bundle_hash)
        self.assertTrue(envelope["audit"]["started_at"].endswith("Z"))
        shown = self.store.show(record.incident_id, record.run_id)
        self.assertEqual(shown.report, record.report)
        self.assertEqual(shown.collection_audit, record.collection_audit)
        self.assertFalse((self.store_root / "ai-budget.json").exists())

    def test_all_pods_must_be_ready_and_counted_and_collection_complete_for_healthy(self):
        for options, expected_state in (({}, "healthy"), ({"ready": False}, "unknown"),
                ({"expected": 3}, "unknown"), ({"observed": 1}, "unknown"), ({"missing": True}, "unknown")):
            with self.subTest(options=options):
                snapshot = self.complete_snapshot(**options)
                record = self.investigate(snapshot, request_id="case-" + str(len(list(self.store_root.glob('inc-*')))))
                self.assertEqual(record.report.current_state, expected_state)
                self.assertEqual(record.metadata["status"], "partial" if options.get("missing") else "completed")
                self.assertEqual(self.store.show(record.incident_id, record.run_id).report, record.report)

    def test_all_sources_unavailable_remain_partial_without_claiming_healthy(self):
        clock = Clock()
        builder = SnapshotBuilder(self.settings, self.target, clock=clock)
        add(builder, "missing", "workload_status", failure=ReadFailure("permission_denied", "permission_denied"))
        record = self.investigate(builder.finish())
        self.assertEqual((record.report.diagnosis_status, record.report.current_state), ("insufficient_evidence", "unknown"))
        self.assertEqual(record.report.analysis_status, "partial")
        self.assertFalse(record.report.facts)

    def test_ready_pods_with_slow_db_are_not_reported_healthy(self):
        snapshot = self.complete_snapshot()
        items = [item.model_copy(deep=True) for item in snapshot.bundle.evidence]
        metric = next(item for item in items if item.kind == "metric" and item.payload["query_id"] == "db_latency")
        next(sample for sample in metric.payload["samples"] if sample["outcome"] == "p99")["value"] = 2
        metric.content_hash = sha256_json(metric.payload)
        bundle = build_evidence_bundle(target=snapshot.bundle.target, window=snapshot.bundle.window,
                                      created_at=snapshot.bundle.created_at, evidence=items)
        record = self.investigate(CapturedSnapshot(bundle, snapshot.audit))
        self.assertEqual(record.report.current_state, "unknown")

    def test_snapshot_retries_reuse_run_but_changed_audit_cannot_reuse_request_id(self):
        first = self.investigate()
        second = self.investigate()
        self.assertEqual((first.incident_id, first.run_id), (second.incident_id, second.run_id))
        audit = self.snapshot.audit.model_copy(deep=True)
        audit.raw_bytes_read += 1
        with self.assertRaisesRegex(ValueError, "different input"):
            self.investigate(CapturedSnapshot(self.snapshot.bundle, audit))

    def test_mutated_bundle_hash_audit_and_settings_fail_before_incident_creation(self):
        scenarios = []
        bundle = self.snapshot.bundle.model_copy(deep=True)
        bundle.evidence[0].payload["ready"] = False
        scenarios.append(CapturedSnapshot(bundle, self.snapshot.audit))
        audit = self.snapshot.audit.model_copy(deep=True)
        audit.entries[0].status = "missing"
        scenarios.append(CapturedSnapshot(self.snapshot.bundle, audit))
        audit = self.snapshot.audit.model_copy(deep=True)
        audit.config_hash = "a" * 64
        scenarios.append(CapturedSnapshot(self.snapshot.bundle, audit))
        for snapshot in scenarios:
            with self.subTest(snapshot=snapshot.audit.config_hash), self.assertRaises(ValueError):
                self.investigate(snapshot)
        self.assertFalse(list(self.store_root.glob("inc-*")))

    def test_extra_captured_payload_fields_are_rejected_even_with_recomputed_hash(self):
        bundle = self.snapshot.bundle
        items = [item.model_copy(deep=True) for item in bundle.evidence]
        items[0].payload["private_user_name"] = SENTINEL
        items[0].content_hash = sha256_json(items[0].payload)
        changed = build_evidence_bundle(target=bundle.target, window=bundle.window, created_at=bundle.created_at, evidence=items)
        with self.assertRaises(ValueError) as error:
            self.investigate(CapturedSnapshot(changed, self.snapshot.audit))
        self.assertNotIn(SENTINEL, str(error.exception))

    def test_show_rejects_changed_collection_audit_or_false_healthy_report(self):
        record = self.investigate()
        run_dir = self.store_root / record.incident_id / record.run_id
        audit_path = run_dir / "collection-audit.json"
        original = audit_path.read_bytes()
        envelope = json.loads(original)
        envelope["audit"]["raw_bytes_read"] += 1
        audit_path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(ValueError, "collection audit"):
            self.store.show(record.incident_id, record.run_id)
        audit_path.write_bytes(original)
        path = run_dir / "report.json"
        report = json.loads(path.read_text())
        report["current_state"] = "healthy"
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "collection state"):
            self.store.show(record.incident_id, record.run_id)

    def test_captured_is_denied_before_model_budget_key_and_all_adapter_transports(self):
        bundle = self.snapshot.bundle
        with self.assertRaises(ModelDataDenied):
            ensure_external_model_allowed(bundle)
        config = AIConfig(account_label="no-network")
        transport, token_counter, analyzer = Mock(), Mock(), Mock()
        with self.assertRaises(ModelDataDenied):
            self.store.investigate_bundle(bundle=bundle, registry=self.registry, question="분석", request_id="blocked",
                mode="ai", ai_config=config, analyzer=analyzer)
        record = self.investigate()
        for engine in (analyze_ai, analyze_fixed_summary):
            with self.subTest(engine=engine.__name__), self.assertRaisesRegex(AIAnalysisError, "captured_data_not_approved"):
                engine(record.request, bundle, record.run_id, config, "unused-key", transport, token_counter)
        model = Mock()
        with self.assertRaises(ModelDataDenied):
            run_replay(request=record.request, bundle=bundle, run_id=record.run_id, model=model)
        transport.assert_not_called()
        token_counter.assert_not_called()
        analyzer.assert_not_called()
        model.next_action.assert_not_called()
        self.assertFalse((self.store_root / "ai-budget.json").exists())
        with self.assertRaisesRegex(ValueError, "built-in rules"):
            self.store.investigate_bundle(bundle=bundle, registry=self.registry, question="분석", request_id="custom", analyzer=analyzer)

    def test_production_or_mixed_bundle_is_not_approved_for_models(self):
        synthetic = self.snapshot.bundle.model_copy(deep=True)
        synthetic.evidence[0].provenance = synthetic.evidence[0].provenance.model_validate(
            {"kind": "synthetic", "origin": "test", "fixture_version": "test"})
        with self.assertRaises(ModelDataDenied):
            ensure_external_model_allowed(synthetic)
        production = synthetic.model_copy(deep=True)
        for item in production.evidence:
            item.provenance = item.provenance.model_validate({"kind": "synthetic", "origin": "test", "fixture_version": "test"})
        production.target.environment = "production"
        with self.assertRaises(ModelDataDenied):
            ensure_external_model_allowed(production)

    def cli_args(self):
        return ["collect-investigate", "--bindings", str(ROOT / "config/targets.practice.example.json"),
                "--runtime", str(ROOT / "config/b-runtime.example.json"), "--metric-queries", str(ROOT / "config/metric-queries.json"),
                "--target", "url-shortener-practice", "--store", str(self.store_root),
                "--question", "현 상태를 설명해줘", "--request-id", "cli-captured"]

    def test_cli_collect_rules_and_show_partial_without_key_lookup(self):
        output = io.StringIO()
        with patch("ai_ops.cli.collect_snapshot", return_value=self.snapshot) as collect, patch("ai_ops.cli.api_key_from_environment") as key:
            with contextlib.redirect_stdout(output):
                result = main(self.cli_args())
            self.assertEqual(result, 1)
            self.assertIn("수집 자료가 불완전", output.getvalue())
            self.assertNotIn("AI 실패", output.getvalue())
            key.assert_not_called()
            collect.assert_called_once()
        from ai_ops.observability import Telemetry
        metrics = Telemetry(self.store_root).render().decode()
        self.assertIn('stage="collection",result="partial"} 1', metrics)
        self.assertIn('stage="report",result="partial"} 1', metrics)
        incident = next(path for path in self.store_root.glob("inc-*"))
        run = next(path for path in incident.glob("run-*"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["show", "--store", str(self.store_root), "--incident", incident.name, "--run", run.name, "--format", "json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["failure_reason"], "collection_incomplete")

    def test_cli_unknown_target_and_ai_mode_fail_before_collection(self):
        with patch("ai_ops.cli.collect_snapshot") as collect:
            args = self.cli_args()
            args[args.index("--target") + 1] = "foreign-target"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args), 2)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(self.cli_args() + ["--mode", "ai"])
            self.assertEqual(error.exception.code, 2)
            collect.assert_not_called()

    def test_legacy_file_cli_rejects_captured_ai_before_reading_key(self):
        destination = Path(self.directory.name) / "captured-file"
        shutil.copytree(ROOT / "evals/fixtures/dev/s1-cache-error", destination)
        path = destination / "manifest.json"
        manifest = json.loads(path.read_text())
        for entry in manifest["entries"]:
            entry["provenance"] = {"kind": "captured", "origin": "local-captured-file"}
        path.write_text(json.dumps(manifest))
        with patch("ai_ops.cli.api_key_from_environment") as key, patch.object(IncidentStore, "load_api_key") as stored_key:
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["investigate", "--bundle", str(destination), "--registry", str(ROOT / "config/targets.example.json"),
                             "--store", str(self.store_root), "--mode", "ai", "--question", "분석", "--request-id", "blocked-file"])
            self.assertEqual(code, 2)
            key.assert_not_called()
            stored_key.assert_not_called()
        self.assertFalse(list(self.store_root.glob("inc-*")))

    def test_large_missing_collection_is_bounded_in_report_without_losing_audit(self):
        builder = SnapshotBuilder(self.settings, self.target, clock=Clock())
        for index in range(40):
            add(builder, "missing-" + str(index), "event", failure=ReadFailure("missing", "timeout"))
        record = self.investigate(builder.finish())
        self.assertEqual(len(record.report.missing_evidence), 30)
        self.assertEqual(len(record.collection_audit.entries), 40)
        self.assertIn("최대 30개", record.markdown)


if __name__ == "__main__":
    unittest.main()
