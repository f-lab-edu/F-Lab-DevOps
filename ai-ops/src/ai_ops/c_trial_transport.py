"""운영자 Mac에서 고정 실습 대상만 읽고 Argo·조사 Job 요청을 전송한다."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

from ai_ops.c_verification_transport import CONTEXT, LIMIT, RESOURCES
from ai_ops.c_recovery_snapshot import same_recovery_resources


TRIAL_RESOURCES = {
    **RESOURCES,
    "lease": ("ai-ops-lab", "lease", "b6-scenario-lock"),
    "worker": ("ai-ci", "deployment", "ci-worker"),
    "relay": ("ai-ci", "deployment", "ci-relay"),
    "pvc": ("ai-ci", "pvc", "ci-queue"),
    "project": ("argocd", "appproject", "b6-lab"),
    "production": ("url-shortener", "deployment", "url-shortener-api"),
}


class SnapshotChanged(ValueError):
    """두 읽기 사이의 상태 변경이며 권한 오류나 다른 UID 허용을 뜻하지 않는다."""


class C4TrialTransport:
    """도구 호출을 고정하고 자격·외부 오류 본문을 출력하지 않는다."""

    def __init__(self, kubeconfig, *, image_check, guard_check, merge_session,
                 clock=None, monotonic=time.monotonic, sleep=time.sleep):
        self.kubeconfig = Path(kubeconfig).expanduser().absolute()
        info = self.kubeconfig.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
            raise ValueError("기존 사용자 전용 kubeconfig가 필요합니다")
        self.image_check = image_check
        self.guard_check = guard_check
        self.merge_session = merge_session
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic
        self.sleep = sleep
        config = json.loads(self._call(["kubectl", "--kubeconfig", str(self.kubeconfig),
                                       "config", "view", "--minify", "-o", "json"]))
        if config.get("current-context") != CONTEXT:
            raise ValueError("대상 EKS 문맥이 다릅니다")
        operator = json.loads(self._call(["gh", "api", "user"]))
        if operator.get("id") != 149856960 or operator.get("type") != "User":
            raise ValueError("현재 인증된 실습 운영자 identity가 다릅니다")

    def _call(self, command, *, payload=None, env=None, timeout=20):
        result = subprocess.run(command, input=payload, capture_output=True, env=env, timeout=timeout)
        if result.returncode or len(result.stdout) > LIMIT:
            raise ValueError("고정 실습 도구 호출이 실패했습니다")
        return result.stdout

    def _kube(self, *args, payload=None, timeout=20):
        return self._call(["kubectl", "--kubeconfig", str(self.kubeconfig), "--context", CONTEXT,
                          "--request-timeout=15s", *args], payload=payload, timeout=timeout)

    def get(self, resource):
        namespace, *target = TRIAL_RESOURCES[resource]
        result = json.loads(self._kube("-n", namespace, "get", *target, "-o", "json"))
        if result.get("metadata", {}).get("continue"):
            raise ValueError("잘린 Kubernetes 원본은 사용할 수 없습니다")
        return result

    def snapshot(self):
        started = self.clock()
        # 같은 읽기 단계만 병렬화하며 전후 대조 단계 사이에는 barrier를 둔다.
        with ThreadPoolExecutor(max_workers=5) as pool:
            app, dep, lease, pod_list, set_list = list(pool.map(self.get,
                ("application", "deployment", "lease", "pods", "replicasets")))
            after = list(pool.map(self.get, ("application", "deployment", "lease")))
        pods, sets = pod_list["items"], set_list["items"]
        owners = pods[0].get("metadata", {}).get("ownerReferences", []) if len(pods) == 1 else []
        matching = [item for item in sets if len(owners) == 1 and owners[0].get("kind") == "ReplicaSet"
                    and item["metadata"]["uid"] == owners[0].get("uid")]
        if not same_recovery_resources(
                {"application": app, "deployment": dep, "lease": lease},
                dict(zip(("application", "deployment", "lease"), after)), now=self.clock()):
            raise SnapshotChanged("조회 도중 실습 원본이 바뀌었습니다")
        return {"application": app, "deployment": dep, "lease": lease, "pods": pods,
                "replica_set": matching[0] if len(matching) == 1 else {}, "checked_at": started.isoformat()}

    def lease_command(self, *args, payload=None):
        if args == ("-n", "ai-ops-lab", "get", "lease", "b6-scenario-lock", "--ignore-not-found", "-o", "json"):
            return self._kube(*args).decode()
        if args != ("replace", "-f", "-", "-o", "json") or payload is None:
            raise ValueError("공유 Lease의 조회·CAS 교체만 허용합니다")
        lease = json.loads(payload)
        if (lease.get("kind") != "Lease" or lease.get("apiVersion") != "coordination.k8s.io/v1"
                or lease["metadata"].get("name") != "b6-scenario-lock"
                or lease["metadata"].get("namespace") != "ai-ops-lab"
                or not lease["metadata"].get("uid") or not lease["metadata"].get("resourceVersion")):
            raise ValueError("고정 Lease 원본이 필요합니다")
        return self._kube(*args, payload=payload.encode()).decode()

    def create_capture_job(self, job):
        if (job.get("kind") != "Job" or job["metadata"].get("namespace") != "ai-ci"
                or not re.fullmatch(r"c2-source-capture-[0-9]{1,16}", job["metadata"].get("name", ""))):
            raise ValueError("고정 조사 Job만 생성합니다")
        result = json.loads(self._kube("create", "-f", "-", "-o", "json", payload=json.dumps(job).encode()))
        if result["metadata"].get("name") != job["metadata"]["name"]:
            raise ValueError("조사 Job 생성 응답을 확인하지 못했습니다")
        return {"name": result["metadata"]["name"], "uid": result["metadata"]["uid"]}

    def argo(self, role, command):
        prefix = command[:3]
        if (role not in ("sync", "rollback") or prefix not in
                (["app", "set", "lab-target"], ["app", "sync", "lab-target"])
                or command[3:4] != ["--revision"] or len(command) not in (5, 6, 7)
                or not re.fullmatch(r"[a-f0-9]{40}", command[4])
                or command[5:] not in ([], ["--async"], ["--timeout", "60"], ["--timeout", "150"])):
            raise ValueError("고정 실습 SHA의 set/sync만 허용합니다")
        token = os.environ.get("C04_SYNC_ARGOCD_TOKEN" if role == "sync" else "C04_ROLLBACK_ARGOCD_TOKEN")
        if not token:
            raise ValueError("실습 전용 단기 Argo 자격이 필요합니다")
        env = {**os.environ, "ARGOCD_AUTH_TOKEN": token}
        # 저장된 관리자 Argo context가 사용되지 않도록 매 호출마다 빈 설정을 사용한다.
        with tempfile.TemporaryDirectory(prefix="c04-argo-") as folder:
            return self._call(["argocd", "--config", str(Path(folder)/"config"),
                "--server", "argocd.bidservice.store", "--grpc-web", *command], env=env, timeout=165)

    def require_images(self, plan):
        if self.image_check(plan) is not True:
            raise ValueError("실행 직전 정상 ECR 존재·실패 ECR 부재 확인이 필요합니다")

    def require_guards(self, plan):
        if self.guard_check(plan) is not True:
            raise ValueError("동일 기한의 로컬·원격 실제 감시 준비 확인이 필요합니다")

    def wait_until(self, planned_at):
        remaining = (planned_at-self.clock()).total_seconds()
        if remaining > 30:
            raise ValueError("예정 시각의 30초 전부터 주입 준비를 시작합니다")
        if remaining > 0:
            self.sleep(remaining)

    def wait_failure(self, plan):
        from ai_ops.c2_active_capture import _validate_active_failure

        deadline = min(self.monotonic()+60, self.monotonic()+
                       max(0, (plan.manual_recovery_at-self.clock()).total_seconds()))
        while self.monotonic() < deadline:
            try:
                snapshot = self.snapshot()
                _validate_active_failure(snapshot["application"], snapshot["deployment"],
                    snapshot["replica_set"], snapshot["pods"], snapshot["lease"],
                    failure_revision=plan.failure_revision, failure_image=plan.failure_image,
                    application_uid=plan.application_uid, deployment_uid=plan.deployment_uid,
                    source_repository=plan.repository, now=self.clock(),
                    expected_lease_holder=snapshot["lease"]["spec"].get("holderIdentity"))
                return snapshot
            except ValueError:
                self.sleep(2)
        raise ValueError("기한 내 실제 ImagePullBackOff를 확인하지 못했습니다")

    def wait_healthy(self, revision, image):
        deadline = self.monotonic()+150
        while self.monotonic() < deadline:
            try:
                snapshot = self.snapshot()
            except SnapshotChanged:
                self.sleep(1)
                continue
            app, dep = snapshot["application"], snapshot["deployment"]
            if (app["spec"]["source"]["targetRevision"] == revision
                    and app["status"]["sync"].get("revision") == revision
                    and app["status"]["sync"].get("status") == "Synced"
                    and app["status"]["health"].get("status") == "Healthy"
                    and app["status"]["operationState"].get("phase") == "Succeeded"
                    and app["status"]["operationState"]["syncResult"].get("revision") == revision
                    and dep["spec"]["template"]["spec"]["containers"][0]["image"] == image
                    and dep["status"].get("readyReplicas") == 1
                    and dep["status"].get("observedGeneration") == dep["metadata"].get("generation")
                    and not app.get("operation")):
                return snapshot
            self.sleep(2)
        raise ValueError("고정 정상 SHA의 준비 상태 확인 기한을 넘었습니다")
