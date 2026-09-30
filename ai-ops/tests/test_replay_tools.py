"""A4 저장 증거 조회·실행 한도·감사 기록을 합성 입력으로 검증한다."""

import json
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ops.contracts import load_target_registry
from ai_ops.evidence import load_evidence_bundle
from ai_ops.incidents import IncidentStore
from ai_ops.replay_runner import (
    ReplayLimits, ScriptedModel, Stop, ToolCall, TransientModelError, run_replay,
)
from ai_ops.replay_tools import ToolDenied, query_bundle


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/fixtures/dev"
REGISTRY = ROOT / "config/targets.example.json"


class ReplayToolTest(unittest.TestCase):
    def setUp(self):
        self.registry = load_target_registry(REGISTRY)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = IncidentStore(Path(self.temporary.name) / "store")

    def bundle(self, case):
        return load_evidence_bundle(FIXTURES / case, self.registry)

    def record(self, case="s1-cache-error"):
        return self.store.investigate(
            bundle_root=FIXTURES / case, registry=self.registry,
            question="추가 근거를 확인해줘", request_id=case,
        )

    @staticmethod
    def args(bundle, **extra):
        return {
            "target": bundle.target.model_dump(mode="json"),
            "window": bundle.window.model_dump(mode="json"), **extra,
        }

    def test_six_read_tools_only_return_scoped_material(self):
        cases = [
            ("s6-healthy", "get_workload_status", lambda b: {"target": b.target.model_dump(mode="json")}, "ev-s6-workload"),
            ("s3-image-tag", "get_recent_events", lambda b: self.args(b), "ev-s3-event"),
            ("s1-cache-error", "get_pod_logs", lambda b: self.args(b, container="all"), "ev-s1-log"),
            ("s1-cache-error", "query_service_metrics", lambda b: self.args(b, query_id="cache_outcomes"), "ev-s1-metric"),
            ("s10-rollback-recovered", "get_deployment_context", lambda b: self.args(b), "ev-s10-failed"),
        ]
        for case, name, build_args, evidence_id in cases:
            with self.subTest(name=name):
                bundle = self.bundle(case)
                response = query_bundle(bundle, name, build_args(bundle))
                self.assertIn(evidence_id, response.evidence_ids)
                self.assertTrue(all(item["target"] == bundle.target.model_dump(mode="json") for item in response.evidence))
        runbook = query_bundle(self.bundle("s1-cache-error"), "get_runbook", {"document_id": "cache"})
        self.assertEqual(runbook.runbook["document_id"], "cache")
        self.assertTrue(runbook.runbook["content"])
        self.assertEqual(runbook.evidence_ids, [])

    def test_metric_subwindow_does_not_expose_outside_samples(self):
        bundle = self.bundle("s1-cache-error")
        args = self.args(bundle, query_id="cache_outcomes")
        args["window"] = {"start": "2026-09-28T00:19:00Z", "end": "2026-09-28T00:21:00Z"}
        result = query_bundle(bundle, "query_service_metrics", args)
        self.assertEqual(result.evidence_ids, ["ev-s1-metric"])
        self.assertEqual(len(result.evidence[0]["payload"]["samples"]), 2)
        self.assertIn("source_content_hash", result.evidence[0])
        args["window"] = {"start": "2026-09-28T00:01:00Z", "end": "2026-09-28T00:02:00Z"}
        self.assertEqual(query_bundle(bundle, "query_service_metrics", args).status, "unavailable")

    def test_modified_runbook_is_rejected_by_index_hash(self):
        root = Path(self.temporary.name) / "runbooks"
        root.mkdir()
        shutil.copy(ROOT / "runbooks/index.yaml", root / "index.yaml")
        (root / "cache.md").write_text("바뀐 내용", encoding="utf-8")
        with patch("ai_ops.replay_tools.RUNBOOK_ROOT", root):
            with self.assertRaisesRegex(ToolDenied, "runbook_hash_mismatch"):
                query_bundle(self.bundle("s1-cache-error"), "get_runbook", {"document_id": "cache"})

    def test_fake_model_receives_only_requested_evidence_and_private_audit(self):
        record = self.record()
        bundle = record.bundle
        model = ScriptedModel([
            ToolCall("query_service_metrics", self.args(bundle, query_id="cache_outcomes")),
            ToolCall("get_pod_logs", self.args(bundle, container="all")),
            Stop(),
        ])
        result = self.store.replay(record.incident_id, record.run_id, model)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_calls, 2)
        self.assertEqual(result.delivered_evidence_ids, ["ev-s1-metric", "ev-s1-log"])
        self.assertEqual(model.views[0]["provided_evidence"], [])
        self.assertEqual([item["evidence_id"] for item in model.views[1]["provided_evidence"]], ["ev-s1-metric"])
        self.assertNotIn("DEMO_SECRET_SENTINEL", json.dumps(model.views, ensure_ascii=False))
        audit_path = self.store.root / record.incident_id / record.run_id / "tool-audit.json"
        self.assertEqual(stat.S_IMODE(audit_path.stat().st_mode), 0o600)
        stored = json.loads(audit_path.read_text())
        self.assertEqual(stored["run_id"], record.run_id)
        self.assertEqual(stored["delivered_evidence_ids"], result.delivered_evidence_ids)
        self.assertEqual(stored["audit"][0]["result_evidence_ids"], ["ev-s1-metric"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.replay(record.incident_id, record.run_id, ScriptedModel([Stop()]))

    def test_denies_forbidden_tools_targets_queries_and_arbitrary_arguments(self):
        record = self.record()
        args = self.args(record.bundle)
        outside = self.args(record.bundle)
        outside["target"]["namespace"] = "other-namespace"
        requests = [
            ToolCall("get_secret", {"token": "DEMO_SECRET_SENTINEL"}),
            ToolCall("shell", {"command": "cat /etc/passwd"}),
            ToolCall("patch_deployment", {"replicas": 0}),
            ToolCall("get_recent_events", outside),
            ToolCall("query_service_metrics", {**args, "query_id": "arbitrary_promql"}),
            ToolCall("get_recent_events", {**args, "path": "../../etc/passwd"}),
        ]
        result = run_replay(
            request=record.request, bundle=record.bundle, run_id=record.run_id,
            model=ScriptedModel(requests + [Stop()]),
        )
        self.assertEqual(result.tool_calls, len(requests))
        self.assertEqual(result.delivered_evidence_ids, [])
        self.assertTrue(all(item["error"] for item in result.audit))
        self.assertNotIn("DEMO_SECRET_SENTINEL", json.dumps(result.as_dict()))
        self.assertNotIn("/etc/passwd", json.dumps(result.as_dict()))
        with self.assertRaisesRegex(ToolDenied, "out_of_scope_window"):
            query_bundle(record.bundle, "get_recent_events", {
                **args, "window": {"start": "2026-09-27T23:00:00Z", "end": "2026-09-28T01:00:00Z"},
            })
        with self.assertRaisesRegex(ToolDenied, "runbook_not_registered"):
            query_bundle(record.bundle, "get_runbook", {"document_id": "unlisted"})
        with self.assertRaisesRegex(ToolDenied, "arguments_too_large"):
            query_bundle(record.bundle, "get_runbook", {"document_id": "x" * 9000})

    def test_default_call_limit_and_model_retry(self):
        record = self.record()
        calls = [ToolCall("get_recent_events", self.args(record.bundle)) for _ in range(11)]
        result = run_replay(
            request=record.request, bundle=record.bundle, run_id=record.run_id,
            model=ScriptedModel([TransientModelError(), *calls]),
        )
        self.assertEqual((result.status, result.failure_reason), ("limited", "tool_call_limit"))
        self.assertEqual(result.tool_calls, 10)
        self.assertEqual(result.audit[-1]["error"], "tool_call_limit")
        self.assertEqual(result.model_errors, 1)
        failed = run_replay(
            request=record.request, bundle=record.bundle, run_id=record.run_id,
            model=ScriptedModel([TransientModelError(), TransientModelError()]),
        )
        self.assertEqual((failed.status, failed.failure_reason), ("failed", "model_retry_exhausted"))

    def test_tool_and_total_deadlines_discard_late_results(self):
        record = self.record()
        tick = [0.0]

        def clock():
            return tick[0]

        def slow_tool(bundle, name, arguments):
            tick[0] += 11.0
            return query_bundle(bundle, name, arguments)

        with patch("ai_ops.replay_runner.query_bundle", side_effect=slow_tool):
            result = run_replay(
                request=record.request, bundle=record.bundle, run_id=record.run_id,
                model=ScriptedModel([ToolCall("get_recent_events", self.args(record.bundle))]), clock=clock,
            )
        self.assertEqual((result.status, result.failure_reason), ("limited", "tool_timeout"))
        self.assertEqual(result.delivered_evidence_ids, [])
        self.assertEqual(result.audit[0]["result_evidence_ids"], [])

        class SlowModel:
            def next_action(self, view, timeout_seconds):
                tick[0] += 121.0
                return Stop()

        tick[0] = 0.0
        result = run_replay(
            request=record.request, bundle=record.bundle, run_id=record.run_id,
            model=SlowModel(), clock=clock,
        )
        self.assertEqual((result.status, result.failure_reason), ("limited", "total_timeout"))

    def test_wall_clock_interrupts_a_slow_model(self):
        record = self.record()

        class BlockingModel:
            def next_action(self, view, timeout_seconds):
                time.sleep(0.2)
                return Stop()

        result = run_replay(
            request=record.request, bundle=record.bundle, run_id=record.run_id,
            model=BlockingModel(), limits=ReplayLimits(total_seconds=0.02),
        )
        self.assertEqual((result.status, result.failure_reason), ("limited", "total_timeout"))

    def test_wall_clock_interrupts_a_slow_tool(self):
        record = self.record()

        def blocking_tool(bundle, name, arguments):
            time.sleep(0.2)
            return query_bundle(bundle, name, arguments)

        with patch("ai_ops.replay_runner.query_bundle", side_effect=blocking_tool):
            result = run_replay(
                request=record.request, bundle=record.bundle, run_id=record.run_id,
                model=ScriptedModel([ToolCall("get_recent_events", self.args(record.bundle))]),
                limits=ReplayLimits(tool_seconds=0.02),
            )
        self.assertEqual((result.status, result.failure_reason), ("limited", "tool_timeout"))
        self.assertEqual(result.delivered_evidence_ids, [])
