"""새 격리 실습의 고정 계획과 실제 원본 사전 점검을 생성한다."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from ai_ops.b6_ci import validate_sources
from ai_ops.c2_active_capture import build_active_capture_job
from ai_ops.c_trial_execution import TrialPlan, verify_normal_trial
from ai_ops.c_trial_github import GitHubMergeSession
from ai_ops.c_trial_transport import C4TrialTransport
from ai_ops.contracts import _utc_datetime, sha256_json
from ai_ops.path_policy import cli_paths, write_private_json


def github(path, *, paginate=False):
    command = ["gh", "api", path]
    if paginate:
        command += ["--paginate", "--slurp"]
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode or len(result.stdout) > 4*1024*1024:
        raise ValueError("GitHub 사전 원본 조회가 실패했습니다")
    return json.loads(result.stdout)


def prepare(args):
    expected = json.loads(args.previous_preflight.read_text())
    cloud = json.loads(args.image_evidence.read_text())
    now = datetime.now(timezone.utc)
    when_ready = getattr(args, 'when_ready', False)
    planned = None if when_ready else _utc_datetime(args.planned_at)
    if not when_ready and not 300 < (planned-now).total_seconds() <= 3600:
        raise ValueError("새 예정 시각은 현재로부터 5분 초과·1시간 이내로 지정합니다")
    if (cloud.get("account_id") != "764428911194" or cloud.get("cluster_status") != "ACTIVE"
            or cloud.get("cluster_arn") != "arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener"
            or cloud.get("failure_absent") is not True or cloud.get("normal_status") != "ACTIVE"
            or not 0 <= (now-_utc_datetime(cloud["checked_at"])).total_seconds() <= 120):
        raise ValueError("같은 대상의 최신 AWS MCP 이미지 확인이 필요합니다")
    transport = C4TrialTransport(args.kubeconfig, image_check=lambda plan: False,
        guard_check=lambda plan: False, merge_session=GitHubMergeSession)
    snapshot = transport.snapshot()
    worker, pvc = transport.get("worker"), transport.get("pvc")
    normal_sha, failure_sha = expected["normal_revision"], expected["failure_candidate"]
    values = {}
    for label, revision in (("normal", normal_sha), ("failure", failure_sha), ("seed", expected["seed_main"])):
        blob = github(f"repos/joungGo/c03-execution-lab/contents/ai-ops/lab/values.yaml?ref={revision}")
        values[label] = json.loads(base64.b64decode(blob["content"]))
    normal_image, failure_image = validate_sources(values["normal"], values["failure"])
    if (values["seed"] != values["failure"] or cloud["normal_digest"] != normal_image.split("@")[1]
            or cloud["failure_digest"] != failure_image.split("@")[1]):
        raise ValueError("실제 Git 소스와 ECR 이미지가 다릅니다")
    targets = dict(trial_id=args.trial_id, repository='https://github.com/joungGo/c03-execution-lab.git',
        application_uid=expected["application_uid"],
        deployment_uid=expected["deployment_uid"], lease_uid=expected["lease_uid"],
        worker_uid=worker["metadata"]["uid"], pvc_uid=pvc["metadata"]["uid"],
        normal_revision=normal_sha, failure_revision=failure_sha, seed_main=expected["seed_main"],
        workflow_main=expected["workflow_main"],
        normal_image=normal_image, failure_image=failure_image)
    plan = SimpleNamespace(**targets) if when_ready else TrialPlan(**targets, schema_version=2, planned_at=planned,
        approval_cutoff=planned-timedelta(minutes=5), manual_recovery_at=planned+timedelta(seconds=240),
        review_deadline=planned+timedelta(seconds=210),minimum_review_seconds=120,
        rollback_deadline=planned+timedelta(seconds=300))
    verify_normal_trial(plan, snapshot, now=datetime.now(timezone.utc))
    project, relay, production = (transport.get(key) for key in ("project", "relay", "production"))
    repositories = project["spec"].get("sourceRepos", [])
    if (plan.repository not in repositories or not set(repositories).issubset({
            plan.repository, "https://github.com/f-lab-edu/F-Lab-DevOps.git"})
            or project["spec"].get("destinations") != [
                {"namespace": "ai-ops-lab", "server": "https://kubernetes.default.svc"}]
            or project["spec"].get("namespaceResourceWhitelist") != [{"group": "apps", "kind": "Deployment"}]
            or project["spec"].get("clusterResourceWhitelist")):
        raise ValueError("실습 Project의 허용 저장소가 다릅니다")
    for name in ("c03-sync", "c03-rollback"):
        roles = [role for role in project["spec"]["roles"] if role["name"] == name]
        if len(roles) != 1 or roles[0].get("jwtTokens") or roles[0].get("groups"):
            raise ValueError("기존 실습 역할의 자격 회수 상태가 다릅니다")
        wanted = [f"p, proj:b6-lab:{name}, applications, {verb}, b6-lab/lab-target, allow"
                  for verb in ("get", "update", "sync")]
        if roles[0]["policies"] != wanted:
            raise ValueError("실습 Argo 역할의 최소 권한이 다릅니다")
    if (worker["status"].get("readyReplicas") != 1 or relay["status"].get("readyReplicas") != 1
            or pvc["status"].get("phase") != "Bound"
            or production["status"].get("readyReplicas") != production["spec"]["replicas"]):
        raise ValueError("기존 worker·relay·PVC·운영 기준선이 다릅니다")
    repo = "repos/joungGo/c03-execution-lab"
    main = github(repo+"/git/ref/heads/main")["object"]["sha"]
    failure = github(repo+"/git/ref/heads/codex/c04-failure-4273d17cfde8")["object"]["sha"]
    protection = github(repo+"/branches/main/protection")
    if (main != plan.seed_main or failure != plan.failure_revision
            or protection["required_pull_request_reviews"]["required_approving_review_count"] != 1
            or protection["required_pull_request_reviews"]["dismiss_stale_reviews"] is not True
            or protection["enforce_admins"]["enabled"] is not True
            or protection["required_status_checks"]["strict"] is not True
            or "C03 Main PR Gate" not in protection["required_status_checks"]["contexts"]):
        raise ValueError("실습 Git 기준·필수 리뷰·CI 보호가 다릅니다")
    role = json.loads(transport._kube("-n", "ai-ci", "get", "role", "c2-projection-portforward", "-o", "json"))
    worker_pods = json.loads(transport._kube("-n", "ai-ci", "get", "pods", "-l", "app=ci-worker", "-o", "json"))
    if (worker_pods.get("metadata", {}).get("continue") or len(worker_pods["items"]) != 1
            or any(rule.get("resourceNames") != [worker_pods["items"][0]["metadata"]["name"]]
                   for rule in role["rules"])):
        raise ValueError("기존 조사 조회 권한과 현재 worker가 다릅니다")
    expected_rules = [{"apiGroups": [""], "resources": [resource], "verbs": [verb],
        "resourceNames": [worker_pods["items"][0]["metadata"]["name"]]}
        for resource, verb in (("pods", "get"), ("pods/portforward", "create"))]
    if sorted(role["rules"], key=lambda rule: rule["resources"]) != expected_rules:
        raise ValueError("현재 worker의 최소 조회 권한이 다릅니다")
    can_i = {}
    for namespace, verb, resource in (
        ("ai-ops-lab", "get", "leases/b6-scenario-lock"),
        ("ai-ops-lab", "update", "leases/b6-scenario-lock"), ("ai-ci", "create", "jobs.batch")):
        key = namespace+"/"+verb+"/"+resource
        can_i[key] = transport._kube("-n", namespace, "auth", "can-i", verb, resource).decode().strip() == "yes"
    if not all(can_i.values()):
        raise ValueError("기존 운영자의 잠금·조사 권한이 부족합니다")
    registration = GitHubMergeSession().verify_registration()
    workflow_main = github("repos/f-lab-edu/F-Lab-DevOps/git/ref/heads/main")["object"]["sha"]
    if workflow_main != expected["workflow_main"]:
        raise ValueError("원복 workflow main이 바뀌었습니다")
    workflow_hashes = {}
    if when_ready:
        from ai_ops.c_trial_ready_transport import GUARD_SOURCES
        if set(expected['code_sha256']) != GUARD_SOURCES:
            raise ValueError('새 준비 완료 감시의 배포된 소스 hash가 필요합니다')
    for path, expected_hash in expected["code_sha256"].items():
        obj = github("repos/f-lab-edu/F-Lab-DevOps/contents/"+path+"?ref="+workflow_main)
        observed = hashlib.sha256(base64.b64decode(obj["content"])).hexdigest()
        if observed != expected_hash:
            raise ValueError("원복 workflow·실행 코드가 바뀌었습니다")
        workflow_hashes[path] = observed
    secrets = github("repos/f-lab-edu/F-Lab-DevOps/environments/ai-ops-lab-ci/secrets")
    if secrets.get("total_count", 0) >= 30 or any(
            entry["name"] == "C03_ROLLBACK_ARGOCD_TOKEN" for entry in secrets["secrets"]):
        raise ValueError("기존 원복 Secret 회수 상태를 확인하지 못했습니다")
    runs = github("repos/f-lab-edu/F-Lab-DevOps/actions/workflows/401385940/runs?per_page=100",
                  paginate=True)
    active_runs = [run["id"] for page in runs for run in page["workflow_runs"] if run["status"] != "completed"]
    if active_runs:
        raise ValueError("기존 원복 workflow가 실행 중입니다")
    job = build_active_capture_job(worker, trial_id=plan.trial_id, failure_revision=plan.failure_revision)
    dry_run = json.loads(transport._kube("create", "--dry-run=server", "-f", "-", "-o", "json",
                                        payload=json.dumps(job).encode()))
    originals = {"application": snapshot["application"], "deployment": snapshot["deployment"],
        "lease": snapshot["lease"], "worker": worker, "pvc": pvc,
        "relay": relay, "production": production, "project": project}
    before = {key: {"uid": obj["metadata"]["uid"], "spec": obj["spec"]}
              for key, obj in originals.items()}
    current = {key: transport.get(key) for key in originals}
    after = {key: {"uid": obj["metadata"]["uid"], "spec": obj["spec"]}
             for key, obj in current.items()}
    if (before != after or github(repo+"/git/ref/heads/main")["object"]["sha"] != plan.seed_main
            or github(repo+"/branches/main/protection") != protection):
        raise ValueError("사전 검증 도중 대상·Git 기준·보호 설정이 바뀌었습니다")
    verify_normal_trial(plan, transport.snapshot(), now=datetime.now(timezone.utc))
    checks = {"normal_runtime": True, "lease_empty": True, "normal_and_failure_ecr": True,
        "git_seed_and_failure": True, "main_review_and_ci_protection": True, "worker_pvc_ready": True,
        "current_c02_portforward_scope": True, "merge_app_registration": registration,
        "capture_server_dry_run": dry_run["metadata"]["name"] == job["metadata"]["name"],
        "operator_permissions": can_i, "rollback_workflow_hashes": workflow_hashes,
        "rollback_credentials_absent": True, "active_rollback_runs": active_runs,
        "preservation_hashes": {key: sha256_json(value) for key, value in before.items()},
        "target_specs_and_git_protection_unchanged": True}
    if when_ready:
        from ai_ops.c_trial_ready import ReadyTrialPlan
        from ai_ops.c_trial_bundle import verify_runtime_bundle
        root = Path(__file__).resolve().parents[2]
        verify_runtime_bundle(root)
        plan = ReadyTrialPlan(targets=targets, preflight_sha256=sha256_json(checks),
            runtime_manifest_sha256=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest())
    return {"record_type": "c04_executor_preflight", "checked_at": datetime.now(timezone.utc).isoformat(),
        "plan": plan.model_dump(mode="json"), "plan_hash": plan.digest(), "checks": checks,
        "fault_injection_approved": False, "guards_armed": False,
        "new_installation_or_argo_tokens_issued": False, "app_jwt_used_for_readonly_registration": True,
        "ready_to_inject": False, "pending": ["new_per_run_fault_approval",
            "fresh_sync_rollback_credentials_and_permission_checks",
            "actual_local_and_remote_guard_readiness_before_injection",
            "new_incident_proposal_pr_and_exact_head_review"]}


def main():
    parser = argparse.ArgumentParser(description="C-04 실제 실행기 연결의 읽기/서버 dry-run 사전 점검")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--previous-preflight", type=Path, required=True)
    parser.add_argument("--image-evidence", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--planned-at")
    mode.add_argument("--when-ready", action='store_true')
    parser.add_argument("--trial-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"previous_preflight": "input",
        "image_evidence": "input", "output": "output"})
    result = prepare(args)
    write_private_json(args.output, result)
    print(json.dumps({"checks_passed": True, "plan_hash": result["plan_hash"],
                      "ready_to_inject": False}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        raise SystemExit("C-04 실행 전 검증을 중지했습니다. 대상·권한·입력을 확인하세요.") from None
