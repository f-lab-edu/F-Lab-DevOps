"""A5 어댑터의 합성 입력·예산·근거 검사를 외부 호출 없이 확인한다."""

import json
import stat
import tempfile
import unittest
from pathlib import Path

from ai_ops.contracts import load_target_registry
from ai_ops.incidents import IncidentStore, RunStopped
from ai_ops.openai_engine import AIConfig, analyze_ai


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals/fixtures/dev/s1-cache-error"
REGISTRY = ROOT / "config/targets.example.json"


def response(output, usage=None):
    return {"status": "completed", "model": "gpt-6-sol",
            "usage": usage or {"input_tokens": 500, "output_tokens": 200}, "output": output}


def draft(evidence_id="ev-s1-log"):
    return {
        "analysis_status": "complete", "diagnosis_status": "suspected_cause", "current_state": "unknown",
        "facts": [{"statement": "RedisError 로그가 관측되었다.", "evidence_ids": [evidence_id]}],
        "hypotheses": [{"cause": "캐시 조회 오류 가능성", "supporting_evidence_ids": [evidence_id],
                        "contradicting_evidence_ids": []}],
        "missing_evidence": [], "recommended_checks": ["DB fallback 영향을 확인한다."],
        "suggested_actions": [{"description": "담당자 검토 후 설정을 확인한다.", "execution_status": "not_executed"}],
        "limitations": ["저장 증거만 확인했으며 현재 상태는 알 수 없다."],
    }


class OpenAIEngineTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = IncidentStore(Path(temporary.name) / "store")
        self.registry = load_target_registry(REGISTRY)
        self.config = AIConfig(account_label="local-test")

    def analyze(self, transport, request_id="ai-test", config=None):
        config = config or self.config
        return self.store.investigate(
            bundle_root=FIXTURE, registry=self.registry, question="캐시 오류를 조사해줘",
            request_id=request_id, mode="ai", ai_config=config,
            analyzer=lambda request, bundle, run_id: analyze_ai(
                request, bundle, run_id, config, "fake-test-key", transport),
        )

    def test_tool_then_report_persists_private_audit_and_usage(self):
        bodies = []

        def transport(body, key, timeout):
            self.assertEqual(key, "fake-test-key")
            self.assertGreater(timeout, 0)
            self.assertFalse(body["store"])
            self.assertEqual(len(body["tools"]), 6)
            self.assertNotIn("DEMO_SECRET_SENTINEL", json.dumps(body, ensure_ascii=False))
            bodies.append(body)
            if len(bodies) == 1:
                view = json.loads(body["input"][0]["content"])
                return response([{"type": "function_call", "call_id": "call-1", "name": "get_pod_logs",
                                  "arguments": json.dumps({"target": view["target"], "window": view["window"],
                                                           "container": "all"})}])
            self.assertEqual(body["input"][-1]["type"], "function_call_output")
            view = json.loads(body["input"][-1]["output"])
            self.assertIn("ev-s1-log", [item["evidence_id"] for item in view["provided_evidence"]])
            return response([{"type": "message", "content": [{"type": "output_text", "text": json.dumps(draft())}]}])

        record = self.analyze(transport)
        self.assertEqual(record.metadata["status"], "completed")
        self.assertEqual(record.report.execution.mode, "ai")
        self.assertEqual(record.report.execution.input_tokens, 1000)
        self.assertEqual(record.report.execution.output_tokens, 400)
        self.assertEqual(record.report.execution.cost_usd, 0.006)
        self.assertIn("AI가 합성 저장 증거", record.markdown)
        self.assertEqual(self.store.show(record.incident_id, record.run_id).report, record.report)
        audit_path = self.store.root / record.incident_id / record.run_id / "tool-audit.json"
        self.assertEqual(stat.S_IMODE(audit_path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(audit_path.read_text())["delivered_evidence_ids"], ["ev-s1-log"])
        budget = json.loads((self.store.root / "ai-budget.json").read_text())
        self.assertEqual(list(budget["days"].values()), [0.1])

    def test_undelivered_citation_fails_and_audit_survives(self):
        calls = []
        def transport(body, _key, _timeout):
            calls.append(body)
            if len(calls) == 1:
                view = json.loads(body["input"][0]["content"])
                return response([{"type": "function_call", "call_id": "call-2", "name": "get_recent_events",
                                  "arguments": json.dumps({"target": view["target"], "window": view["window"]})}])
            return response([{"type": "message", "content": [{"type": "output_text",
                                                               "text": json.dumps(draft())}]}])

        with self.assertRaises(RunStopped) as failed:
            self.analyze(transport, "bad-citation")
        record = self.store.show(failed.exception.incident_id, failed.exception.run_id)
        self.assertEqual(record.metadata["failure_reason"], "invalid_ai_report")
        self.assertTrue((self.store.root / record.incident_id / record.run_id / "tool-audit.json").exists())

    def test_daily_and_call_budget_refuse_before_request(self):
        calls = []
        config = AIConfig(account_label="other", per_case_usd=0.001, per_day_usd=1)
        with self.assertRaises(RunStopped):
            self.analyze(lambda *args: calls.append(args) or {}, "low-budget", config)
        self.assertEqual(calls, [])
        for number in range(9):
            with self.assertRaises(RunStopped):
                self.analyze(lambda *_: response([{"type": "message", "content": [{"type": "output_text",
                                                                               "text": json.dumps(draft())}]}]),
                             f"budget-{number}")
        with self.assertRaisesRegex(ValueError, "daily AI budget limit"):
            self.analyze(lambda *_: {}, "budget-over")


if __name__ == "__main__":
    unittest.main()
