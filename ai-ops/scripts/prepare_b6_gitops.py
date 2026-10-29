"""실습 GitOps 계획을 생성하고 선택적으로 서버 dry-run·공유 잠금만 점검한다."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from ai_ops.b6_coordination import LabLease, require_manual_deployment
from ai_ops.b6_gitops import APPLICATION, ARGO_NAMESPACE, CONTROLLER, GITOPS_PATH, PROJECT, build_gitops, validate_gitops
from ai_ops.b6_lab import NAMESPACE, PURPOSE
from ai_ops.b6_scenarios import LabClient
from ai_ops.contracts import sha256_json
from ai_ops.path_policy import cli_paths, write_private_json


ROOT = Path(__file__).resolve().parents[2]


def source_matches(plan):
    """검토한 커밋의 Helm 파일만 렌더해 계획된 단일 Deployment와 비교한다."""
    names = ("Chart.yaml", "values.yaml", "values.schema.json", "templates/deployment.yaml")
    try:
        with tempfile.TemporaryDirectory() as root:
            chart = Path(root)
            for name in names:
                result = subprocess.run(["git", "show", f"{plan['revision']}:{GITOPS_PATH}/{name}"],
                                        cwd=ROOT, capture_output=True, timeout=15)
                if result.returncode or not 0 < len(result.stdout) <= 65536:
                    return False
                target = chart / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(result.stdout)
            rendered = subprocess.run(["helm", "template", "lab-target", str(chart), "--namespace", NAMESPACE],
                                      capture_output=True, timeout=15)
            if rendered.returncode or not 0 < len(rendered.stdout) <= 65536:
                return False
            text = rendered.stdout.decode()
            document = "\n".join(line for line in text.splitlines() if line.strip() != "---" and not line.startswith("#"))
            return json.loads(document) == plan["deployment"]
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        return False


def identities(client):
    """자료 본문 없이 운영 식별자와 실습 Pod 정의 해시만 비교한다."""
    return {"production": client.production(), "lab": {name: {
        "uid": obj["metadata"]["uid"], "generation": obj["metadata"]["generation"],
        "template_sha256": sha256_json(obj["spec"]["template"])}
        for name in ("lab-api", "lab-collector", "lab-postgres", "lab-redis")
        for obj in [client.get(NAMESPACE, "deployment", name)]}}


def live_check(plan, client, *, exercise_lock=False):
    """실습 구성의 변경은 하지 않으며 잠금 점검만 명시 선택 때 실행한다."""
    namespace = client.get(NAMESPACE, "namespace", NAMESPACE)
    if any(namespace["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items()):
        raise ValueError("실습 namespace 소유 표식이 다릅니다")
    current = client.get(NAMESPACE, "deployment", "lab-api")
    require_manual_deployment(current)
    for kind, name in (("application", APPLICATION), ("appproject", PROJECT)):
        if client.command("-n", ARGO_NAMESPACE, "get", kind, name, "--ignore-not-found", "-o", "json").strip():
            raise ValueError("기존 실습 GitOps 등록이 있어 신규 준비 절차를 중단합니다")
    controller = client.get(ARGO_NAMESPACE, "statefulset", "argocd-application-controller")
    if "system:serviceaccount:argocd:" + controller["spec"]["template"]["spec"]["serviceAccountName"] != CONTROLLER:
        raise ValueError("서버 검증 정책의 Argo identity가 다릅니다")
    before = identities(client)
    lease = LabLease(client.command, "preflight-" + uuid.uuid4().hex)
    lock_result = {"exercised": False}
    if exercise_lock:
        lock_result.update(lease.acquire(), exercised=True)
    else:
        existing = lease._get()
        if existing is not None:
            lease._validate(existing)
            if existing["spec"].get("holderIdentity"):
                raise ValueError("공유 잠금 보유 중에는 준비 검사를 실행하지 않습니다")
    safe = False
    try:
        projected = json.loads(client.command("apply", "--dry-run=server", "-f", "-", "-o", "json",
                                              payload=json.dumps(plan["deployment"])))
        if (projected["metadata"]["uid"] != current["metadata"]["uid"]
                or projected["spec"] != current["spec"]):
            raise ValueError("GitOps 인계가 현재 앱 정의를 변경합니다")
        for item in plan["bootstrap"]:
            client.command("apply", "--dry-run=server", "-f", "-", "-o", "json", payload=json.dumps(item))
        if exercise_lock:
            contender = LabLease(client.command, "preflight-" + uuid.uuid4().hex)
            try:
                contender.acquire()
            except ValueError as error:
                if str(error) != "다른 실행 또는 미복구 실행이 공유 잠금을 보유하고 있습니다":
                    raise
            else:
                raise ValueError("동시에 두 실행이 공유 잠금을 취득했습니다")
            lock_result["concurrent_acquire_rejected"] = True
        after = identities(client)
        if before != after:
            raise ValueError("준비 검사 중 운영 또는 실습 식별자가 변경됐습니다")
        safe = True
        return {"server_dry_run_passed": True, "deployment_spec_unchanged": True,
                "identities_unchanged": True, "identities": after, "lock": lock_result,
                "bootstrap_applied": False, "application_synced": False}
    finally:
        # dry-run 실패만으로 잠금을 버리지 않는다. 식별자 불변을 추가로 확인한 때만 해제한다.
        if exercise_lock and (safe or identities(client) == before):
            lock_result.update(lease.release(recovery_verified=True, scenario_started=False))


def main():
    parser = argparse.ArgumentParser(description="B-06 S10 GitOps 계획 준비; 실제 적용·동기화 기능 없음")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--live-check", action="store_true")
    parser.add_argument("--exercise-lock", action="store_true")
    args = cli_paths(parser, parser.parse_args(), {"manifest": "input", "output": "output", "state_dir": "store"})
    if args.exercise_lock and not args.live_check:
        parser.error("공유 잠금 점검에는 --live-check가 필요합니다")
    if args.live_check and args.state_dir is None:
        parser.error("서버 점검에는 기존 실행기의 --state-dir가 필요합니다")
    try:
        plan = build_gitops(json.loads(args.manifest.read_bytes()), args.revision)
        summary = validate_gitops(plan, json.loads(args.manifest.read_bytes()))
        source_ready = source_matches(plan)
        result = {**summary, "plan": plan, "source_commit_matches_deployment": source_ready,
                  "ready_to_adopt": False, "live_check_performed": False}
        if args.live_check:
            state_dir = args.state_dir
            if not state_dir.is_dir() or state_dir.stat().st_mode & 0o077:
                raise ValueError("기존 개인 실습 제어 폴더가 필요합니다")
            fd = os.open(state_dir / ".scenario-lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                control = state_dir / "control.json"
                if control.is_symlink():
                    raise ValueError("실습 제어 파일은 링크일 수 없습니다")
                if control.exists():
                    previous = json.loads(control.read_bytes())
                    if (previous.get("active") or previous.get("recovery_complete") is not True
                            or previous.get("not_before", 0) > time.time()):
                        raise ValueError("이전 실습의 복구와 지표 창을 먼저 확인해야 합니다")
                result["live_check"] = live_check(plan, LabClient(), exercise_lock=args.exercise_lock)
                result["live_check_performed"] = True
        write_private_json(args.output, result)
    except Exception:
        parser.exit(1, "실습 GitOps 준비 검사 실패: 기존 소유권·잠금·복구·서버 검증 결과를 확인하세요. 실제 적용은 실행하지 않았습니다.\n")
    print("실습 GitOps 계획 저장 완료. 실제 등록·동기화·장애 주입은 실행하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
