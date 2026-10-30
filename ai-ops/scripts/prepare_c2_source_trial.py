"""현재 격리 실습 앱을 읽어 C-02 소스 전환 검토안만 저장한다."""

import argparse
import json
from pathlib import Path
import subprocess

from ai_ops.c2_source_trial import prepare_source_trial
from ai_ops.path_policy import cli_paths, write_private_json


def command(*args: str, allow_missing: bool = False) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=20)
    if allow_missing and result.returncode and b"ImageNotFoundException" in result.stderr:
        return b""
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ValueError("C-02 실습 출처를 확인하지 못했습니다")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 활성 소스 실습 읽기 전용 준비")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--failure-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    def get(namespace: str, kind: str, name: str):
        return json.loads(command("kubectl", "--kubeconfig", args.kubeconfig, "-n", namespace,
                                  "get", kind, name, "-o", "json"))
    def source(revision: str):
        raw = command("git", "show", revision + ":ai-ops/lab/values.yaml")
        if len(raw) > 65536:
            raise ValueError("실습 소스가 너무 큽니다")
        return raw
    baseline = source(args.baseline_revision)
    failure = source(args.failure_revision)
    changed = command("git", "diff", "--name-only", args.baseline_revision,
                      args.failure_revision).decode().splitlines()
    account = json.loads(command("aws", "sts", "get-caller-identity"))
    if changed != ["ai-ops/lab/values.yaml"] or account.get("Account") != "716174522908":
        raise ValueError("실습 소스 변경 범위 또는 AWS 계정이 다릅니다")
    from ai_ops.b6_ci import validate_sources
    good, bad = validate_sources(json.loads(baseline), json.loads(failure))
    def exists(digest: str) -> bool:
        result = command("aws", "ecr", "describe-images", "--region", "ap-northeast-2",
                         "--repository-name", "urlshortener", "--image-ids",
                         "imageDigest=" + digest, allow_missing=True)
        return bool(result)
    plan = prepare_source_trial(get("argocd", "application", "lab-target"),
                                get("ai-ops-lab", "deployment", "lab-api"),
                                get("ai-ops-lab", "lease", "b6-scenario-lock"),
                                baseline, failure, baseline_revision=args.baseline_revision,
                                failure_revision=args.failure_revision,
                                good_present=exists(good.split("@", 1)[1]),
                                bad_absent=not exists(bad.split("@", 1)[1]))
    write_private_json(args.output, plan)
    print("C-02 소스 전환 검토안만 저장했습니다. 장애 주입·Argo 변경은 실행하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
