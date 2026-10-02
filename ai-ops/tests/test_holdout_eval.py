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
from ai_ops.openai_engine import AIAnalysisError, AIConfig


ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / "evals/fixtures/holdout"


class HoldoutEvaluationTest(unittest.TestCase):
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
        with self.assertRaisesRegex(AIAnalysisError, "invalid_ai_report_contract"):
            analyze_fixed_summary(request, bundle, run_id, AIConfig("eval-test"), "fake-key",
                                  transport=transport, token_counter=lambda *_: 100)
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
