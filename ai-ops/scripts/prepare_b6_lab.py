"""고정된 B-06 실습 정의를 비공개 JSON으로 준비한다. 클러스터 변경은 하지 않는다."""

import argparse
import json
from pathlib import Path

from ai_ops.b6_lab import LabInputs, build_lab, validate_lab
from ai_ops.path_policy import cli_paths, write_private_json


def main():
    parser = argparse.ArgumentParser(description="분리된 B-06 실습 리소스 생성·경계 검사")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"input": "input", "output": "output"})
    inputs = LabInputs.model_validate_json(args.input.read_bytes())
    bundle = build_lab(inputs)
    summary = validate_lab(bundle)
    write_private_json(args.output, bundle)
    print(json.dumps(summary, ensure_ascii=False))
    print("실습 전용 Secret이 포함된 비공개 입력을 생성했습니다. 설치는 실행하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
