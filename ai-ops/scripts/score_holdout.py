"""A7 실행 뒤 정답표를 읽어 자동 채점과 수동 검토 대상을 분리한다."""

from __future__ import annotations

import argparse
import json
from statistics import median
from pathlib import Path

from ai_ops.contracts import load_target_registry
from ai_ops.evidence import load_evidence_bundle


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/fixtures/holdout/suite.json"
RUBRIC = ROOT / "evals/rubrics/holdout/scenarios.json"


def score(runs: dict, rubric: dict, suite: dict) -> dict:
    """정답이 필요한 채점은 실행 종료 후에만 수행한다."""
    if runs.get("suite_hash") != suite.get("suite_hash") or rubric.get("suite_id") != suite.get("suite_id"):
        raise ValueError("평가 실행과 정답표의 suite가 다릅니다")
    cases = {item["case_id"]: item for item in rubric["cases"]}
    if len(cases) != 16 or set(cases) != {item["case_id"] for item in suite["cases"]}:
        raise ValueError("정답표의 16개 사건이 suite와 다릅니다")
    registry = load_target_registry(ROOT / "config/targets.example.json")
    evidence_ids = {}
    for row in suite["cases"]:
        bundle = load_evidence_bundle(SUITE.parent / row["bundle"], registry)
        if bundle.bundle_hash != row["input_hash"] or runs["input_hashes"].get(row["case_id"]) != bundle.bundle_hash:
            raise ValueError("평가 입력 해시가 바뀌었습니다")
        evidence_ids[row["case_id"]] = {item.evidence_id for item in bundle.evidence if item.status == "available"}
    rows = []
    manual_review = []
    seen = set()
    selected = {}
    for run in runs["results"]:
        key = (run["case_id"], run["repeat"])
        attempt = run.get("attempt", 1)
        if ((key, attempt) in seen or run["case_id"] not in cases or
                not 1 <= run["repeat"] <= runs["repeats"] or type(attempt) is not int or attempt < 1):
            raise ValueError("평가 실행 시도가 중복되거나 범위를 벗어났습니다")
        seen.add((key, attempt))
        previous = selected.get(key)
        if previous is None or previous["status"] != "completed":
            selected[key] = run
    for run in selected.values():
        report = run.get("report")
        expected = cases[run["case_id"]]
        cited = []
        if report:
            cited.extend(eid for fact in report["facts"] for eid in fact["evidence_ids"])
            cited.extend(eid for hypothesis in report["hypotheses"] for eid in
                         hypothesis["supporting_evidence_ids"] + hypothesis["contradicting_evidence_ids"])
        valid_ids = all(eid in evidence_ids[run["case_id"]] for eid in cited)
        completed = run["status"] == "completed" and report is not None and (
            runs["mode"] == "rules" or report["execution"]["mode"] == "ai")
        rows.append({
            "case_id": run["case_id"], "repeat": run["repeat"], "scenario": expected["scenario"],
            "class": expected["class"], "status": run["status"],
            "diagnosis_status": report["diagnosis_status"] if report else None,
            "current_state": report["current_state"] if report else None,
            "diagnosis_match": completed and report["diagnosis_status"] == expected["expected_diagnosis"],
            "state_match": completed and report["current_state"] == expected["expected_state"],
            "cited_ids_exist": completed and valid_ids,
            "required_ids_cited": completed and set(expected["required_evidence_ids"]).issubset(cited),
            "top1_manual_review": "pending" if completed and expected["class"] == "incident" else "not_applicable",
            "evidence_semantics_review": "pending" if completed else "not_applicable",
            "wall_ms": run.get("wall_ms"),
            "cost_usd": report["execution"].get("cost_usd") if report else None,
        })
        if completed:
            manual_review.append({
                "case_id": run["case_id"], "repeat": run["repeat"], "class": expected["class"],
                "top1_criterion": expected["top1_criterion"],
                "forbidden_claim": expected["forbidden_claim"],
                "required_evidence_ids": expected["required_evidence_ids"],
                "facts": report["facts"], "hypotheses": report["hypotheses"],
                "top1_correct": None, "evidence_semantically_supported": None,
                "forbidden_claim_present": None, "reviewer": None,
            })
    first = {row["case_id"]: row for row in rows if row["repeat"] == 1}
    normal = [row for row in first.values() if row["class"] == "normal"]
    insufficient = [row for row in first.values() if row["class"] == "insufficient"]
    recovered = [row for row in first.values() if row["scenario"] == "S10"]
    completed_rows = [row for row in rows if row["status"] == "completed"]
    repeated = [
        case_id for case_id in cases
        if len([row for row in rows if row["case_id"] == case_id and row["status"] == "completed"]) == 3
    ] if runs["repeats"] == 3 else []
    agreed = sum(len({row["diagnosis_status"] for row in rows if row["case_id"] == case_id}) == 1
                 for case_id in repeated)
    return {
        "suite_id": suite["suite_id"], "suite_hash": suite["suite_hash"], "mode": runs["mode"],
        "expected_runs": 16 * runs["repeats"], "recorded_runs": len(rows),
        "recorded_attempts": len(runs["results"]),
        "first_run_cases": len(first),
        "normal_false_positives": sum(row["diagnosis_status"] == "suspected_cause" for row in normal),
        "normal_completed": sum(row["status"] == "completed" for row in normal),
        "insufficient_deferred": sum(row["diagnosis_match"] for row in insufficient),
        "recovered_distinguished": sum(row["state_match"] for row in recovered),
        "citation_id_passes": sum(row["cited_ids_exist"] for row in rows),
        "required_evidence_passes": sum(row["required_ids_cited"] for row in completed_rows),
        "completed_runs": len(completed_rows),
        "median_wall_ms": median(row["wall_ms"] for row in completed_rows) if completed_rows else None,
        "estimated_cost_usd": round(sum(
            (run.get("model_usage") or {}).get("cost_usd") or
            ((run.get("report") or {}).get("execution") or {}).get("cost_usd") or 0
            for run in runs["results"]
        ), 6),
        "unknown_cost_attempts": sum(
            (run.get("model_usage") or {}).get("request_attempts", 0) > 0 and
            (run.get("model_usage") or {}).get("cost_usd") is None
            for run in runs["results"]
        ),
        "three_run_diagnosis_agreement": {"agree": agreed, "eligible": len(repeated)},
        "top1_score": "수동 검토 대기",
        "semantic_evidence_score": "수동 검토 대기",
        "rows": rows,
        "manual_review": manual_review,
    }


def apply_manual_reviews(result: dict, reviews: dict) -> dict:
    """사람이 확인한 점수만 반영하며 검토가 덜 끝난 비율은 확정하지 않는다."""
    if (reviews.get("schema_version") != 1 or reviews.get("suite_hash") != result["suite_hash"] or
            reviews.get("mode") != result["mode"] or not isinstance(reviews.get("reviews"), list)):
        raise ValueError("수동 검토표의 버전·suite·비교군이 다릅니다")
    pending = {(item["case_id"], item["repeat"]): item for item in result["manual_review"]}
    checked = set()
    for item in reviews["reviews"]:
        if not isinstance(item, dict):
            raise ValueError("수동 검토 항목은 객체여야 합니다")
        key = (item.get("case_id"), item.get("repeat"))
        if key not in pending or key in checked:
            raise ValueError("수동 검토 사건이 중복되거나 실행 결과에 없습니다")
        checked.add(key)
        row = pending[key]
        top1 = item.get("top1_correct")
        if row["class"] == "incident":
            if type(top1) is not bool:
                raise ValueError("장애 사례의 Top-1은 참·거짓으로 기록해야 합니다")
        elif top1 is not None:
            raise ValueError("정상·정보 부족 사례에 Top-1 점수를 매길 수 없습니다")
        if (type(item.get("evidence_semantically_supported")) is not bool or
                type(item.get("forbidden_claim_present")) is not bool or
                not isinstance(item.get("reviewer"), str) or not item["reviewer"].strip()):
            raise ValueError("의미 검토·금지 주장·검토자 기록이 필요합니다")
        if not isinstance(item.get("note"), str) or not item["note"].strip():
            raise ValueError("원본과 대조한 검토 근거를 note에 기록해야 합니다")
        row.update({field: item[field] for field in (
            "top1_correct", "evidence_semantically_supported", "forbidden_claim_present", "reviewer", "note"
        )})
    for row in result["rows"]:
        review = pending.get((row["case_id"], row["repeat"]))
        if review and (row["case_id"], row["repeat"]) in checked:
            row["top1_manual_review"] = review["top1_correct"] if row["class"] == "incident" else "not_applicable"
            row["evidence_semantics_review"] = review["evidence_semantically_supported"]
    incidents = [row for row in result["rows"] if row["repeat"] == 1 and row["class"] == "incident"]
    if len(incidents) == 8 and all(type(row["top1_manual_review"]) is bool for row in incidents):
        result["top1_score"] = {"correct": sum(row["top1_manual_review"] for row in incidents), "total": 8}
    if len(checked) == len(pending) == result["expected_runs"]:
        result["semantic_evidence_score"] = {
            "supported": sum(item["evidence_semantically_supported"] for item in pending.values()),
            "total": len(pending),
        }
        result["forbidden_claims"] = sum(item["forbidden_claim_present"] for item in pending.values())
    result["manual_review_progress"] = {"reviewed": len(checked), "eligible": len(pending)}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="A7 정답표 기반 자동 채점과 수동 검토 목록")
    parser.add_argument("--runs", type=Path, required=True, help="run_holdout.py가 만든 저장소 밖 결과")
    parser.add_argument("--review", type=Path, help="선택: 사람이 원본과 대조해 채운 검토표 JSON")
    parser.add_argument("--output", type=Path, help="선택: 채점표 JSON 저장 경로")
    args = parser.parse_args()
    runs = json.loads(args.runs.read_text(encoding="utf-8"))
    rubric = json.loads(RUBRIC.read_text(encoding="utf-8"))
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    result = score(runs, rubric, suite)
    if args.review:
        reviews = json.loads(args.review.read_text(encoding="utf-8"))
        result = apply_manual_reviews(result, reviews)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
