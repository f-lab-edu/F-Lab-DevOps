"""격리 실습 worker의 기존 큐에 조사 한 건을 접수하는 일회성 Job 정의."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import re


TARGET = "url-shortener-b6-lab"
QUESTION = "현재 격리 실습 앱의 이미지 취득 실패 원인과 근거를 조사해줘"
IMAGE = re.compile(r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener-practice-ai-ops@sha256:[a-f0-9]{64}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")


def validate_s10_live_failure(application: dict, deployment: dict, replica_set: dict,
                              pods: list[dict], lease: dict, *, baseline_revision: str,
                              failure_revision: str, failure_image: str, run_id: int,
                              run_attempt: int,
                              now: datetime | None = None) -> None:
    """S10 관측 창의 고정 대상·현재 실패 Pod·잠금을 대조한다."""
    moment = now or datetime.now(timezone.utc)
    if moment.utcoffset() != timedelta(0):
        raise ValueError("실습 실패 확인 시각은 UTC여야 합니다")
    if (type(run_id) is not int or not 1 <= run_id <= 10**15
            or type(run_attempt) is not int or not 1 <= run_attempt <= 100
            or not isinstance(baseline_revision, str) or not SHA.fullmatch(baseline_revision)
            or not isinstance(failure_revision, str) or not SHA.fullmatch(failure_revision)
            or baseline_revision == failure_revision or not isinstance(failure_image, str)
            or not failure_image.startswith("716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener@")
            or not DIGEST.fullmatch(failure_image.split("@", 1)[1])):
        raise ValueError("실습 실패 입력이 유효하지 않습니다")
    try:
        source = application["spec"]["source"]
        operation = application["status"]["operationState"]
        dep_meta = deployment["metadata"]
        dep_spec = deployment["spec"]
        pod = pods[0]
        pod_meta = pod["metadata"]
        pod_status = pod["status"]["containerStatuses"][0]
        dep_container = dep_spec["template"]["spec"]["containers"][0]
        pod_container = pod["spec"]["containers"][0]
        owner = pod_meta["ownerReferences"][0]
        rs_owner = replica_set["metadata"]["ownerReferences"][0]
        started = datetime.fromisoformat(operation["startedAt"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(pod_meta["creationTimestamp"].replace("Z", "+00:00"))
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise ValueError("실습 실패 원본 구조가 유효하지 않습니다") from None
    holder = "ci-" + hashlib.sha256(f"{run_id}:{run_attempt}".encode()).hexdigest()[:32]
    if (len(pods) != 1 or application["metadata"].get("name") != "lab-target"
            or application["metadata"].get("namespace") != "argocd"
            or application["spec"].get("project") != "b6-lab"
            or source.get("repoURL") != "https://github.com/f-lab-edu/F-Lab-DevOps.git"
            or source.get("path") != "ai-ops/lab" or source.get("targetRevision") != baseline_revision
            or application["spec"].get("syncPolicy", {}).get("automated") is not None
            or operation.get("phase") != "Succeeded"
            or operation.get("operation", {}).get("sync", {}).get("revision") != failure_revision
            or operation.get("syncResult", {}).get("revision") != failure_revision
            or dep_meta.get("name") != "lab-api" or dep_meta.get("namespace") != "ai-ops-lab"
            or dep_spec.get("replicas") != 1
            or len(dep_spec["template"]["spec"]["containers"]) != 1
            or dep_container.get("name") != "api" or dep_container.get("image") != failure_image
            or deployment["status"].get("observedGeneration") != dep_meta.get("generation")
            or deployment["status"].get("readyReplicas", 0) != 0
            or replica_set["metadata"].get("namespace") != "ai-ops-lab"
            or rs_owner.get("kind") != "Deployment" or rs_owner.get("uid") != dep_meta.get("uid")
            or owner.get("kind") != "ReplicaSet" or owner.get("uid") != replica_set["metadata"].get("uid")
            or pod_meta.get("namespace") != "ai-ops-lab"
            or len(pod["spec"]["containers"]) != 1
            or pod_container.get("name") != "api" or pod_container.get("image") != failure_image
            or pod_status.get("name") != "api" or pod_status.get("image") != failure_image
            or pod_status.get("ready") is not False
            or pod_status.get("state", {}).get("waiting", {}).get("reason") not in ("ErrImagePull", "ImagePullBackOff")
            or lease["metadata"].get("name") != "b6-scenario-lock"
            or lease["metadata"].get("namespace") != "ai-ops-lab"
            or lease.get("spec", {}).get("holderIdentity") != holder
            or started.utcoffset() != timedelta(0) or created.utcoffset() != timedelta(0)
            or not timedelta(0) <= moment - started <= timedelta(minutes=5)
            or not timedelta(0) <= moment - created <= timedelta(minutes=5)):
        raise ValueError("현재 격리 실습 이미지 실패를 확인하지 못했습니다")


def build_live_capture_job(deployment: dict, *, run_id: int, run_attempt: int,
                           failure_revision: str) -> dict:
    """현재 worker 이미지·설정·PVC에 결합하고 별도 조회 자격은 싣지 않는다."""
    if (type(run_id) is not int or not 1 <= run_id <= 10**15
            or type(run_attempt) is not int or not 1 <= run_attempt <= 100
            or not isinstance(failure_revision, str) or not SHA.fullmatch(failure_revision)):
        raise ValueError("실습 CI 실행 ID 또는 실패 revision이 유효하지 않습니다")
    try:
        metadata = deployment["metadata"]
        spec = deployment["spec"]
        template = spec["template"]
        pod = template["spec"]
        containers = pod["containers"]
        volumes = pod["volumes"]
        worker = next(item for item in containers if item["name"] == "ci-worker")
        config = next(item for item in volumes if item["name"] == "config")
        data = next(item for item in volumes if item["name"] == "data")
    except (KeyError, TypeError, StopIteration):
        raise ValueError("실습 worker Deployment 구조가 유효하지 않습니다") from None
    config_sources = {item.get("configMap", {}).get("name") for item in config.get("projected", {}).get("sources", [])}
    if (metadata.get("name") != "ci-worker" or metadata.get("namespace") != "ai-ci"
            or not isinstance(metadata.get("uid"), str) or not metadata["uid"]
            or spec.get("replicas") != 1 or pod.get("serviceAccountName") != "ci-worker"
            or template.get("metadata", {}).get("labels", {}).get("app") != "ci-worker"
            or len(containers) != 2 or {item.get("name") for item in containers} != {"ci-worker", "projection-read"}
            or not IMAGE.fullmatch(worker.get("image", ""))
            or any(item["image"] != worker["image"] for item in containers)
            or data.get("persistentVolumeClaim", {}).get("claimName") != "ci-queue"
            or (config.get("configMap", {}).get("name") != "ci-config"
                and config_sources != {"ci-config", "ci-normal-binding"})):
        raise ValueError("실습 worker의 이미지·설정·PVC가 고정 범위와 다릅니다")
    name = f"c2-live-capture-{run_id}-{run_attempt}"
    args = ["queue-submit", "--bindings", "/etc/ai-ops/bindings.json", "--runtime", "/etc/ai-ops/runtime.json",
            "--metric-queries", "/etc/ai-ops/metric-queries.json", "--store", "/data/incidents",
            "--target", TARGET, "--question", QUESTION, "--request-id", name]
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "namespace": "ai-ci",
            "labels": {"app.kubernetes.io/part-of": "ai-ci", "ai-ops/c2-live-capture": "true"},
            "annotations": {"ai-ops/s10-failure-revision": failure_revision,
                            "ai-ops/worker-deployment-uid": metadata["uid"]}},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 180, "ttlSecondsAfterFinished": 300,
                     "template": {"metadata": {"labels": {"app": "c2-live-capture"}}, "spec": {
                         "restartPolicy": "Never", "serviceAccountName": "ci-worker",
                         "automountServiceAccountToken": False, "enableServiceLinks": False,
                         "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                                             "fsGroup": 10001, "fsGroupChangePolicy": "OnRootMismatch",
                                             "seccompProfile": {"type": "RuntimeDefault"}},
                         "affinity": {"podAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
                             "labelSelector": {"matchLabels": {"app": "ci-worker"}},
                             "topologyKey": "kubernetes.io/hostname"}]}},
                         "containers": [{"name": "submit", "image": worker["image"], "command": ["python", "-m", "ai_ops.cli"],
                                         "args": args, "env": [{"name": "HOME", "value": "/tmp"}],
                                         "resources": {"requests": {"cpu": "25m", "memory": "64Mi"},
                                                       "limits": {"cpu": "150m", "memory": "192Mi"}},
                                         "securityContext": {"allowPrivilegeEscalation": False,
                                                             "readOnlyRootFilesystem": True,
                                                             "capabilities": {"drop": ["ALL"]}},
                                         "volumeMounts": [{"name": "config", "mountPath": "/etc/ai-ops", "readOnly": True},
                                                          {"name": "data", "mountPath": "/data"},
                                                          {"name": "tmp", "mountPath": "/tmp"}]}],
                         "volumes": [deepcopy(config), deepcopy(data), {"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}}]}}}}
