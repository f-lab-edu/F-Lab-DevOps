"""현재 실습 worker Deployment를 확인해 C-02 일회성 조사 접수 Job만 준비한다."""

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess

from ai_ops.b6_ci import validate_sources
from ai_ops.c2_live_job import SHA, build_live_capture_job, validate_s10_live_failure
from ai_ops.path_policy import cli_paths, write_private_json


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 격리 실습 실시간 조사 접수 Job 준비")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--failure-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    if (not SHA.fullmatch(args.baseline_revision) or not SHA.fullmatch(args.failure_revision)
            or args.baseline_revision == args.failure_revision):
        raise ValueError("실습 기준선·실패 revision이 유효하지 않습니다")
    kubeconfig = Path(args.kubeconfig).expanduser().absolute()
    info = kubeconfig.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
            or not 1 <= info.st_size <= 1024 * 1024):
        raise ValueError("관리자 kubeconfig의 소유자·권한이 유효하지 않습니다")
    def get(namespace: str, kind: str, name: str) -> dict:
        raw = subprocess.run(["kubectl", "--kubeconfig", str(kubeconfig), "-n", namespace,
                              "get", kind, name, "-o", "json"], capture_output=True,
                             timeout=12, check=True).stdout
        if len(raw) > 1024 * 1024:
            raise ValueError("클러스터 자료가 너무 큽니다")
        return json.loads(raw)
    def source(revision: str) -> dict:
        raw = subprocess.run(["git", "show", revision + ":ai-ops/lab/values.yaml"],
                             capture_output=True, timeout=12, check=True).stdout
        if len(raw) > 65536:
            raise ValueError("실습 values 자료가 너무 큽니다")
        return json.loads(raw)
    _, failure_image = validate_sources(source(args.baseline_revision), source(args.failure_revision))
    application = get("argocd", "application", "lab-target")
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    lease = get("ai-ops-lab", "lease", "b6-scenario-lock")
    pods_raw = subprocess.run(["kubectl", "--kubeconfig", str(kubeconfig), "-n", "ai-ops-lab",
                               "get", "pods", "-l", "app=lab-api", "-o", "json"],
                              capture_output=True, timeout=12, check=True).stdout
    if len(pods_raw) > 1024 * 1024:
        raise ValueError("실습 Pod 자료가 너무 큽니다")
    pods = json.loads(pods_raw)["items"]
    owners = pods[0].get("metadata", {}).get("ownerReferences", []) if len(pods) == 1 else []
    replica_set = get("ai-ops-lab", "replicaset", owners[0]["name"]) if len(owners) == 1 \
        and owners[0].get("kind") == "ReplicaSet" else {}
    validate_s10_live_failure(application, deployment, replica_set, pods, lease,
                              baseline_revision=args.baseline_revision,
                              failure_revision=args.failure_revision, failure_image=failure_image,
                              run_id=args.run_id, run_attempt=args.run_attempt)
    worker = get("ai-ci", "deployment", "ci-worker")
    job = build_live_capture_job(worker, run_id=args.run_id, run_attempt=args.run_attempt,
                                 failure_revision=args.failure_revision)
    write_private_json(args.output, job)
    print("C-02 일회성 조사 접수 Job을 개인 파일에 준비했습니다. 클러스터에는 적용하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
