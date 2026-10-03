"""익명 A/B 검토 패킷과 채점기 입력 변환의 경계를 확인한다."""

from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from prepare_blind_review import build_package, import_reviews, render_html


def packets() -> tuple[dict, dict]:
    """동일 증거와 서로 다른 보고서를 갖는 16건을 만든다."""
    common = {
        "schema_version": 1,
        "suite_id": "a7-holdout-v3",
        "suite_hash": "suite-hash",
        "rubric_hash": "rubric-hash",
        "first_only": True,
    }
    outputs = []
    for mode in ("fixed", "ai"):
        cases = []
        for number in range(1, 17):
            case_id = f"case-{number:02d}"
            cases.append({
                "case_id": case_id,
                "class": "incident" if number <= 8 else "normal",
                "scenario": "S1" if number <= 8 else "S2",
                "top1_criterion": "첫 원인 후보 확인",
                "forbidden_claim": "없는 값을 단정하지 않음",
                "required_evidence_ids": ["e-1"],
                "evidence": [{"evidence_id": "e-1", "status": "available"}],
                "reports": [{"repeat": 1, "facts": [{"text": f"보고서 {mode}"}]}],
            })
        outputs.append({**common, "mode": mode, "cases": cases})
    return tuple(outputs)


class BlindReviewTest(unittest.TestCase):
    def test_balanced_aliases_and_html_data_boundary(self):
        fixed, ai = packets()
        reviewer_data, private_map = build_package(fixed, ai, rng=random.Random(7), package_id="packet-1")

        self.assertEqual(len(reviewer_data["cases"]), 16)
        self.assertNotIn("mode", reviewer_data)
        self.assertEqual(sum(row["A"] == "fixed" for row in private_map["assignments"].values()), 8)
        self.assertEqual({report["alias"] for case in reviewer_data["cases"] for report in case["reports"]},
                         {"A", "B"})
        self.assertNotIn("assignments", reviewer_data)

        reviewer_data["cases"][0]["evidence"][0]["content"] = "</script><script>alert(1)</script>"
        html = render_html(reviewer_data, "<script type='application/json'>__A7_REVIEW_DATA__</script>")
        self.assertNotIn("</script><script>alert", html)
        embedded = html.split(">", 1)[1].rsplit("</script>", 1)[0]
        self.assertEqual(json.loads(embedded)["cases"][0]["evidence"][0]["content"],
                         "</script><script>alert(1)</script>")

    def test_complete_review_maps_to_existing_scorer_format(self):
        fixed, ai = packets()
        reviewer_data, private_map = build_package(fixed, ai, rng=random.Random(3), package_id="packet-2")
        rows = [{
            "case_id": case["case_id"], "alias": alias,
            "top1_correct": True if case["class"] == "incident" else None,
            "evidence_semantically_supported": True,
            "forbidden_claim_present": False,
            "note": "e-1과 보고서 문장을 대조함",
        } for case in reviewer_data["cases"] for alias in ("A", "B")]
        submitted = {
            "schema_version": 1, "package_id": "packet-2", "suite_hash": "suite-hash",
            "reviewer": "external-reviewer", "independent_attestation": True,
            "reviews": rows,
        }
        templates = [{
            "schema_version": 1, "suite_hash": "suite-hash", "mode": mode,
            "reviews": [{"case_id": f"case-{number:02d}", "repeat": 1} for number in range(1, 17)],
        } for mode in ("fixed", "ai")]

        fixed_review, ai_review = import_reviews(submitted, private_map, *templates)
        self.assertEqual((len(fixed_review["reviews"]), len(ai_review["reviews"])), (16, 16))
        self.assertEqual({row["reviewer"] for row in fixed_review["reviews"]}, {"external-reviewer"})
        self.assertIsNone(fixed_review["reviews"][-1]["top1_correct"])
        with self.assertRaisesRegex(ValueError, "32건"):
            import_reviews({**submitted, "reviews": rows[:-1]}, private_map, *templates)
        with self.assertRaisesRegex(ValueError, "독립 검토"):
            import_reviews({**submitted, "independent_attestation": False}, private_map, *templates)


if __name__ == "__main__":
    unittest.main()
