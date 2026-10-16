"""사람의 A7 원본 분석 기록을 사후 검증하고 기계적 항목만 채점한다."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from ai_ops.path_policy import cli_paths, write_private_json


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/fixtures/holdout-v3/suite.json"
RUBRIC = ROOT / "evals/rubrics/holdout-v3/scenarios.json"
HEADERS = (
    "case_id", "analyst_id", "started_at_kst", "ended_at_kst", "diagnosis_status",
    "current_state", "top1_cause", "evidence_ids", "rationale", "uncertainty",
    "external_sources", "prior_exposure",
)
DIAGNOSES = {"suspected_cause", "no_incident", "insufficient_evidence"}
STATES = {"affected", "recovered", "healthy", "unknown"}
EXPOSURES = {"seen", "unseen", "unsure"}
KST_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00\Z")


def _kst(value: str) -> datetime:
    """명시적 KST ISO 시각만 받는다."""
    if not KST_PATTERN.fullmatch(value):
        raise ValueError("KST 시각 형식은 YYYY-MM-DDTHH:MM:SS+09:00이어야 합니다")
    return datetime.fromisoformat(value)


def evaluate(rows: list[dict[str, str]], suite: dict, rubric: dict) -> dict:
    """기록 무결성·시간·분류 일치를 확인하고 원인 의미는 보류한다."""
    expected_cases = {row["case_id"] for row in suite["cases"]}
    criteria = {row["case_id"]: row for row in rubric["cases"]}
    if (suite.get("suite_id") != "a7-holdout-v3" or len(expected_cases) != 16 or
            expected_cases != set(criteria) or len(rows) != 16 or
            {row.get("case_id") for row in rows} != expected_cases):
        raise ValueError("v3 사건 16건이 중복 없이 모두 필요합니다")
    analysts = {row["analyst_id"].strip() for row in rows}
    exposures = {row["prior_exposure"] for row in rows}
    if len(analysts) != 1 or not next(iter(analysts)) or len(exposures) != 1 or not exposures <= EXPOSURES:
        raise ValueError("분석자 식별자와 기존 결과 노출 여부가 16건에서 같아야 합니다")
    result_rows = []
    for row in sorted(rows, key=lambda item: item["case_id"]):
        case_id = row["case_id"]
        start, end = _kst(row["started_at_kst"]), _kst(row["ended_at_kst"])
        duration = (end - start).total_seconds()
        if duration <= 0 or duration > timedelta(hours=8).total_seconds():
            raise ValueError(f"{case_id}: 조사 시간은 0초 초과·8시간 이하여야 합니다")
        diagnosis, state = row["diagnosis_status"], row["current_state"]
        if diagnosis not in DIAGNOSES or state not in STATES or not row["rationale"].strip():
            raise ValueError(f"{case_id}: 원인 판단·상태·판단 이유를 확인하세요")
        if bool(row["top1_cause"].strip()) != (diagnosis == "suspected_cause"):
            raise ValueError(f"{case_id}: 첫 원인 후보와 원인 판단이 일치하지 않습니다")
        manifest = json.loads((SUITE.parent / case_id / "manifest.json").read_text(encoding="utf-8"))
        allowed_ids = {item["evidence_id"] for item in manifest["entries"]}
        cited_ids = set(filter(None, re.split(r"[\s,;]+", row["evidence_ids"])))
        if cited_ids - allowed_ids:
            raise ValueError(f"{case_id}: 해당 사건에 없는 근거 ID가 있습니다")
        criterion = criteria[case_id]
        result_rows.append({
            "case_id": case_id,
            "duration_seconds": int(duration),
            "diagnosis_status_matches": diagnosis == criterion["expected_diagnosis"],
            "current_state_matches": state == criterion["expected_state"],
            "top1_cause": row["top1_cause"],
            "top1_manual_review_required": criterion["class"] == "incident",
        })
    durations = [row["duration_seconds"] for row in result_rows]
    return {
        "schema_version": 1,
        "suite_hash": suite["suite_hash"],
        "analyst_id": next(iter(analysts)),
        "prior_exposure": next(iter(exposures)),
        "completed_cases": len(result_rows),
        "duration_seconds_total": sum(durations),
        "duration_seconds_median": statistics.median(durations),
        "diagnosis_status_matches": sum(row["diagnosis_status_matches"] for row in result_rows),
        "current_state_matches": sum(row["current_state_matches"] for row in result_rows),
        "top1_semantic_score": None,
        "top1_note": "장애 8건의 첫 원인 후보는 별도 사람이 원본·고정 기준과 대조해야 합니다.",
        "cases": result_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="A7 수동 분석 CSV의 시간·분류를 사후 검증")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Git 제외 개인 경로의 결과 JSON")
    args = cli_paths(parser, parser.parse_args(), {'csv': 'input', 'output': 'output'})
    if args.output.resolve().is_relative_to(ROOT.resolve()) and not args.output.resolve().is_relative_to((ROOT / "site").resolve()):
        parser.error("결과 JSON은 Git 제외 개인 경로 또는 ai-ops/site에 저장하세요")
    with args.csv.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != HEADERS:
            parser.error("수동 분석 CSV 열 이름·순서가 템플릿과 다릅니다")
        rows = list(reader)
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    rubric = json.loads(RUBRIC.read_text(encoding="utf-8"))
    result = evaluate(rows, suite, rubric)
    write_private_json(args.output, result)
    print(f"16건 검증: 진단 분류 {result['diagnosis_status_matches']}/16, 상태 {result['current_state_matches']}/16, 첫 원인 의미는 별도 검토 대기")


if __name__ == "__main__":
    main()
