"""현재 Pod 전용 Role을 재검사하고 한 시간짜리 로컬 조회 kubeconfig를 만든다."""

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess

from ai_ops.c_portforward_access import ACCOUNT, NAMESPACE, ROLE, build_portforward_access
from ai_ops.path_policy import cli_paths, write_private_json


def _run(kubeconfig: Path, *args: str) -> str:
    result = subprocess.run(["kubectl", "--kubeconfig", str(kubeconfig), *args],
                            capture_output=True, text=True, timeout=15, check=True)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 현재 Pod 전용 1시간 조회 자격 발급")
    parser.add_argument("--admin-kubeconfig", required=True)
    parser.add_argument("--pod", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    admin = Path(args.admin_kubeconfig).expanduser().absolute()
    info = admin.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
            or not 1 <= info.st_size <= 1024 * 1024):
        raise ValueError("관리자 kubeconfig의 소유자·권한이 유효하지 않습니다")
    pod = json.loads(_run(admin, "-n", NAMESPACE, "get", "pod", args.pod, "-o", "json"))
    owners = [item for item in pod["metadata"].get("ownerReferences", [])
              if item.get("controller") is True and item.get("kind") == "ReplicaSet"]
    if len(owners) != 1:
        raise ValueError("실습 worker Pod 소유 관계가 유효하지 않습니다")
    replica_set = json.loads(_run(admin, "-n", NAMESPACE, "get", "replicaset", owners[0]["name"], "-o", "json"))
    deployment = json.loads(_run(admin, "-n", NAMESPACE, "get", "deployment", "ci-worker", "-o", "json"))
    expected = build_portforward_access(pod, replica_set, deployment)["items"]
    for item in expected:
        live = json.loads(_run(admin, "-n", NAMESPACE, "get", item["kind"].lower(),
                               item["metadata"]["name"], "-o", "json"))
        if (live["metadata"].get("annotations", {}).get("ai-ops/c2-target-pod-uid")
                != pod["metadata"]["uid"]):
            raise ValueError("포트 전달 권한이 현재 Pod UID에 결합되지 않았습니다")
        if item["kind"] == "Role" and live.get("rules") != item["rules"]:
            raise ValueError("포트 전달 Role 권한이 계획과 다릅니다")
        if item["kind"] == "RoleBinding" and (live.get("roleRef") != item["roleRef"]
                or live.get("subjects") != item["subjects"]):
            raise ValueError("포트 전달 RoleBinding 권한이 계획과 다릅니다")
        if item["kind"] == "ServiceAccount" and live.get("automountServiceAccountToken") is not False:
            raise ValueError("조회 ServiceAccount의 자동 토큰 마운트가 꺼져 있지 않습니다")
    raw_config = json.loads(_run(admin, "config", "view", "--raw", "-o", "json"))
    context_name = raw_config["current-context"]
    context = next(item["context"] for item in raw_config["contexts"] if item["name"] == context_name)
    cluster = next(item["cluster"] for item in raw_config["clusters"] if item["name"] == context["cluster"])
    if (not cluster.get("server", "").startswith("https://")
            or not cluster.get("certificate-authority-data")
            or cluster.get("insecure-skip-tls-verify")):
        raise ValueError("Kubernetes API의 TLS 연결 정보가 유효하지 않습니다")
    token = _run(admin, "-n", NAMESPACE, "create", "token", ACCOUNT, "--duration=1h")
    if not token or len(token) > 8192:
        raise ValueError("단기 조회 토큰을 발급하지 못했습니다")
    restricted = {"apiVersion": "v1", "kind": "Config", "clusters": [
        {"name": "c2-cluster", "cluster": {"server": cluster["server"],
                                      "certificate-authority-data": cluster["certificate-authority-data"]}}],
        "users": [{"name": "c2-operator", "user": {"token": token}}],
        "contexts": [{"name": "c2-projection", "context": {"cluster": "c2-cluster",
                                                "user": "c2-operator", "namespace": NAMESPACE}}],
        "current-context": "c2-projection"}
    write_private_json(args.output, restricted)
    print("현재 Pod 한 개의 포트 전달용 1시간 kubeconfig를 개인 파일로 발급했습니다. 토큰 값은 출력하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
