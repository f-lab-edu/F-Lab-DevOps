"""현재 Argo·Deployment·Lease와 고정 revision values를 조회해 제안 차단 사유를 남긴다."""

import argparse
import json
from pathlib import Path
import subprocess

from ai_ops.c_live_preflight import SHA, assess_live_preflight
from ai_ops.path_policy import cli_paths, write_private_json


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=True).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 현재 변경안 선행 조건 읽기 전용 감사")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    kubeconfig = str(Path(args.kubeconfig).expanduser().absolute())
    def get(namespace: str, kind: str, name: str) -> dict:
        return json.loads(_run("kubectl", "--kubeconfig", kubeconfig, "-n", namespace,
                               "get", kind, name, "-o", "json"))
    app = get("argocd", "application", "lab-target")
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    lease = get("ai-ops-lab", "lease", "b6-scenario-lock")
    revision = app["spec"]["source"]["targetRevision"]
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise ValueError("실습 Argo revision이 전체 SHA가 아닙니다")
    values = _run("git", "show", revision + ":ai-ops/lab/values.yaml").encode()
    result = assess_live_preflight(app, deployment, lease, values)
    write_private_json(args.output, result)
    print("C-02 실시간 선행 조건을 읽기 전용으로 기록했습니다. 제안·승인·배포는 생성하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
