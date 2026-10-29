"""현재 실습 worker Pod를 읽고 C-02 최소 port-forward RBAC 정의만 생성한다."""

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess

from ai_ops.c_portforward_access import build_portforward_access
from ai_ops.path_policy import cli_paths, write_private_json


def _get(kubeconfig: Path, kind: str, name: str) -> dict:
    result = subprocess.run(["kubectl", "--kubeconfig", str(kubeconfig), "-n", "ai-ci",
                             "get", kind, name, "-o", "json"], capture_output=True,
                            text=True, timeout=12, check=True)
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 실습 사건 조회 Pod 한정 권한 준비")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--pod", required=True, help="현재 ci-worker Pod 이름")
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    kubeconfig = Path(args.kubeconfig).expanduser().absolute()
    info = kubeconfig.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
            or not 1 <= info.st_size <= 1024 * 1024):
        raise ValueError("관리자 kubeconfig의 소유자·권한이 유효하지 않습니다")
    pod = _get(kubeconfig, "pod", args.pod)
    owners = [item for item in pod["metadata"].get("ownerReferences", [])
              if item.get("controller") is True and item.get("kind") == "ReplicaSet"]
    if len(owners) != 1:
        raise ValueError("실습 worker Pod 소유 관계가 유효하지 않습니다")
    replica_set = _get(kubeconfig, "replicaset", owners[0]["name"])
    deployment = _get(kubeconfig, "deployment", "ci-worker")
    manifest = build_portforward_access(pod, replica_set, deployment)
    write_private_json(args.output, manifest)
    print("현재 Pod 한 개의 get·port-forward create 권한 정의를 생성했습니다. 적용·토큰 발급은 하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
