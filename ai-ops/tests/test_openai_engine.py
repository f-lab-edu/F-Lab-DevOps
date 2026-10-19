"""A5 어댑터의 합성 입력·예산·근거 검사를 외부 호출 없이 확인한다."""

import json
import io
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from ai_ops.contracts import load_target_registry
from ai_ops.incidents import IncidentStore, RunStopped
from ai_ops.openai_engine import AIAnalysisError, AIConfig, OpenAISelectionModel, _http_count_input_tokens, _http_post, _tools, analyze_ai
from ai_ops.replay_runner import TransientModelError


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

    def test_http_quota_error_is_not_retried_and_rate_limit_is_distinguished(self):
        def failure(code):
            body = json.dumps({"error": {"type": "insufficient_quota" if code == "credit_balance_exhausted" else "rate_limit_error",
                                          "code": code, "message": "비공개 응답 본문"}}).encode()
            return urllib.error.HTTPError("https://api.openai.com/v1/responses", 429, "요청 실패", {}, io.BytesIO(body))

        with patch("ai_ops.openai_engine.urllib.request.urlopen", side_effect=failure("credit_balance_exhausted")):
            with self.assertRaisesRegex(AIAnalysisError, "model_credit_balance_exhausted") as quota:
                _http_post({"model": "gpt-6-sol"}, "fake-test-key", 1)
        self.assertNotIn("비공개 응답 본문", str(quota.exception))

        with patch("ai_ops.openai_engine.urllib.request.urlopen", side_effect=failure("rate_limit_exceeded")):
            with self.assertRaises(TransientModelError) as limited:
                _http_post({"model": "gpt-6-sol"}, "fake-test-key", 1)
        self.assertEqual(limited.exception.reason, "model_rate_limited")

    def test_tool_schema_restricts_metric_ids_and_log_container(self):
        tools = {tool["name"]: tool["parameters"]["properties"] for tool in _tools()}
        self.assertIn("cache_outcomes", tools["query_service_metrics"]["query_id"]["enum"])
        self.assertEqual(tools["get_pod_logs"]["container"]["enum"], ["all"])

    def test_oversized_model_request_stops_before_transport(self):
        sent = []
        model = OpenAISelectionModel(AIConfig("size-test"), "fake-test-key",
                                     transport=lambda *args: sent.append(args))
        with self.assertRaisesRegex(AIAnalysisError, "model_input_too_large"):
            model.next_action({"question": "가" * 40_000}, 5)
        self.assertEqual(sent, [])
        self.assertGreater(model.usage_summary()["max_request_bytes"], model.config.max_input_bytes)

    def test_input_token_count_uses_matching_request_fields(self):
        body = {"model": "gpt-6-sol", "input": "합성 요청", "instructions": "읽기 전용",
                "tools": [], "text": {"format": {"type": "text"}}, "store": False,
                "max_output_tokens": 1024}
        with patch("ai_ops.openai_engine._http_post", return_value={
            "object": "response.input_tokens", "input_tokens": 73,
        }) as request:
            self.assertEqual(_http_count_input_tokens(body, "fake-test-key", 5), 73)
        sent_body = request.call_args.args[0]
        self.assertEqual(request.call_args.kwargs["path"], "/responses/input_tokens")
        self.assertNotIn("store", sent_body)
        self.assertNotIn("max_output_tokens", sent_body)
        self.assertEqual(sent_body["text"], body["text"])

    def analyze(self, transport, request_id="ai-test", config=None, token_counter=None):
        config = config or self.config
        return self.store.investigate(
            bundle_root=FIXTURE, registry=self.registry, question="캐시 오류를 조사해줘",
            request_id=request_id, mode="ai", ai_config=config,
            analyzer=lambda request, bundle, run_id: analyze_ai(
                request, bundle, run_id, config, "fake-test-key", transport, token_counter),
        )

    def test_counted_input_budget_allows_two_queries_and_final_report(self):
        calls = []
        counts = []

        def transport(body, _key, _timeout):
            calls.append(body)
            view = json.loads(body["input"][0]["content"])
            target, window = view["target"], view["window"]
            if len(calls) == 1:
                name, arguments = "query_service_metrics", {"target": target, "window": window,
                                                                "query_id": "cache_outcomes"}
            elif len(calls) == 2:
                name, arguments = "get_pod_logs", {"target": target, "window": window,
                                                     "container": "all"}
            else:
                return response([{"type": "message", "content": [{"type": "output_text",
                                                                   "text": json.dumps(draft())}]}])
            return response([{"type": "function_call", "call_id": f"call-{len(calls)}",
                              "name": name, "arguments": json.dumps(arguments)}])

        def count(body, _key, _timeout):
            counts.append(len(json.dumps(body)))
            return 2_000

        record = self.analyze(transport, "counted-three-calls", token_counter=count)
        self.assertEqual(record.metadata["status"], "completed")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(counts), 3)

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
        self.assertEqual(record.metadata["failure_reason"], "invalid_ai_report_citation")
        self.assertEqual(record.metadata["model_usage"]["request_attempts"], 2)
        self.assertEqual(record.metadata["model_usage"]["usage_responses"], 2)
        self.assertEqual(record.metadata["model_usage"]["estimated_known_cost_usd"], 0.006)
        self.assertEqual(record.metadata["validation_diagnostics"],
                         [{"code": "invalid_ai_report_citation", "path": ""}])
        self.assertTrue((self.store.root / record.incident_id / record.run_id / "tool-audit.json").exists())

    def test_inconsistent_diagnosis_is_recorded_without_model_text(self):
        calls = []

        def transport(body, _key, _timeout):
            calls.append(body)
            if len(calls) == 1:
                view = json.loads(body["input"][0]["content"])
                return response([{"type": "function_call", "call_id": "call-3", "name": "get_pod_logs",
                                  "arguments": json.dumps({"target": view["target"], "window": view["window"],
                                                           "container": "all"})}])
            invalid = draft()
            invalid["hypotheses"] = []
            return response([{"type": "message", "content": [{"type": "output_text", "text": json.dumps(invalid)}]}])

        with self.assertRaises(RunStopped) as failed:
            self.analyze(transport, "bad-diagnosis")
        record = self.store.show(failed.exception.incident_id, failed.exception.run_id)
        self.assertEqual(record.metadata["failure_reason"], "invalid_ai_report_diagnosis")
        self.assertEqual(record.metadata["model_usage"]["input_tokens"], 1000)
        self.assertEqual(record.metadata["validation_diagnostics"][0]["code"], "value_error")
        self.assertFalse((self.store.root / record.incident_id / record.run_id / "report.json").exists())

    def test_failed_second_request_keeps_known_usage_and_returns_partial_rules(self):
        calls = []

        def transport(body, _key, _timeout):
            calls.append(body)
            if len(calls) == 1:
                view = json.loads(body["input"][0]["content"])
                return response([{"type": "function_call", "call_id": "call-usage", "name": "get_pod_logs",
                                  "arguments": json.dumps({"target": view["target"], "window": view["window"],
                                                           "container": "all"})}],
                                usage={"input_tokens": 321, "output_tokens": 45})
            raise AIAnalysisError("model_http_401")

        result = self.analyze(transport, "partial-usage")
        record = self.store.show(result.incident_id, result.run_id)
        self.assertEqual(record.metadata["status"], "partial")
        self.assertEqual(record.metadata["failure_reason"], "model_http_401")
        self.assertEqual(record.report.analysis_status, "partial")
        self.assertEqual(record.report.execution.engine, "rules-fallback-v1")
        self.assertIn("AI 조사에 실패", record.markdown)
        self.assertEqual(json.loads((self.store.root / record.incident_id / record.run_id /
                                     "tool-audit.json").read_text())["delivered_evidence_ids"], ["ev-s1-log"])
        usage = dict(record.metadata["model_usage"])
        self.assertGreater(usage.pop("max_request_bytes"), 0)
        self.assertEqual(usage, {
            "request_attempts": 2, "usage_responses": 1, "input_tokens": 321,
            "output_tokens": 45, "estimated_known_cost_usd": 0.001092,
            "complete_for_attempts": False, "price_date": "2026-09-30",
        })

    def test_validation_diagnostics_record_only_safe_field_path(self):
        calls = []
        private_text = "PRIVATE_MODEL_TEXT_SENTINEL"

        def transport(body, _key, _timeout):
            calls.append(body)
            if len(calls) == 1:
                view = json.loads(body["input"][0]["content"])
                return response([{"type": "function_call", "call_id": "call-shape", "name": "get_pod_logs",
                                  "arguments": json.dumps({"target": view["target"], "window": view["window"],
                                                           "container": "all"})}])
            invalid = draft()
            invalid["facts"][0]["evidence_ids"] = [private_text]
            return response([{"type": "message", "content": [{"type": "output_text", "text": json.dumps(invalid)}]}])

        with self.assertRaises(RunStopped) as failed:
            self.analyze(transport, "safe-diagnostic")
        record = self.store.show(failed.exception.incident_id, failed.exception.run_id)
        self.assertEqual(record.metadata["failure_reason"], "invalid_ai_report_evidence_shape")
        self.assertIn("facts.0.evidence_ids.0", [item["path"] for item in record.metadata["validation_diagnostics"]])
        run_dir = self.store.root / record.incident_id / record.run_id
        self.assertFalse((run_dir / "report.json").exists())
        self.assertNotIn(private_text, (run_dir / "metadata.json").read_text())
        self.assertNotIn(private_text, (run_dir / "tool-audit.json").read_text())

    def test_daily_and_call_budget_refuse_before_request(self):
        calls = []
        config = AIConfig(account_label="other", per_case_usd=0.001, per_day_usd=1)
        low_budget = self.analyze(lambda *args: calls.append(args) or {}, "low-budget", config)
        self.assertEqual((low_budget.metadata["status"], low_budget.metadata["failure_reason"]),
                         ("partial", "case_budget_limit"))
        self.assertEqual(calls, [])
        for number in range(9):
            with self.assertRaises(RunStopped):
                self.analyze(lambda *_: response([{"type": "message", "content": [{"type": "output_text",
                                                                               "text": json.dumps(draft())}]}]),
                             f"budget-{number}")
        limited = self.analyze(lambda *_: {}, "budget-over")
        self.assertEqual((limited.metadata["status"], limited.metadata["failure_reason"]),
                         ("partial", "daily_budget_limit"))
        self.assertEqual(limited.report.execution.engine, "rules-fallback-v1")
        self.assertEqual(len(calls), 0)

    def test_timeout_fallback_keeps_evidence_and_never_claims_ai_success(self):
        calls = []

        def transport(body, _key, _timeout):
            calls.append(body)
            if len(calls) == 1:
                view = json.loads(body["input"][0]["content"])
                return response([{"type": "function_call", "call_id": "call-timeout", "name": "get_pod_logs",
                                  "arguments": json.dumps({"target": view["target"], "window": view["window"],
                                                           "container": "all"})}])
            raise TransientModelError("model_network_error")

        result = self.analyze(transport, "timeout-fallback")
        saved = self.store.show(result.incident_id, result.run_id)
        self.assertEqual((saved.metadata["status"], saved.metadata["failure_reason"]),
                         ("partial", "model_network_error"))
        self.assertEqual(saved.report.execution.mode, "rules")
        self.assertEqual(saved.report.analysis_status, "partial")
        self.assertIn("규칙 분석한 부분 결과", saved.markdown)
        self.assertEqual(json.loads((self.store.root / saved.incident_id / saved.run_id /
                                     "tool-audit.json").read_text())["delivered_evidence_ids"], ["ev-s1-log"])

    def test_total_timeout_falls_back_but_user_cancel_does_not(self):
        partial = self.store.investigate(
            bundle_root=FIXTURE, registry=self.registry, question="캐시 오류를 조사해줘",
            request_id="a6-total-timeout", mode="ai", ai_config=self.config,
            analyzer=lambda *_: (_ for _ in ()).throw(AIAnalysisError("total_timeout")),
        )
        self.assertEqual((partial.metadata["status"], partial.metadata["failure_reason"]),
                         ("partial", "total_timeout"))
        self.assertEqual(self.store.show(partial.incident_id, partial.run_id).report, partial.report)
        with self.assertRaises(RunStopped) as cancelled:
            self.analyze(lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()), "a6-cancel")
        saved = self.store.show(cancelled.exception.incident_id, cancelled.exception.run_id)
        self.assertEqual((saved.metadata["status"], saved.metadata["failure_reason"]),
                         ("cancelled", "user_cancelled"))
        self.assertIsNone(saved.report)

    def test_unsafe_model_drafts_fail_without_fallback_or_raw_text(self):
        cases = (
            ("secret", lambda item: item["facts"][0].update(
                statement="token=PRIVATE_A6_SENTINEL"), "sensitive_report_text"),
            ("control", lambda item: item["facts"][0].update(
                statement="오류\x1b[31m강조"), "unsafe_report_control_character"),
            ("execution", lambda item: item["suggested_actions"][0].update(
                description="Pod 재시작 완료"), "invalid_ai_report_execution_claim"),
        )
        for label, mutate, reason in cases:
            with self.subTest(label=label):
                calls = []
                def transport(body, _key, _timeout):
                    calls.append(body)
                    if len(calls) == 1:
                        view = json.loads(body["input"][0]["content"])
                        return response([{"type": "function_call", "call_id": "call-safety",
                                          "name": "get_pod_logs", "arguments": json.dumps({
                                              "target": view["target"], "window": view["window"],
                                              "container": "all"})}])
                    item = draft()
                    mutate(item)
                    return response([{"type": "message", "content": [{
                        "type": "output_text", "text": json.dumps(item)}]}])
                with self.assertRaises(RunStopped) as failed:
                    self.analyze(transport, "unsafe-" + label)
                saved = self.store.show(failed.exception.incident_id, failed.exception.run_id)
                self.assertEqual((saved.metadata["status"], saved.metadata["failure_reason"]),
                                 ("failed", reason))
                run_dir = self.store.root / saved.incident_id / saved.run_id
                self.assertFalse((run_dir / "report.json").exists())
                self.assertNotIn("PRIVATE_A6_SENTINEL", (run_dir / "metadata.json").read_text())

    def test_model_requested_forbidden_shell_is_denied_and_audited(self):
        calls = []
        def transport(body, _key, _timeout):
            calls.append(body)
            view = json.loads(body["input"][0]["content"])
            if len(calls) == 1:
                return response([{"type": "function_call", "call_id": "call-log", "name": "get_pod_logs",
                                  "arguments": json.dumps({"target": view["target"], "window": view["window"],
                                                           "container": "all"})}])
            if len(calls) == 2:
                return response([{"type": "function_call", "call_id": "call-shell", "name": "shell",
                                  "arguments": json.dumps({"command": "cat /etc/passwd"})}])
            return response([{"type": "message", "content": [{"type": "output_text",
                                                               "text": json.dumps(draft())}]}])
        result = self.analyze(transport, "forbidden-shell")
        audit = json.loads((self.store.root / result.incident_id / result.run_id /
                            "tool-audit.json").read_text())
        self.assertEqual(audit["audit"][1]["tool_name"], "rejected_tool")
        self.assertNotIn("/etc/passwd", json.dumps(audit))
        self.assertEqual(result.metadata["status"], "completed")


if __name__ == "__main__":
    unittest.main()
