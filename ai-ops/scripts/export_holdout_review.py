"""완료된 A7 평가에서 독립 검토용 증거·보고서와 빈 판정표를 내보낸다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_ops.contracts import AnalysisReport, load_target_registry
from ai_ops.evidence import load_evidence_bundle

from run_holdout import REGISTRY_PATH, _write_private
from score_holdout import score


def build_review_materials(runs: dict, rubric: dict, suite: dict, suite_path: Path,
                           *, first_only: bool = False) -> tuple[dict, dict]:
    """정답 판정을 채우지 않고 검증된 합성 증거와 완료 보고서만 모은다."""
    result = score(runs, rubric, suite, suite_path)
    registry = load_target_registry(REGISTRY_PATH)
    rubric_cases = {item["case_id"]: item for item in rubric["cases"]}
    completed = {}
    for row in runs["results"]:
        if row["status"] == "completed":
            completed.setdefault((row["case_id"], row["repeat"]), row)
    cases = {}
    for row in result["manual_review"]:
        if first_only and row["repeat"] != 1:
            continue
        case_id = row["case_id"]
        suite_row = next(item for item in suite["cases"] if item["case_id"] == case_id)
        bundle = load_evidence_bundle(suite_path.parent / suite_row["bundle"], registry)
        if bundle.target.environment != "practice" or any(
                item.provenance.kind != "synthetic" for item in bundle.evidence):
            raise ValueError("독립 검토 자료는 practice 합성 증거에 한정합니다")
        report = AnalysisReport.model_validate(
            completed[(case_id, row["repeat"])]["report"]
        )
        if report.input_hash != bundle.bundle_hash:
            raise ValueError("보고서와 증거 묶음의 해시가 다릅니다")
        case = cases.setdefault(case_id, {
            "case_id": case_id,
            "scenario": rubric_cases[case_id]["scenario"],
            "class": row["class"],
            "top1_criterion": row["top1_criterion"],
            "forbidden_claim": row["forbidden_claim"],
            "required_evidence_ids": row["required_evidence_ids"],
            "evidence": [item.model_dump(mode="json") for item in bundle.evidence],
            "reports": [],
        })
        case["reports"].append({
            "repeat": row["repeat"],
            "diagnosis_status": report.diagnosis_status,
            "current_state": report.current_state,
            "facts": [item.model_dump(mode="json") for item in report.facts],
            "hypotheses": [item.model_dump(mode="json") for item in report.hypotheses],
            "missing_evidence": [item.model_dump(mode="json") for item in report.missing_evidence],
            "recommended_checks": report.recommended_checks,
            "suggested_actions": [item.model_dump(mode="json") for item in report.suggested_actions],
            "limitations": report.limitations,
        })
    packet = {
        "schema_version": 1, "suite_id": suite["suite_id"],
        "suite_hash": suite["suite_hash"], "rubric_hash": rubric.get("rubric_hash"),
        "mode": runs["mode"], "first_only": first_only,
        "notice": "증거는 합성 저장 자료이며 판정란은 작성하지 않았습니다. 원본과 보고서를 사람이 대조해야 합니다.",
        "cases": list(cases.values()),
    }
    template = {
        "schema_version": 1, "suite_hash": suite["suite_hash"], "mode": runs["mode"],
        "reviews": [{
            "case_id": row["case_id"], "repeat": row["repeat"],
            "top1_correct": None, "evidence_semantically_supported": None,
            "forbidden_claim_present": None, "reviewer": "", "note": "",
        } for row in result["manual_review"] if not first_only or row["repeat"] == 1],
    }
    return packet, template


def main() -> None:
    parser = argparse.ArgumentParser(description="A7 독립 검토용 합성 증거와 빈 판정표")
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True, help="저장소 밖 개인 파일")
    parser.add_argument("--template", type=Path, required=True, help="저장소 밖 개인 파일")
    parser.add_argument("--first-only", action="store_true", help="첫 반복만 검토")
    args = parser.parse_args()
    if args.packet == args.template:
        parser.error("검토 자료와 빈 판정표의 저장 경로는 달라야 합니다")
    runs = json.loads(args.runs.read_text(encoding="utf-8"))
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    rubric = json.loads(args.rubric.read_text(encoding="utf-8"))
    packet, template = build_review_materials(
        runs, rubric, suite, args.suite.resolve(), first_only=args.first_only)
    _write_private(args.packet, packet)
    _write_private(args.template, template)
    print(f"독립 검토 자료 {len(packet['cases'])}건과 빈 판정 {len(template['reviews'])}건을 저장했습니다")


if __name__ == "__main__":
    main()
