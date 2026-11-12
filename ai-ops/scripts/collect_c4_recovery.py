"""고정된 실습 대상의 읽기 관찰을 C-04 판정 보고서로 연결한다."""

import argparse
import json
from pathlib import Path

from ai_ops.c_verification import verify_recovery
from ai_ops.c_verification_collect import CollectionPlan, collect
from ai_ops.c_verification_transport import KubectlReader
from ai_ops.path_policy import cli_paths, write_private_json
from verify_c4_recovery import read_json, render_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="C-04 실습 읽기 수집; 기본은 대상 대조만 수행")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect", action="store_true", help="대상 대조 통과 후 최소 120초 동안 GET 관찰")
    args = cli_paths(parser, parser.parse_args(argv), {"plan": "input", "output": "output"})
    try:
        value = read_json(args.plan)
        if value.get("record_type") == "c04_collection_preparation":
            if value.get("status") != "ready" or value.get("collection_plan") is None:
                raise ValueError("검증된 C-03 인계에 연결된 준비 계획이 아닙니다")
            value = value["collection_plan"]
        plan = CollectionPlan.model_validate(value)
        request, audit = collect(plan, KubectlReader(), enabled=args.collect)
        report = verify_recovery(request)
        write_private_json(args.output, {"collection": audit, "input": request.model_dump(mode="json"),
            "verification": report, "report_markdown": render_report(report)})
        print(json.dumps({"status": report["status"], "collection": audit, "execution_allowed": False}, ensure_ascii=False))
        return 0 if report["status"] == "resolved" else 1
    except (ValueError, TypeError, KeyError, OSError):
        parser.exit(2, "C-04 수집 계획·경로 검증 실패. 자격과 원문 응답은 출력하지 않았습니다.\n")


if __name__ == "__main__":
    raise SystemExit(main())
