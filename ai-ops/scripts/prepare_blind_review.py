"""A7 두 방식의 첫 반복을 익명 A/B 검토 화면으로 만들고 판정을 되돌린다."""

from __future__ import annotations

import argparse
import json
import random
import uuid
from pathlib import Path

from run_holdout import _write_private


TEMPLATE = Path(__file__).with_name("review_template.html")
LOCAL_SITE = Path(__file__).resolve().parents[1] / "site"
CASE_COUNT = 16


def _cases(packet: dict, mode: str) -> dict[str, dict]:
    """기존 검토 패킷이 v3 첫 반복 16건인지 확인한다."""
    if packet.get("schema_version") != 1 or packet.get("mode") != mode or packet.get("first_only") is not True:
        raise ValueError(f"{mode} 첫 반복 검토 패킷의 형식이 다릅니다")
    rows = packet.get("cases")
    if not isinstance(rows, list) or len(rows) != CASE_COUNT:
        raise ValueError(f"{mode} 검토 패킷은 16건이어야 합니다")
    cases = {row["case_id"]: row for row in rows}
    if len(cases) != CASE_COUNT or any(
        len(row.get("reports", [])) != 1 or row["reports"][0].get("repeat") != 1
        for row in rows
    ):
        raise ValueError(f"{mode} 사건 ID 또는 첫 반복 보고서가 잘못됐습니다")
    return cases


def build_package(fixed: dict, ai: dict, *, rng: random.Random,
                  package_id: str) -> tuple[dict, dict]:
    """방식 이름은 대응표에만 남기고 검토 화면에는 무작위 A/B를 넣는다."""
    fixed_cases = _cases(fixed, "fixed")
    ai_cases = _cases(ai, "ai")
    if (fixed.get("suite_id") != "a7-holdout-v3" or
            fixed.get("suite_hash") != ai.get("suite_hash") or
            fixed.get("rubric_hash") != ai.get("rubric_hash") or
            set(fixed_cases) != set(ai_cases)):
        raise ValueError("두 방식의 평가 suite·정답표·사건 ID가 다릅니다")
    orientations = [True] * (CASE_COUNT // 2) + [False] * (CASE_COUNT // 2)
    rng.shuffle(orientations)
    items = []
    assignments = {}
    classes = {}
    for case_id, fixed_first in zip(sorted(fixed_cases), orientations, strict=True):
        left = fixed_cases[case_id]
        right = ai_cases[case_id]
        for key in ("class", "scenario", "top1_criterion", "forbidden_claim",
                    "required_evidence_ids", "evidence"):
            if left[key] != right[key]:
                raise ValueError(f"{case_id}: 두 방식의 {key}가 다릅니다")
        assigned = {"A": "fixed", "B": "ai"} if fixed_first else {"A": "ai", "B": "fixed"}
        assignments[case_id] = assigned
        classes[case_id] = left["class"]
        reports = {"fixed": left["reports"][0], "ai": right["reports"][0]}
        items.append({
            "case_id": case_id,
            "class": left["class"],
            "top1_criterion": left["top1_criterion"],
            "forbidden_claim": left["forbidden_claim"],
            "required_evidence_ids": left["required_evidence_ids"],
            "evidence": left["evidence"],
            "reports": [{"alias": alias, **reports[assigned[alias]]} for alias in ("A", "B")],
        })
    shared = {"schema_version": 1, "package_id": package_id, "suite_hash": fixed["suite_hash"]}
    reviewer_data = {
        **shared,
        "notice": "합성 증거의 첫 반복 보고서입니다. A/B 방식 이름과 기존 예비 판정은 숨겼습니다.",
        "cases": items,
    }
    private_map = {**shared, "assignments": assignments, "classes": classes}
    return reviewer_data, private_map


def _json_for_html(value: dict) -> str:
    """HTML 스크립트 종료 문자가 자료 안에 있어도 문서 구조를 지킨다."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))


def render_html(reviewer_data: dict, template: str) -> str:
    """외부 요청 없이 열 수 있는 단일 HTML을 만든다."""
    marker = "__A7_REVIEW_DATA__"
    if template.count(marker) != 1:
        raise ValueError("검토 화면의 자료 삽입 위치가 잘못됐습니다")
    return template.replace(marker, _json_for_html(reviewer_data))


def import_reviews(submitted: dict, private_map: dict,
                   fixed_template: dict, ai_template: dict) -> tuple[dict, dict]:
    """완료된 A/B 판정을 원래 두 방식의 채점기 입력으로 변환한다."""
    if (submitted.get("schema_version") != 1 or
            submitted.get("package_id") != private_map.get("package_id") or
            submitted.get("suite_hash") != private_map.get("suite_hash") or
            submitted.get("independent_attestation") is not True):
        raise ValueError("검토 파일의 패킷 ID·suite 또는 독립 검토 확인이 잘못됐습니다")
    reviewer = submitted.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("검토자 이름이 필요합니다")
    templates = {"fixed": fixed_template, "ai": ai_template}
    allowed = {}
    for mode, template in templates.items():
        if (template.get("schema_version") != 1 or template.get("mode") != mode or
                template.get("suite_hash") != private_map["suite_hash"]):
            raise ValueError(f"{mode} 빈 검토표가 패킷과 다릅니다")
        rows = template.get("reviews")
        if not isinstance(rows, list) or len(rows) != CASE_COUNT or any(row.get("repeat") != 1 for row in rows):
            raise ValueError(f"{mode} 첫 반복 빈 검토표가 16건이 아닙니다")
        allowed[mode] = {row["case_id"] for row in rows}
        if len(allowed[mode]) != CASE_COUNT or allowed[mode] != set(private_map["assignments"]):
            raise ValueError(f"{mode} 검토표 사건이 대응표와 다릅니다")
    reviews = submitted.get("reviews")
    if not isinstance(reviews, list) or len(reviews) != CASE_COUNT * 2:
        raise ValueError("두 방식 첫 반복의 판정 32건이 필요합니다")
    converted = {"fixed": [], "ai": []}
    seen = set()
    for row in reviews:
        if not isinstance(row, dict):
            raise ValueError("검토 항목은 객체여야 합니다")
        case_id, alias = row.get("case_id"), row.get("alias")
        if case_id not in private_map["assignments"] or alias not in ("A", "B") or (case_id, alias) in seen:
            raise ValueError("사건·A/B 판정이 중복되거나 대응표에 없습니다")
        seen.add((case_id, alias))
        mode = private_map["assignments"][case_id][alias]
        top1 = row.get("top1_correct")
        if private_map["classes"][case_id] == "incident":
            if type(top1) is not bool:
                raise ValueError("장애 사건의 Top-1 판정이 필요합니다")
        elif top1 is not None:
            raise ValueError("정상·정보 부족 사건의 Top-1은 null이어야 합니다")
        if (type(row.get("evidence_semantically_supported")) is not bool or
                type(row.get("forbidden_claim_present")) is not bool or
                not isinstance(row.get("note"), str) or not row["note"].strip()):
            raise ValueError("근거 의미·금지 주장·판정 이유가 필요합니다")
        converted[mode].append({
            "case_id": case_id, "repeat": 1,
            "top1_correct": top1,
            "evidence_semantically_supported": row["evidence_semantically_supported"],
            "forbidden_claim_present": row["forbidden_claim_present"],
            "reviewer": reviewer.strip(), "note": row["note"].strip(),
        })
    if any(len(rows) != CASE_COUNT for rows in converted.values()):
        raise ValueError("방식별 판정 16건이 필요합니다")
    return tuple({
        "schema_version": 1,
        "suite_hash": private_map["suite_hash"],
        "mode": mode,
        "reviews": sorted(converted[mode], key=lambda row: row["case_id"]),
    } for mode in ("fixed", "ai"))


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="A7 독립 검토용 익명 A/B 화면 준비·판정 변환")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="검토 HTML과 개인 대응표 생성")
    prepare.add_argument("--fixed-packet", type=Path, required=True)
    prepare.add_argument("--ai-packet", type=Path, required=True)
    prepare.add_argument("--html", type=Path, required=True)
    prepare.add_argument("--mapping", type=Path, required=True, help="저장소 밖 개인 대응표")
    ingest = commands.add_parser("import", help="완료된 판정표를 두 방식의 채점 형식으로 변환")
    ingest.add_argument("--review", type=Path, required=True)
    ingest.add_argument("--mapping", type=Path, required=True)
    ingest.add_argument("--fixed-template", type=Path, required=True)
    ingest.add_argument("--ai-template", type=Path, required=True)
    ingest.add_argument("--fixed-output", type=Path, required=True)
    ingest.add_argument("--ai-output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        if args.html == args.mapping:
            parser.error("HTML과 대응표는 다른 경로에 저장해야 합니다")
        if args.html.resolve().parent != LOCAL_SITE.resolve() or args.html.suffix != ".html":
            parser.error("검토 HTML은 Git에서 제외된 ai-ops/site/에 저장해야 합니다")
        if args.html.exists() or args.mapping.exists():
            parser.error("기존 검토 화면이나 대응표를 덮어쓸 수 없습니다. 새 파일 이름을 지정하세요")
        data, private_map = build_package(
            _read(args.fixed_packet), _read(args.ai_packet),
            rng=random.SystemRandom(), package_id=uuid.uuid4().hex,
        )
        html = render_html(data, TEMPLATE.read_text(encoding="utf-8"))
        _write_private(args.mapping, private_map)
        args.html.write_text(html, encoding="utf-8")
        print(f"검토 화면 {len(data['cases'])}건과 개인 대응표를 저장했습니다: {args.html}")
    else:
        fixed, ai = import_reviews(
            _read(args.review), _read(args.mapping),
            _read(args.fixed_template), _read(args.ai_template),
        )
        if args.fixed_output == args.ai_output:
            parser.error("두 방식 출력 경로는 달라야 합니다")
        _write_private(args.fixed_output, fixed)
        _write_private(args.ai_output, ai)
        print("독립 판정 32건을 방식별 첫 반복 검토표로 변환했습니다")


if __name__ == "__main__":
    main()
