"""활성 GitOps 실패를 확인해 C-02 일회성 조사 Job만 개인 파일에 준비한다."""

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess

from ai_ops.b6_ci import validate_sources
from ai_ops.c2_active_capture import build_active_capture_job, validate_active_failure
from ai_ops.path_policy import cli_paths, write_private_json


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 활성 소스 실습 조사 Job 준비; 적용하지 않음")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--failure-revision", required=True)
    parser.add_argument("--application-uid", required=True)
    parser.add_argument("--deployment-uid", required=True)
    parser.add_argument("--trial-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    kubeconfig = Path(args.kubeconfig).expanduser().absolute()
    info = kubeconfig.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
            or not 1 <= info.st_size <= 1024 * 1024):
        raise ValueError("관리자 kubeconfig의 소유자·권한이 유효하지 않습니다")
    def command(*argv: str) -> bytes:
        value = subprocess.run(argv, capture_output=True, timeout=15, check=True).stdout
        if len(value) > 1024 * 1024:
            raise ValueError("C-02 실습 조회 응답이 너무 큽니다")
        return value
    def get(namespace: str, kind: str, name: str) -> dict:
        return json.loads(command("kubectl", "--kubeconfig", str(kubeconfig), "-n", namespace,
                                  "get", kind, name, "-o", "json"))
    def source(revision: str) -> dict:
        raw = command("git", "show", revision + ":ai-ops/lab/values.yaml")
        if len(raw) > 65536:
            raise ValueError("실습 values 자료가 너무 큽니다")
        return json.loads(raw)
    changed = command("git", "diff", "--name-only", args.baseline_revision,
                      args.failure_revision).decode().splitlines()
    if changed != ["ai-ops/lab/values.yaml"]:
        raise ValueError("실패 커밋의 변경 범위가 다릅니다")
    _, failure_image = validate_sources(source(args.baseline_revision), source(args.failure_revision))
    application = get("argocd", "application", "lab-target")
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    lease = get("ai-ops-lab", "lease", "b6-scenario-lock")
    pods = json.loads(command("kubectl", "--kubeconfig", str(kubeconfig), "-n", "ai-ops-lab",
                              "get", "pods", "-l", "app=lab-api", "-o", "json"))["items"]
    owners = pods[0].get("metadata", {}).get("ownerReferences", []) if len(pods) == 1 else []
    replica_set = get("ai-ops-lab", "replicaset", owners[0]["name"]) if len(owners) == 1 \
        and owners[0].get("kind") == "ReplicaSet" else {}
    validate_active_failure(application, deployment, replica_set, pods, lease,
                            failure_revision=args.failure_revision, failure_image=failure_image,
                            application_uid=args.application_uid,
                            deployment_uid=args.deployment_uid)
    worker = get("ai-ci", "deployment", "ci-worker")
    job = build_active_capture_job(worker, trial_id=args.trial_id,
                                   failure_revision=args.failure_revision)
    write_private_json(args.output, job)
    print("C-02 활성 소스 조사 Job을 개인 파일에 준비했습니다. 클러스터에는 적용하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
