"""실제 경보 정의를 개인 파일에 고정하고 검증된 C-03 인계의 수집 계획을 준비한다."""

import argparse
import json
from pathlib import Path

from ai_ops.c_verification_handoff import C03Handoff
from ai_ops.c_verification_prepare import prepare_collection
from ai_ops.c_verification_transport import KubectlReader
from ai_ops.path_policy import cli_paths, write_private_json
from verify_c4_recovery import read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description="C-04 읽기 준비; 장애 주입·현재 시각으로 과거 영수증 보충 없음")
    parser.add_argument("--handoff", type=Path, help="실제 C-03 실행기가 새로 출력한 인계 기록")
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(argv), {"handoff": "input", "output": "output"})
    try:
        handoff = C03Handoff.model_validate(read_json(args.handoff)) if args.handoff else None
        result = prepare_collection(KubectlReader(), handoff=handoff)
        write_private_json(args.output, result)
        print(json.dumps({"status": result["status"], "reasons": result["reasons"],
                          "collection_plan_created": result["collection_plan"] is not None}, ensure_ascii=False))
        return 0 if result["status"] == "ready" else 1
    except (ValueError, TypeError, KeyError, OSError):
        parser.exit(2, "C-04 인계·경로 검증 실패. 기존 영수증을 현재 시각으로 갱신하지 않았습니다.\n")


if __name__ == "__main__":
    raise SystemExit(main())
