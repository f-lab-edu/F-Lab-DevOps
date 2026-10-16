"""A7 수동 분석 화면의 정답 분리와 제출 기록의 검증 경계를 확인한다."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from prepare_manual_baseline import build_packet, render
from score_manual_baseline import evaluate


class ManualBaselineTest(unittest.TestCase):
    def test_generated_packet_contains_only_source_evidence(self):
        packet = build_packet()
        self.assertEqual(len(packet["cases"]), 16)
        self.assertEqual(set(packet), {"schema_version", "suite_hash", "cases"})
        self.assertEqual(set(packet["cases"][0]), {"case_id", "target", "window", "evidence"})
        self.assertTrue(packet["cases"][0]["evidence"][0]["raw"])
        self.assertIsNone(packet["cases"][14]["evidence"][0]["raw"])
        for case in packet["cases"]:
            self.assertTrue(all("report" not in key and "criterion" not in key for key in case))
        sample = {"value": "</script><script>alert(1)</script>"}
        html = render(sample, "<script type='application/json'>__MANUAL_BASELINE_DATA__</script>")
        self.assertNotIn("</script><script>alert", html)
        self.assertEqual(json.loads(html.split(">", 1)[1].rsplit("</script>", 1)[0]), sample)

    def test_scoring_keeps_top1_unscored_and_rejects_bad_time(self):
        suite = json.loads((ROOT / "evals/fixtures/holdout-v3/suite.json").read_text())
        rubric = json.loads((ROOT / "evals/rubrics/holdout-v3/scenarios.json").read_text())
        criteria = {case["case_id"]: case for case in rubric["cases"]}
        rows = []
        for case in suite["cases"]:
            rule = criteria[case["case_id"]]
            rows.append({
                "case_id": case["case_id"], "analyst_id": "analyst-test",
                "started_at_kst": "2026-10-15T10:00:00+09:00",
                "ended_at_kst": "2026-10-15T10:01:00+09:00",
                "diagnosis_status": rule["expected_diagnosis"],
                "current_state": rule["expected_state"],
                "top1_cause": "검토가 필요한 가설" if rule["expected_diagnosis"] == "suspected_cause" else "",
                "evidence_ids": "", "rationale": "원본을 읽고 작성한 시험 기록",
                "uncertainty": "", "external_sources": "없음", "prior_exposure": "seen",
            })
        result = evaluate(rows, suite, rubric)
        self.assertEqual(result["diagnosis_status_matches"], 16)
        self.assertEqual(result["duration_seconds_total"], 16 * 60)
        self.assertIsNone(result["top1_semantic_score"])
        rows[0]["started_at_kst"] = "2026-10-15T10:00:00Z"
        with self.assertRaisesRegex(ValueError, "KST"):
            evaluate(rows, suite, rubric)


if __name__ == "__main__":
    unittest.main()
