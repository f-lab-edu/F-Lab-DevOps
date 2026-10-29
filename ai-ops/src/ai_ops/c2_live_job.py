"""격리 실습 worker의 기존 큐에 조사 한 건을 접수하는 일회성 Job 정의."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re


TARGET = "url-shortener-b6-lab"
QUESTION = "현재 격리 실습 앱의 이미지 취득 실패 원인과 근거를 조사해줘"
IMAGE = re.compile(r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener-practice-ai-ops@sha256:[a-f0-9]{64}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")


def validate_live_preflight(preflight: dict, *, failure_revision: str,
                            now: datetime | None = None) -> None:
    """방금 읽은 격리 실습의 실제 이미지 실패 근거만 접수 준비에 사용한다."""
    moment = now or datetime.now(timezone.utc)
    if moment.utcoffset() != timedelta(0):
        raise ValueError("사전 판정 확인 시각은 UTC여야 합니다")
    if (not isinstance(preflight, dict) or preflight.get("target") != "urlshortener/ai-ops-lab/lab-api/practice"
            or preflight.get("base_sha") != failure_revision or preflight.get("proof_ready") is not True
            or preflight.get("blockers") != ["incident_correlation_and_request_required"]
            or preflight.get("proposal_allowed") is not False
            or preflight.get("cluster_or_github_write_performed") is not False
            or not isinstance(preflight.get("current_digest"), str)
            or not DIGEST.fullmatch(preflight["current_digest"])):
        raise ValueError("현재 실습 이미지 실패 근거가 준비되지 않았습니다")
    try:
        checked = datetime.fromisoformat(preflight["proof_checked_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        raise ValueError("사전 판정 시각이 유효하지 않습니다") from None
    if checked.utcoffset() != timedelta(0) or not timedelta(0) <= moment - checked <= timedelta(seconds=60):
        raise ValueError("실습 이미지 실패 사전 판정이 만료됐습니다")


def build_live_capture_job(deployment: dict, *, run_id: int, failure_revision: str) -> dict:
    """현재 worker 이미지·설정·PVC에 결합하고 별도 조회 자격은 싣지 않는다."""
    if type(run_id) is not int or not 1 <= run_id <= 10**15 or not isinstance(failure_revision, str) or not SHA.fullmatch(failure_revision):
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
    name = f"c2-live-capture-{run_id}"
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
