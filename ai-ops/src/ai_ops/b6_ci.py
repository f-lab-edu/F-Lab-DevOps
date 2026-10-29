"""실습 전용 CI의 고정 소스·잠금·실패 관측·자동 복구를 단계별로 실행한다."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from ai_ops.b6_coordination import LabLease
from ai_ops.b6_scenarios import LabClient
from ai_ops.contracts import sha256_json
from ai_ops.incidents import _atomic_write
from ai_ops.path_policy import permitted_path

REPO = "f-lab-edu/F-Lab-DevOps"
WORKFLOW = ".github/workflows/ai-ops-lab-ci.yml"
PATH = "ai-ops/lab/values.yaml"
APP = "lab-target"
NS = "ai-ops-lab"
ECR = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener"
SHA = re.compile(r"[a-f0-9]{40}")


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def command(args, *, timeout=45, allow_failure=False):
    """자격·서버 응답을 출력하지 않고 호출 시간과 반환 크기를 제한한다."""
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if len(p.stdout.encode()) > 2 * 1024 * 1024 or (p.returncode and not allow_failure):
        raise ValueError("실습 CI 명령 실패")
    return p


def validate_sources(baseline: dict, failure: dict) -> tuple[str, str]:
    """고정 Helm values의 이미지 digest 한 필드만 달라진 실패 소스를 허용한다."""
    expected = deepcopy(baseline)
    try:
        image = baseline["api"]["image"]
        failed_image = failure["api"]["image"]
        good_digest = image["digest"]
        bad_digest = failed_image["digest"]
    except (KeyError, TypeError):
        raise ValueError("실습 values 구조가 다릅니다") from None
    if (set(baseline) != {"api"} or set(baseline["api"]) != {"image"}
            or set(image) != {"repository", "digest"} or image["repository"] != ECR
            or not isinstance(good_digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", good_digest)
            or not isinstance(bad_digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", bad_digest)
            or good_digest == bad_digest):
        raise ValueError("실습 이미지 digest가 유효하지 않습니다")
    expected["api"]["image"]["digest"] = bad_digest
    if expected != failure:
        raise ValueError("실패 소스에 digest 외 변경이 있습니다")
    return ECR + "@" + good_digest, ECR + "@" + bad_digest


class LabCI:
    def __init__(self, root: Path):
        self.root = permitted_path(root, "store")
        if self.root.stat().st_mode & 0o077:
            raise ValueError("CI 자료 폴더는 개인 전용이어야 합니다")
        self.records = self.root / "records"
        self.records.mkdir(mode=0o700, exist_ok=True)
        self.client = LabClient()
        self.state_file = self.root / "state.json"

    def save(self, state):
        _atomic_write(self.state_file, json.dumps(state, sort_keys=True).encode())

    def load(self):
        state = json.loads(self.state_file.read_bytes())
        if state["run_id"] != os.environ["GITHUB_RUN_ID"] or state["run_attempt"] != os.environ["GITHUB_RUN_ATTEMPT"]:
            raise ValueError("다른 실행의 상태 파일입니다")
        return state

    def argo(self, *args, **kwargs):
        return command(["argocd", *args, "--server", "argocd.bidservice.store", "--grpc-web"], **kwargs)

    def app(self):
        app = json.loads(self.argo("app", "get", APP, "-o", "json").stdout)
        spec = app["spec"]
        if (spec["project"] != "b6-lab" or spec["source"]["repoURL"] != "https://github.com/" + REPO + ".git"
                or spec["source"]["path"] != "ai-ops/lab"
                or set(spec["source"]) != {"repoURL", "path", "targetRevision"}
                or spec["destination"] != {"server": "https://kubernetes.default.svc", "namespace": NS}
                or spec.get("syncPolicy", {}).get("automated") is not None):
            raise ValueError("실습 Application 범위가 다릅니다")
        return app

    def prepare(self):
        if (os.environ.get("GITHUB_REPOSITORY") != REPO or os.environ.get("GITHUB_REF") != "refs/heads/main"
                or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"):
            raise ValueError("main 수동 실행만 허용합니다")
        base, fail = os.environ["S10_BASELINE_REVISION"], os.environ["S10_FAILURE_REVISION"]
        for value in (base, fail, os.environ["GITHUB_SHA"]):
            if not SHA.fullmatch(value):
                raise ValueError("전체 소스 SHA가 필요합니다")
        command(["git", "merge-base", "--is-ancestor", base, os.environ["GITHUB_SHA"]])
        changed = command(["git", "diff", "--name-only", base, fail]).stdout.splitlines()
        if changed != [PATH]:
            raise ValueError("실패 커밋은 실습 파일 하나만 달라야 합니다")
        normal = json.loads(command(["git", "show", base + ":" + PATH]).stdout)
        broken = json.loads(command(["git", "show", fail + ":" + PATH]).stdout)
        good, bad = validate_sources(normal, broken)
        account = json.loads(command(["aws", "sts", "get-caller-identity"]).stdout)
        if account["Account"] != "716174522908":
            raise ValueError("AWS 계정이 다릅니다")
        command(["aws", "ecr", "describe-images", "--region", "ap-northeast-2", "--repository-name", "urlshortener", "--image-ids", "imageDigest=" + good.split("@")[1]])
        missing = command(["aws", "ecr", "describe-images", "--region", "ap-northeast-2", "--repository-name", "urlshortener", "--image-ids", "imageDigest=" + bad.split("@", 1)[1]], allow_failure=True)
        if not missing.returncode or "ImageNotFoundException" not in missing.stderr:
            raise ValueError("실패 digest 부재를 확인하지 못했습니다")
        app = self.app()
        deploy = self.client.get(NS, "deployment", "lab-api")
        if (app["spec"]["source"]["targetRevision"] != base
                or app["status"]["sync"]["status"] != "Synced" or app["status"]["health"]["status"] != "Healthy"
                or deploy["spec"]["template"]["spec"]["containers"][0]["image"] != good
                or deploy["status"].get("readyReplicas") != 1):
            raise ValueError("정상 기준선이 아닙니다")
        state = {"run_id": os.environ["GITHUB_RUN_ID"], "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
            "source_revision": os.environ["GITHUB_SHA"], "baseline_revision": base, "failure_revision": fail,
            "good_image": good, "bad_image": bad, "application_uid": app["metadata"]["uid"],
            "deployment_uid": deploy["metadata"]["uid"], "normal_template": deploy["spec"]["template"],
            "injection_requested": False, "deployment_verified_failure": False, "rollback_verified": False}
        # 상태와 소유자를 먼저 기록해 응답 유실 때도 복구 담당자가 실행을 추적할 수 있게 한다.
        state["holder"] = "ci-" + hashlib.sha256((state["run_id"] + ":" + state["run_attempt"]).encode()).hexdigest()[:32]
        self.save(state)
        lease = LabLease(self.client.command, state["holder"])
        state["lease"] = lease.acquire()
        self.save(state)

    def observe(self, state):
        app = self.app()
        d = self.client.get(NS, "deployment", "lab-api")
        if (app["metadata"]["uid"] != state["application_uid"] or d["metadata"]["uid"] != state["deployment_uid"]
                or app["spec"]["source"]["targetRevision"] != state["baseline_revision"]):
            raise ValueError("대상 UID가 변경됐습니다")
        template = deepcopy(d["spec"]["template"])
        image = template["spec"]["containers"][0]["image"]
        template["spec"]["containers"][0]["image"] = state["good_image"]
        if sha256_json(template) != sha256_json(state["normal_template"]):
            raise ValueError("실습 앱에 이미지 외 변경이 있습니다")
        reasons = []
        pods = json.loads(self.client.command("-n", NS, "get", "pods", "-l", "app=lab-api", "-o", "json"))["items"]
        for pod in pods[:4]:
            owner = next((o for o in pod["metadata"].get("ownerReferences", []) if o.get("controller")), {})
            if owner.get("kind") != "ReplicaSet":
                continue
            rs = self.client.get(NS, "replicaset", owner["name"])
            if rs["metadata"]["uid"] != owner["uid"] or not any(o.get("uid") == state["deployment_uid"] for o in rs["metadata"].get("ownerReferences", [])):
                raise ValueError("Pod 소유 관계가 다릅니다")
            if pod["spec"]["containers"][0]["image"] == state["bad_image"]:
                for c in pod["status"].get("containerStatuses", []):
                    reason = c.get("state", {}).get("waiting", {}).get("reason")
                    if reason in ("ErrImagePull", "ImagePullBackOff"):
                        reasons.append(reason)
        status = app["status"]
        return {"deployment_uid": state["deployment_uid"], "application_uid": state["application_uid"],
            "image": image, "generation_observed": d["status"].get("observedGeneration") == d["metadata"]["generation"], "ready_replicas": d["status"].get("readyReplicas", 0), "image_pull_reasons": sorted(set(reasons)),
            "sync_status": status["sync"]["status"], "health_status": status["health"]["status"],
            "operation_revision": status.get("operationState", {}).get("operation", {}).get("sync", {}).get("revision"),
            "operation_phase": status.get("operationState", {}).get("phase")}

    def record(self, state, stage, started, result, observed=None):
        record = {"schema_version": 2, "record_type": "b6_lab_ci_observation", "repository": REPO,
            "workflow": WORKFLOW, "run_id": state["run_id"], "run_attempt": state["run_attempt"],
            "source_revision": state["source_revision"], "stage": stage, "started_at": started, "observed_at": now(),
            "cluster_arn": "arn:aws:eks:ap-northeast-2:716174522908:cluster/urlshortener", "namespace": NS,
            "application": "argocd/lab-target", "baseline_revision": state["baseline_revision"],
            "failure_revision": state["failure_revision"], "verification_result": result, "observed": observed,
            "user_path_recovery_verified": False, "evidence_import_enabled": False}
        _atomic_write(self.records / (stage + ".json"), json.dumps(record, sort_keys=True).encode())

    def deploy(self):
        state = self.load()
        started = now()
        state["injection_requested"] = True
        self.save(state)
        observed = None
        try:
            self.argo("app", "sync", APP, "--revision", state["failure_revision"], "--async")
            for _ in range(30):
                observed = self.observe(state)
                if (observed["image"] == state["bad_image"] and observed["ready_replicas"] == 0
                        and observed["generation_observed"] and observed["image_pull_reasons"] and observed["operation_revision"] == state["failure_revision"]):
                    state["deployment_verified_failure"] = True
                    break
                time.sleep(3)
            if not state["deployment_verified_failure"]:
                raise ValueError("의도한 이미지 실패를 확인하지 못했습니다")
        finally:
            self.save(state)
            self.record(state, "deployment", started, "failure" if state["deployment_verified_failure"] else "inconclusive", observed)
        return 1

    def rollback(self):
        state = self.load()
        started = now()
        observed = None
        try:
            if state["injection_requested"]:
                self.argo("app", "sync", APP, "--revision", state["baseline_revision"], "--timeout", "150", timeout=165)
                self.argo("app", "wait", APP, "--sync", "--health", "--operation", "--timeout", "150", timeout=165)
            observed = self.observe(state)
            state["rollback_verified"] = bool(observed["image"] == state["good_image"] and observed["ready_replicas"] == 1
                and observed["generation_observed"] and observed["sync_status"] == "Synced" and observed["health_status"] == "Healthy"
                and observed["operation_phase"] == "Succeeded" and observed["operation_revision"] == state["baseline_revision"])
            if not state["rollback_verified"]:
                raise ValueError("원래 revision 복구를 확인하지 못했습니다")
        finally:
            self.save(state)
            self.record(state, "rollback", started, "success" if state["rollback_verified"] else "failure", observed)

    def finish(self):
        state = self.load()
        if state.get("lease") and state["rollback_verified"]:
            lease = LabLease(self.client.command, state["holder"])
            lease.uid = state["lease"]["uid"]
            state["lease"].update(lease.release(recovery_verified=True))
        self.record(state, "result", now(), "recovered_after_failure" if state["deployment_verified_failure"] and state["rollback_verified"] else "incomplete")
        self.save(state)
        # 정상 복구됐어도 최초 실패를 CI 성공으로 덮지 않는다.
        return 1


def main():
    import argparse
    parser = argparse.ArgumentParser(description="S10 격리 실습 CI; 정상 복구 후에도 최초 실패 유지")
    parser.add_argument("stage", choices=("prepare", "deploy", "rollback", "finish"))
    args = parser.parse_args()
    try:
        result = getattr(LabCI(Path(os.environ["AI_OPS_FILES_ROOT"])), args.stage)()
    except Exception:
        print("S10 단계 실패. 고정 artifact와 공유 잠금·원복 상태를 확인하세요.")
        return 1
    print("S10 단계 처리 완료: " + args.stage)
    return result or 0


if __name__ == "__main__":
    raise SystemExit(main())
