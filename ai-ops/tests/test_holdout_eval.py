"""A7 고정 평가 입력의 분리·해시와 채점 경계를 확인한다."""

import hashlib
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ai_ops.contracts import canonical_json_bytes, create_incident_request, load_target_registry
from ai_ops.evidence import load_evidence_bundle
from ai_ops.fixed_summary import analyze_fixed_summary
from ai_ops.incidents import IncidentStore
from ai_ops.openai_engine import AIAnalysisError, AIConfig, OpenAISelectionModel
from ai_ops.replay_runner import run_replay


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "evals/fixtures/holdout"
HOLDOUT_V2 = ROOT / "evals/fixtures/holdout-v2"
HOLDOUT_V3 = ROOT / "evals/fixtures/holdout-v3"
V2_SUITE_HASH = "18daeef8c697bf9f02c9f9f3bda29affef3586f1b797d524a494424227aadd39"
V2_RUBRIC_HASH = "6b0d07f1076076ccf8e2d7f187ca5055a3ad6197a092dbd56d8d34b3cb642889"
V3_SUITE_HASH = "a08c462cffebdeefc41a085714f08b321813e66f2a94646bd036addcc275a7e1"
V3_RUBRIC_HASH = "49082d809e0a7f76c42e4eab1e99f87ab0d6b5cad67dc2adb65acba345c66fbb"


class HoldoutEvaluationTest(unittest.TestCase):
    def test_v3_inputs_and_rubric_are_frozen_before_model_calls(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_holdout_v3_fixtures.py"), "--check"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        suite = json.loads((HOLDOUT_V3 / "suite.json").read_text(encoding="utf-8"))
        rubric = json.loads((ROOT / "evals/rubrics/holdout-v3/scenarios.json").read_text(encoding="utf-8"))
        self.assertEqual((suite["suite_hash"], rubric["rubric_hash"]),
                         (V3_SUITE_HASH, V3_RUBRIC_HASH))
        self.assertEqual({row["case_id"] for row in suite["cases"]},
                         {row["case_id"] for row in rubric["cases"]})
        self.assertEqual({kind: sum(row["class"] == kind for row in rubric["cases"])
                          for kind in ("incident", "normal", "insufficient")},
                         {"incident": 8, "normal": 6, "insufficient": 2})
        old_hashes = {row["input_hash"] for folder in (HOLDOUT, HOLDOUT_V2)
                      for row in json.loads((folder / "suite.json").read_text(encoding="utf-8"))["cases"]}
        registry = load_target_registry(ROOT / "config/targets.example.json")
        for row in suite["cases"]:
            bundle = load_evidence_bundle(HOLDOUT_V3 / row["bundle"], registry)
            self.assertEqual(bundle.bundle_hash, row["input_hash"])
            self.assertNotIn(bundle.bundle_hash, old_hashes)
            self.assertTrue(all(item.provenance.kind == "synthetic" and
                                item.provenance.origin == "a7-holdout-v3" for item in bundle.evidence))

    def test_v2_inputs_and_rubric_are_frozen_and_distinct(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_holdout_v2_fixtures.py"), "--check"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        suite = json.loads((HOLDOUT_V2 / "suite.json").read_text(encoding="utf-8"))
        rubric = json.loads((ROOT / "evals/rubrics/holdout-v2/scenarios.json").read_text(encoding="utf-8"))
        self.assertEqual(suite["suite_hash"], V2_SUITE_HASH)
        self.assertEqual(rubric["rubric_hash"], V2_RUBRIC_HASH)
        self.assertEqual(rubric["rubric_hash"], hashlib.sha256(canonical_json_bytes(
            {key: value for key, value in rubric.items() if key != "rubric_hash"})).hexdigest())
        self.assertEqual(len(suite["cases"]), 16)
        self.assertEqual({row["case_id"] for row in suite["cases"]},
                         {row["case_id"] for row in rubric["cases"]})
        older = json.loads((HOLDOUT / "suite.json").read_text(encoding="utf-8"))
        old_hashes = {row["input_hash"] for row in older["cases"]}
        registry = load_target_registry(ROOT / "config/targets.example.json")
        for row in suite["cases"]:
            self.assertNotIn(row["input_hash"], old_hashes)
            bundle = load_evidence_bundle(HOLDOUT_V2 / row["bundle"], registry)
            self.assertEqual(bundle.bundle_hash, row["input_hash"])
            self.assertTrue(all(item.provenance.kind == "synthetic" for item in bundle.evidence))
            self.assertTrue(all(item.provenance.origin == "a7-holdout-v2" for item in bundle.evidence))

    def test_v2_runner_and_scorer_use_separate_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, str(ROOT / "scripts/run_holdout.py"),
                       "--suite", str(HOLDOUT_V2 / "suite.json"), "--mode", "rules",
                       "--repeats", "1", "--store", directory, "--case", "case-01"]
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            runs = json.loads((Path(directory) / "a7-v2-rules-results.json").read_text(encoding="utf-8"))
            self.assertEqual(runs["suite_hash"], V2_SUITE_HASH)
            self.assertEqual(runs["results"][0]["status"], "completed")
            scorer = runpy.run_path(str(ROOT / "scripts/score_holdout.py"))["score"]
            rubric = json.loads((ROOT / "evals/rubrics/holdout-v2/scenarios.json").read_text(encoding="utf-8"))
            suite = json.loads((HOLDOUT_V2 / "suite.json").read_text(encoding="utf-8"))
            self.assertEqual(scorer(runs, rubric, suite, HOLDOUT_V2 / "suite.json")["completed_runs"], 1)
            with self.assertRaisesRegex(ValueError, "해시"):
                scorer(runs, {**rubric, "review_rule": "변경"}, suite, HOLDOUT_V2 / "suite.json")
            packet = Path(directory) / "review-packet.json"
            template = Path(directory) / "review-template.json"
            export = subprocess.run(
                [sys.executable, str(ROOT / "scripts/export_holdout_review.py"),
                 "--suite", str(HOLDOUT_V2 / "suite.json"),
                 "--rubric", str(ROOT / "evals/rubrics/holdout-v2/scenarios.json"),
                 "--runs", str(Path(directory) / "a7-v2-rules-results.json"),
                 "--packet", str(packet), "--template", str(template), "--first-only"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(export.returncode, 0, export.stderr or export.stdout)
            review = json.loads(packet.read_text(encoding="utf-8"))
            blanks = json.loads(template.read_text(encoding="utf-8"))
            self.assertEqual((len(review["cases"]), len(blanks["reviews"])), (1, 1))
            self.assertEqual(review["cases"][0]["scenario"], "S1")
            self.assertTrue(all(item["provenance"]["kind"] == "synthetic"
                                for item in review["cases"][0]["evidence"]))
            self.assertIsNone(blanks["reviews"][0]["top1_correct"])

    def test_v3_runner_and_scorer_keep_the_new_suite_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_holdout.py"),
                 "--suite", str(HOLDOUT_V3 / "suite.json"), "--mode", "rules",
                 "--case", "case-01", "--store", directory],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            runs = json.loads((Path(directory) / "a7-v3-rules-results.json").read_text(encoding="utf-8"))
            self.assertEqual(runs["suite_hash"], V3_SUITE_HASH)
            self.assertEqual(runs["results"][0]["status"], "completed")
            scorer = runpy.run_path(str(ROOT / "scripts/score_holdout.py"))["score"]
            rubric = json.loads((ROOT / "evals/rubrics/holdout-v3/scenarios.json").read_text(encoding="utf-8"))
            suite = json.loads((HOLDOUT_V3 / "suite.json").read_text(encoding="utf-8"))
            self.assertEqual(scorer(runs, rubric, suite, HOLDOUT_V3 / "suite.json")["completed_runs"], 1)

    def test_tool_results_are_sent_once_within_the_input_limit(self):
        registry = load_target_registry(ROOT / "config/targets.example.json")
        bundle = load_evidence_bundle(HOLDOUT / "case-02", registry)
        request = create_incident_request(
            request_id="a7-size-regression", target=bundle.target,
            question="제공된 시간대의 서비스 상태와 가능한 원인을 근거·불확실성과 함께 설명해 주세요.",
            window=bundle.window, bundle=bundle, registry=registry,
            requested_at=datetime.now(timezone.utc),
        )
        actions = ("query_service_metrics", "get_pod_logs", "get_deployment_context")
        sizes = []
        delivered_by_call = []

        def transport(body, _key, _timeout):
            sizes.append(len(canonical_json_bytes(body)))
            if len(sizes) > 1:
                payload = json.loads(body["input"][-1]["output"])
                delivered_by_call.append([item["evidence_id"] for item in payload["provided_evidence"]])
            if len(sizes) <= len(actions):
                name = actions[len(sizes) - 1]
                arguments = {"target": request.target.model_dump(mode="json"),
                             "window": request.window.model_dump(mode="json")}
                if name == "query_service_metrics":
                    arguments["query_id"] = "cache_outcomes"
                if name == "get_pod_logs":
                    arguments["container"] = "all"
                output = [{"type": "function_call", "call_id": f"call-{len(sizes)}",
                           "name": name, "arguments": json.dumps(arguments)}]
            else:
                output = [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}]
            return {"status": "completed", "model": "gpt-6-sol",
                    "usage": {"input_tokens": 100, "output_tokens": 100}, "output": output}

        model = OpenAISelectionModel(AIConfig("size-test"), "fake-key", transport,
                                     token_counter=lambda *_: 100)
        replay = run_replay(request=request, bundle=bundle, run_id="run-" + uuid.uuid4().hex,
                            model=model)
        self.assertEqual((replay.status, replay.tool_calls, len(sizes)), ("completed", 3, 4))
        self.assertTrue(all(size <= model.config.max_input_bytes for size in sizes))
        delivered_ids = [item for batch in delivered_by_call for item in batch]
        self.assertEqual(len(delivered_ids), len(set(delivered_ids)))

    def test_a7_budget_continues_without_clearing_existing_daily_reservations(self):
        runner = runpy.run_path(str(ROOT / "scripts/run_holdout.py"))
        self.assertNotEqual(runner["_request_id"]("ai", "case-02", 1, 1),
                            "a7-ai-case-02-r1-a1")
        self.assertIn(runner["CONTEXT_VERSION"], runner["_request_id"]("ai", "case-02", 1, 1))
        self.assertIn("fixed-v2", runner["_request_id"]("fixed", "case-09", 1, 1,
                                                        "a7-holdout-v2"))
        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory))
            ledger = {"schema_version": 1,
                      "days": {datetime.now(timezone.utc).date().isoformat(): 1.0}}
            (store.root / "ai-budget.json").write_text(json.dumps(ledger), encoding="utf-8")
            self.assertFalse(runner["_daily_budget_available"](store, AIConfig("eval-test")))
            config = AIConfig("eval-test", per_day_usd=runner["EVAL_DAILY_BUDGET_USD"])
            self.assertTrue(runner["_daily_budget_available"](store, config))

    def test_a7_temporary_budget_override_has_a_hard_cap(self):
        command = [sys.executable, str(ROOT / "scripts/run_holdout.py"),
                   "--mode", "rules", "--daily-budget-usd", "30.01"]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("$0.10~$30", result.stderr)

    def test_runner_can_select_cases_without_changing_frozen_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, str(ROOT / "scripts/run_holdout.py"),
                       "--mode", "rules", "--repeats", "1", "--store", directory]
            for case in ("case-03", "case-05"):
                result = subprocess.run(command + ["--case", case], capture_output=True,
                                        text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            runs = json.loads((Path(directory) / "a7-rules-results.json").read_text(encoding="utf-8"))
            self.assertEqual([item["case_id"] for item in runs["results"]], ["case-03", "case-05"])
            self.assertEqual(len(runs["input_hashes"]), 16)

    def test_runner_can_expand_repeat_target_without_repeating_completed_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, str(ROOT / "scripts/run_holdout.py"),
                       "--mode", "rules", "--case", "case-03", "--store", directory]
            for repeats in ("1", "3"):
                result = subprocess.run(command + ["--repeats", repeats],
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            runs = json.loads((Path(directory) / "a7-rules-results.json").read_text(encoding="utf-8"))
            self.assertEqual(runs["repeats"], 3)
            self.assertEqual([(row["repeat"], row["attempt"]) for row in runs["results"]],
                             [(1, 1), (2, 1), (3, 1)])

    def test_scorer_includes_known_cost_of_failed_model_attempts(self):
        score = runpy.run_path(str(ROOT / "scripts/score_holdout.py"))["score"]
        suite = json.loads((HOLDOUT / "suite.json").read_text(encoding="utf-8"))
        rubric = json.loads((ROOT / "evals/rubrics/holdout/scenarios.json").read_text(encoding="utf-8"))
        runs = {"suite_hash": suite["suite_hash"], "mode": "ai", "repeats": 1,
                "input_hashes": {item["case_id"]: item["input_hash"] for item in suite["cases"]},
                "results": [
                    {"case_id": "case-01", "repeat": 1, "status": "failed", "report": None,
                     "wall_ms": 5, "model_usage": {"request_attempts": 3, "usage_responses": 3,
                                                     "estimated_known_cost_usd": 0.016}},
                    {"case_id": "case-02", "repeat": 1, "status": "failed", "report": None,
                     "wall_ms": 5, "model_usage": {"request_attempts": 1, "usage_responses": 0,
                                                     "estimated_known_cost_usd": None}},
                ]}
        result = score(runs, rubric, suite)
        self.assertEqual(result["estimated_cost_usd"], 0.016)
        self.assertEqual(result["unknown_cost_attempts"], 1)

    def test_manual_review_only_publishes_complete_scores(self):
        apply_reviews = runpy.run_path(str(ROOT / "scripts/score_holdout.py"))["apply_manual_reviews"]
        result = {
            "suite_hash": "frozen", "mode": "rules", "expected_runs": 8,
            "top1_score": "수동 검토 대기",
            "semantic_evidence_score": "수동 검토 대기",
            "rows": [{"case_id": f"case-{number:02d}", "repeat": 1, "class": "incident",
                      "top1_manual_review": "pending", "evidence_semantics_review": "pending"}
                     for number in range(1, 9)],
            "manual_review": [{"case_id": f"case-{number:02d}", "repeat": 1,
                               "class": "incident", "top1_correct": None,
                               "evidence_semantically_supported": None,
                               "forbidden_claim_present": None, "reviewer": None}
                              for number in range(1, 9)],
        }
        reviews = {"schema_version": 1, "suite_hash": "frozen", "mode": "rules", "reviews": [
            {"case_id": f"case-{number:02d}", "repeat": 1,
             "top1_correct": number < 8, "evidence_semantically_supported": True,
             "forbidden_claim_present": False, "reviewer": "검토자", "note": "원본 증거와 비교함"}
            for number in range(1, 9)
        ]}
        partial = apply_reviews(json.loads(json.dumps(result)), {**reviews, "reviews": reviews["reviews"][:7]})
        self.assertEqual(partial["top1_score"], "수동 검토 대기")
        self.assertEqual(partial["semantic_evidence_score"], "수동 검토 대기")
        completed = apply_reviews(json.loads(json.dumps(result)), reviews)
        self.assertEqual(completed["top1_score"], {"correct": 7, "total": 8})
        self.assertEqual(completed["semantic_evidence_score"], {"supported": 8, "total": 8})
        self.assertEqual(completed["forbidden_claims"], 0)
        with self.assertRaisesRegex(ValueError, "중복"):
            apply_reviews(json.loads(json.dumps(result)),
                          {**reviews, "reviews": reviews["reviews"] + reviews["reviews"][:1]})

    def test_sixteen_frozen_cases_are_distinct_from_development_inputs(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_holdout_fixtures.py"), "--check"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        suite = json.loads((HOLDOUT / "suite.json").read_text(encoding="utf-8"))
        rubric = json.loads((ROOT / "evals/rubrics/holdout/scenarios.json").read_text(encoding="utf-8"))
        self.assertEqual(len(suite["cases"]), 16)
        self.assertEqual(len(rubric["cases"]), 16)
        self.assertEqual({item["case_id"] for item in suite["cases"]},
                         {item["case_id"] for item in rubric["cases"]})
        self.assertEqual(
            {scenario: sum(item["scenario"] == scenario for item in rubric["cases"])
             for scenario in ("S1", "S2", "S3", "S4", "S5", "S6", "S9", "S10")},
            {scenario: 2 for scenario in ("S1", "S2", "S3", "S4", "S5", "S6", "S9", "S10")},
        )
        registry = load_target_registry(ROOT / "config/targets.example.json")
        dev_raw = {hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (ROOT / "evals/fixtures/dev").rglob("*") if path.is_file()}
        hashes = []
        for row in suite["cases"]:
            case = HOLDOUT / row["bundle"]
            bundle = load_evidence_bundle(case, registry)
            self.assertEqual(bundle.bundle_hash, row["input_hash"])
            self.assertTrue(all(item.provenance.kind == "synthetic" for item in bundle.evidence))
            self.assertTrue(all(item.provenance.origin == "a7-holdout" for item in bundle.evidence))
            for path in case.rglob("*"):
                if path.is_file() and path.name != "manifest.json":
                    self.assertNotIn(hashlib.sha256(path.read_bytes()).hexdigest(), dev_raw)
            hashes.append(bundle.bundle_hash)
        self.assertEqual(len(set(hashes)), 16)

    def test_fixed_summary_uses_only_the_validated_bundle_and_rejects_fake_citation(self):
        registry = load_target_registry(ROOT / "config/targets.example.json")
        bundle = load_evidence_bundle(HOLDOUT / "case-01", registry)
        request = create_incident_request(
            request_id="a7-fixed-test", target=bundle.target, question="원인 후보를 설명해 주세요",
            window=bundle.window, bundle=bundle, registry=registry,
            requested_at=datetime.now(timezone.utc),
        )
        run_id = "run-" + uuid.uuid4().hex
        evidence_id = bundle.evidence[0].evidence_id
        draft = {
            "analysis_status": "complete", "diagnosis_status": "suspected_cause",
            "current_state": "unknown",
            "facts": [{"statement": "캐시 오류가 관측됐다.", "evidence_ids": [evidence_id]}],
            "hypotheses": [{"cause": "캐시 조회 오류 가능성", "supporting_evidence_ids": [evidence_id],
                            "contradicting_evidence_ids": []}],
            "missing_evidence": [], "recommended_checks": [], "suggested_actions": [], "limitations": [],
        }
        received = []
        def transport(body, _api_key, _timeout):
            received.append(json.loads(body["input"][0]["content"]))
            self.assertNotIn("tools", body)
            return {"status": "completed", "model": "gpt-6-sol", "usage": {"input_tokens": 100,
                    "output_tokens": 100}, "output": [{"type": "message", "content": [
                        {"type": "output_text", "text": json.dumps(draft, ensure_ascii=False)}]}]}
        result = analyze_fixed_summary(request, bundle, run_id, AIConfig("eval-test"), "fake-key",
                                       transport=transport, token_counter=lambda *_: 100)
        self.assertEqual(result.report.execution.engine, "openai-fixed-evidence-v1")
        self.assertEqual(result.replay.delivered_evidence_ids,
                         [item.evidence_id for item in bundle.evidence if item.status == "available"])
        self.assertEqual(len(received[0]["evidence"]), len(bundle.evidence))
        self.assertNotIn("rubric", received[0])
        draft["facts"][0]["evidence_ids"] = ["ev-made-up"]
        with self.assertRaisesRegex(AIAnalysisError, "invalid_ai_report_citation") as rejected:
            analyze_fixed_summary(request, bundle, run_id, AIConfig("eval-test"), "fake-key",
                                  transport=transport, token_counter=lambda *_: 100)
        self.assertEqual(rejected.exception.diagnostics,
                         [{"code": "invalid_ai_report_citation", "path": ""}])
        draft["diagnosis_status"] = "no_incident"
        draft["facts"][0]["evidence_ids"] = [evidence_id]
        with self.assertRaisesRegex(AIAnalysisError, "invalid_ai_report_diagnosis") as rejected:
            analyze_fixed_summary(request, bundle, run_id, AIConfig("eval-test"), "fake-key",
                                  transport=transport, token_counter=lambda *_: 100)
        self.assertEqual(rejected.exception.diagnostics,
                         [{"code": "diagnosis_unexpected_hypothesis", "path": ""}])
        draft["diagnosis_status"] = "suspected_cause"
        valid_hypotheses = draft["hypotheses"]
        draft["hypotheses"] = []
        with self.assertRaisesRegex(AIAnalysisError, "invalid_ai_report_diagnosis") as rejected:
            analyze_fixed_summary(request, bundle, run_id, AIConfig("eval-test"), "fake-key",
                                  transport=transport, token_counter=lambda *_: 100)
        self.assertEqual(rejected.exception.diagnostics,
                         [{"code": "diagnosis_missing_hypothesis", "path": ""}])
        draft["hypotheses"] = valid_hypotheses
        def malformed(_body, _key, _timeout):
            return {"status": "completed", "model": "gpt-6-sol",
                    "usage": {"input_tokens": 100, "output_tokens": 100},
                    "output": [{"type": "message", "content": None}]}
        with self.assertRaisesRegex(AIAnalysisError, "invalid_model_output"):
            analyze_fixed_summary(request, bundle, run_id, AIConfig("eval-test"), "fake-key",
                                  transport=malformed, token_counter=lambda *_: 100)

        with tempfile.TemporaryDirectory() as directory:
            store = IncidentStore(Path(directory))
            saved = store.investigate(
                bundle_root=HOLDOUT / "case-01", registry=registry, question=request.question,
                request_id="a7-fixed-store-test", mode="ai", ai_config=AIConfig("eval-test"),
                analyzer=lambda req, data, run: analyze_fixed_summary(
                    req, data, run, AIConfig("eval-test"), "fake-key",
                    transport=lambda body, key, timeout: {
                        "status": "completed", "model": "gpt-6-sol",
                        "usage": {"input_tokens": 100, "output_tokens": 100},
                        "output": [{"type": "message", "content": [{"type": "output_text",
                                    "text": json.dumps({**draft, "facts": [{"statement": "캐시 오류가 관측됐다.",
                                                             "evidence_ids": [evidence_id]}]}, ensure_ascii=False)}]}],
                    }, token_counter=lambda *_: 100),
            )
            restored = store.show(saved.incident_id, saved.run_id)
            self.assertEqual(restored.report.execution.engine, "openai-fixed-evidence-v1")
            self.assertEqual(restored.metadata["status"], "completed")

            def rejected_transport(_body, _key, _timeout):
                raise AIAnalysisError("model_http_401")
            partial = store.investigate(
                bundle_root=HOLDOUT / "case-01", registry=registry, question=request.question,
                request_id="a7-fixed-failure-test", mode="ai", ai_config=AIConfig("eval-test"),
                analyzer=lambda req, data, run: analyze_fixed_summary(
                    req, data, run, AIConfig("eval-test"), "fake-key",
                    transport=rejected_transport, token_counter=lambda *_: 100),
            )
            self.assertEqual((partial.metadata["status"], partial.metadata["failure_reason"]),
                             ("partial", "model_http_401"))
            self.assertEqual(store.show(partial.incident_id, partial.run_id).report.execution.engine,
                             "rules-fallback-v1")

    def test_all_fixed_summary_inputs_fit_the_frozen_byte_limit(self):
        registry = load_target_registry(ROOT / "config/targets.example.json")
        for folder in sorted(HOLDOUT.glob("case-*")):
            with self.subTest(case=folder.name):
                bundle = load_evidence_bundle(folder, registry)
                request = create_incident_request(
                    request_id="a7-size-test", target=bundle.target, question="상태를 설명해 주세요",
                    window=bundle.window, bundle=bundle, registry=registry,
                    requested_at=datetime.now(timezone.utc),
                )
                draft = {"analysis_status": "complete", "diagnosis_status": "insufficient_evidence",
                         "current_state": "unknown", "facts": [], "hypotheses": [],
                         "missing_evidence": [], "recommended_checks": [],
                         "suggested_actions": [], "limitations": []}
                def transport(body, _key, _timeout):
                    self.assertLessEqual(len(canonical_json_bytes(body)), 16_000)
                    return {"status": "completed", "model": "gpt-6-sol",
                            "usage": {"input_tokens": 100, "output_tokens": 100},
                            "output": [{"type": "message", "content": [
                                {"type": "output_text", "text": json.dumps(draft)}]}]}
                analyze_fixed_summary(request, bundle, "run-" + uuid.uuid4().hex,
                                      AIConfig("eval-test"), "fake-key", transport=transport,
                                      token_counter=lambda *_: 100)

if __name__ == "__main__":
    unittest.main()
