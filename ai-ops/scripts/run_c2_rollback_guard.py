"""별도 실행에서 C-02 실습의 고정 정상 revision을 기한에 복구한다."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from ai_ops.c2_rollback_guard import rollback_decision, validate_plan
from ai_ops.path_policy import cli_paths


def call(*args: str, timeout: int = 30) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError("C-02 원복 명령 또는 조회가 실패했습니다")
    return result.stdout


SERVER_ARGO = ("argocd", "--server", "argocd.bidservice.store", "--grpc-web", "app")
CORE_ARGO = ("argocd", "--core", "app")


def get_application(argo: tuple[str, ...] = SERVER_ARGO) -> dict:
    """선택한 Argo 경로로 고정 실습 Application을 조회한다."""
    return json.loads(call(*argo, "get", "lab-target", "-o", "json"))


def verify_normal(plan: dict, app: dict, deployment: dict) -> None:
    """주입 전·원복 후 고정 정상 상태가 실제로 확인됐는지 검사한다."""
    if (rollback_decision(plan, app, deployment) != "verify_normal"
            or app["status"]["sync"]["status"] != "Synced"
            or app["status"]["health"]["status"] != "Healthy"
            or deployment["status"].get("readyReplicas") != 1):
        raise ValueError("C-02 실습 정상 상태가 검증되지 않았습니다")


def verify_local_kubeconfig(path: str) -> None:
    """로컬 직접 조회는 소유자 전용 Argo namespace kubeconfig만 허용한다."""
    info = Path(path).expanduser().absolute().lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
            or not 1 <= info.st_size <= 1024 * 1024):
        raise ValueError("로컬 원복 kubeconfig의 소유자·권한이 유효하지 않습니다")
    namespace = call("kubectl", "--kubeconfig", path, "config", "view", "--minify",
                     "-o", "jsonpath={..namespace}").decode()
    if namespace != "argocd":
        raise ValueError("로컬 원복 kubeconfig의 현재 namespace가 argocd가 아닙니다")


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 별도 기한 원복 실행")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--core", action="store_true")
    args = cli_paths(parser, parser.parse_args(), {"plan": "input"})
    plan = json.loads(args.plan.read_bytes())
    deadline = validate_plan(plan, now=datetime.now(timezone.utc))
    argo = CORE_ARGO if args.core else SERVER_ARGO
    if args.core:
        verify_local_kubeconfig(args.kubeconfig)
        os.environ["KUBECONFIG"] = args.kubeconfig
        os.environ.pop("ARGOCD_AUTH_TOKEN", None)

    def get(namespace: str, kind: str, name: str) -> dict:
        return json.loads(call("kubectl", "--kubeconfig", args.kubeconfig,
                               "-n", namespace, "get", kind, name, "-o", "json"))

    app = get_application(argo)
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    verify_normal(plan, app, deployment)
    print("C-02 별도 원복 감시 준비 완료", flush=True)
    # 주입 실행과 별도 프로세스에서 기한을 기다린다. 취소되거나 죽으면 성공으로 표시하지 않는다.
    time.sleep(max(0, (deadline - datetime.now(timezone.utc)).total_seconds()))

    app = get_application(argo)
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    decision = rollback_decision(plan, app, deployment)
    if decision == "set_and_sync_baseline":
        call(*argo, "set", "lab-target", "--revision", plan["baseline_revision"], timeout=45)
    if decision != "verify_normal":
        call(*argo, "sync", "lab-target", "--revision", plan["baseline_revision"],
             "--timeout", "150", timeout=165)
    call(*argo, "wait", "lab-target", "--sync", "--health", "--operation",
         "--timeout", "150", timeout=165)
    app = get_application(argo)
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    verify_normal(plan, app, deployment)
    print("C-02 격리 실습 기준선 복구·검증 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
