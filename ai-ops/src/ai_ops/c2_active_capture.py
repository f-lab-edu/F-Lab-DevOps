"""활성 GitOps 실패 revision에 결합된 C-02 일회성 조사 접수를 준비한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from ai_ops.c2_live_job import build_live_capture_job


SHA = re.compile(r"[a-f0-9]{40}\Z")
IMAGE = re.compile(r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener@sha256:[a-f0-9]{64}\Z")


def validate_active_failure(application: dict, deployment: dict, replica_set: dict,
                            pods: list[dict], lease: dict, *, failure_revision: str,
                            failure_image: str, application_uid: str,
                            deployment_uid: str, now: datetime | None = None) -> None:
    """활성 소스·동기화·Pod 소유 관계·현재 이미지 실패·빈 잠금을 검사한다."""
    moment = now or datetime.now(timezone.utc)
    if (moment.utcoffset() != timedelta(0)
            or not isinstance(failure_revision, str) or not SHA.fullmatch(failure_revision)
            or not isinstance(failure_image, str) or not IMAGE.fullmatch(failure_image)
            or not isinstance(application_uid, str) or not application_uid
            or not isinstance(deployment_uid, str) or not deployment_uid
            or len(pods) != 1):
        raise ValueError("C-02 활성 실패 입력이 유효하지 않습니다")
    try:
        source = application["spec"]["source"]
        operation = application["status"]["operationState"]
        pod = pods[0]
        container = pod["status"]["containerStatuses"][0]
        owner = pod["metadata"]["ownerReferences"][0]
        rs_owner = replica_set["metadata"]["ownerReferences"][0]
        started = datetime.fromisoformat(operation["startedAt"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(pod["metadata"]["creationTimestamp"].replace("Z", "+00:00"))
        dep_container = deployment["spec"]["template"]["spec"]["containers"]
        pod_container = pod["spec"]["containers"]
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("C-02 활성 실패 원본 구조가 유효하지 않습니다") from exc
    if (application["metadata"].get("name") != "lab-target"
            or application["metadata"].get("namespace") != "argocd"
            or application["metadata"].get("uid") != application_uid
            or application["spec"].get("project") != "b6-lab"
            or source != {"repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git",
                          "path": "ai-ops/lab", "targetRevision": failure_revision}
            or application["spec"].get("destination") != {
                "server": "https://kubernetes.default.svc", "namespace": "ai-ops-lab"}
            or application["spec"].get("syncPolicy", {}).get("automated") is not None
            or application["status"].get("sync", {}).get("status") != "Synced"
            or application["status"]["sync"].get("revision") != failure_revision
            or operation.get("phase") != "Succeeded"
            or operation.get("operation", {}).get("sync", {}).get("revision") != failure_revision
            or operation.get("syncResult", {}).get("revision") != failure_revision
            or deployment["metadata"].get("name") != "lab-api"
            or deployment["metadata"].get("namespace") != "ai-ops-lab"
            or deployment["metadata"].get("uid") != deployment_uid
            or deployment["spec"].get("replicas") != 1
            or len(dep_container) != 1 or dep_container[0].get("name") != "api"
            or dep_container[0].get("image") != failure_image
            or deployment["status"].get("observedGeneration") != deployment["metadata"].get("generation")
            or deployment["status"].get("readyReplicas", 0) != 0
            or replica_set["metadata"].get("namespace") != "ai-ops-lab"
            or rs_owner.get("kind") != "Deployment" or rs_owner.get("uid") != deployment["metadata"].get("uid")
            or owner.get("kind") != "ReplicaSet" or owner.get("uid") != replica_set["metadata"].get("uid")
            or pod["metadata"].get("namespace") != "ai-ops-lab"
            or len(pod_container) != 1 or pod_container[0].get("name") != "api"
            or pod_container[0].get("image") != failure_image
            or container.get("name") != "api" or container.get("image") != failure_image
            or container.get("ready") is not False
            or container.get("state", {}).get("waiting", {}).get("reason") not in ("ErrImagePull", "ImagePullBackOff")
            or lease["metadata"].get("name") != "b6-scenario-lock"
            or lease["metadata"].get("namespace") != "ai-ops-lab"
            or lease.get("spec", {}).get("holderIdentity")
            or started.utcoffset() != timedelta(0) or created.utcoffset() != timedelta(0)
            or not timedelta(0) <= moment - started <= timedelta(minutes=5)
            or not timedelta(0) <= moment - created <= timedelta(minutes=5)):
        raise ValueError("현재 활성 GitOps 이미지 실패를 확인하지 못했습니다")


def build_active_capture_job(worker: dict, *, trial_id: int, failure_revision: str) -> dict:
    """기존 최소 권한 Job의 저장소·이미지를 재사용하고 S10 식별자를 제거한다."""
    if type(trial_id) is not int or not 1 <= trial_id <= 10**15:
        raise ValueError("C-02 실습 번호가 유효하지 않습니다")
    job = build_live_capture_job(worker, run_id=trial_id, run_attempt=1,
                                 failure_revision=failure_revision)
    name = f"c2-source-capture-{trial_id}"
    job["metadata"]["name"] = name
    job["metadata"]["labels"] = {"app.kubernetes.io/part-of": "ai-ci",
                                  "ai-ops/c2-source-capture": "true"}
    job["metadata"]["annotations"] = {
        "ai-ops/source-failure-revision": failure_revision,
        "ai-ops/worker-deployment-uid": worker["metadata"]["uid"]}
    job["spec"]["template"]["metadata"]["labels"] = {"app": "c2-source-capture"}
    args = job["spec"]["template"]["spec"]["containers"][0]["args"]
    args[args.index("--request-id") + 1] = name
    return job
