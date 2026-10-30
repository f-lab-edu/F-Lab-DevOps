"""별도 실행에서 C-02 실습의 고정 정상 revision을 기한에 복구한다."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from ai_ops.c2_rollback_guard import rollback_decision, validate_plan
from ai_ops.path_policy import cli_paths


def call(*args: str, timeout: int = 30) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError("C-02 원복 명령 또는 조회가 실패했습니다")
    return result.stdout


def get_application() -> dict:
    """기존 실습 전용 Argo 자격으로 고정 Application을 조회한다."""
    return json.loads(call("argocd", "--server", "argocd.bidservice.store", "--grpc-web",
                           "app", "get", "lab-target", "-o", "json"))


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 별도 기한 원복 실행")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--kubeconfig", required=True)
    args = cli_paths(parser, parser.parse_args(), {"plan": "input"})
    plan = json.loads(args.plan.read_bytes())
    deadline = validate_plan(plan, now=datetime.now(timezone.utc))
    # 주입 실행과 별도 프로세스에서 기한을 기다린다. 취소되거나 죽으면 성공으로 표시하지 않는다.
    time.sleep(max(0, (deadline - datetime.now(timezone.utc)).total_seconds()))

    def get(namespace: str, kind: str, name: str) -> dict:
        return json.loads(call("kubectl", "--kubeconfig", args.kubeconfig,
                               "-n", namespace, "get", kind, name, "-o", "json"))

    argo = ("argocd", "--server", "argocd.bidservice.store", "--grpc-web", "app")
    app = get_application()
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    decision = rollback_decision(plan, app, deployment)
    if decision == "set_and_sync_baseline":
        call(*argo, "set", "lab-target", "--revision", plan["baseline_revision"], timeout=45)
    if decision != "verify_normal":
        call(*argo, "sync", "lab-target", "--revision", plan["baseline_revision"],
             "--timeout", "150", timeout=165)
    call(*argo, "wait", "lab-target", "--sync", "--health", "--operation",
         "--timeout", "150", timeout=165)
    app = get_application()
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    if (rollback_decision(plan, app, deployment) != "verify_normal"
            or app["status"]["sync"]["status"] != "Synced"
            or app["status"]["health"]["status"] != "Healthy"
            or deployment["status"].get("readyReplicas") != 1):
        raise ValueError("C-02 실습 정상 복구가 검증되지 않았습니다")
    print("C-02 격리 실습 기준선 복구·검증 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
