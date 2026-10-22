"""운영자 gh 인증으로 CI 출처를 읽고 원문 없는 사전 점검 결과를 저장한다."""

import argparse
from pathlib import Path

from ai_ops.ci_history import inspect_ci_history
from ai_ops.path_policy import cli_paths, write_private_json


def main():
    parser = argparse.ArgumentParser(description="배포·롤백 기록 출처 점검; 증거 자동 가져오기는 비활성")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--expected-head", required=True, help="검토한 main 소스의 전체 SHA")
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    try:
        result = inspect_ci_history(args.run_id, args.expected_head)
        write_private_json(args.output, result)
    except Exception:
        # API 응답·개인 인증·무효 입력 원문을 콘솔에 남기지 않는다.
        parser.exit(1, "CI 기록 점검 실패: 출처·시간·형식·권한·자료 보존 상태를 확인하세요.\n")
    print("CI 출처 점검 완료. 클러스터 연결과 조사 증거 가져오기는 아직 비활성입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
