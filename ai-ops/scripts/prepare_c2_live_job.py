"""현재 실습 worker Deployment를 확인해 C-02 일회성 조사 접수 Job만 준비한다."""

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess

from ai_ops.c2_live_job import build_live_capture_job, validate_live_preflight
from ai_ops.path_policy import cli_paths, write_private_json


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 격리 실습 실시간 조사 접수 Job 준비")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--failure-revision", required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"preflight": "input", "output": "output"})
    preflight_raw = args.preflight.read_bytes()
    if len(preflight_raw) > 8192:
        raise ValueError("사전 판정 자료가 너무 큽니다")
    validate_live_preflight(json.loads(preflight_raw), failure_revision=args.failure_revision)
    kubeconfig = Path(args.kubeconfig).expanduser().absolute()
    info = kubeconfig.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
            or not 1 <= info.st_size <= 1024 * 1024):
        raise ValueError("관리자 kubeconfig의 소유자·권한이 유효하지 않습니다")
    raw = subprocess.run(["kubectl", "--kubeconfig", str(kubeconfig), "-n", "ai-ci",
                          "get", "deployment", "ci-worker", "-o", "json"],
                         capture_output=True, timeout=12, check=True).stdout
    if len(raw) > 1024 * 1024:
        raise ValueError("worker Deployment 자료가 너무 큽니다")
    job = build_live_capture_job(json.loads(raw), run_id=args.run_id, failure_revision=args.failure_revision)
    write_private_json(args.output, job)
    print("C-02 일회성 조사 접수 Job을 개인 파일에 준비했습니다. 클러스터에는 적용하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
