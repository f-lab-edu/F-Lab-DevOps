"""Secret 원문 없는 비활성 실습 모델 배포 정의를 만든다."""

import argparse
import json
from pathlib import Path

from ai_ops.model_service import build_service
from ai_ops.path_policy import cli_paths, write_private_json


def main():
    parser = argparse.ArgumentParser(description="실습 모델 gateway·proxy 배포 정의 생성")
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    write_private_json(args.output, build_service(args.image))
    print("복제수 0 배포 정의를 저장했습니다. Secret 생성·클러스터 적용·모델 전송은 실행하지 않았습니다.")


if __name__ == "__main__":
    main()
