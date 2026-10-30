"""C-02 실습의 독립 원복 실행 대상을 고정하고 다른 변경을 거부한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re


SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener@sha256:[0-9a-f]{64}\Z")


def validate_plan(plan: dict, *, now: datetime) -> datetime:
    """승인된 실습 대상·두 SHA·짧은 원복 기한만 허용한다."""
    if (set(plan) != {"schema_version", "target", "application_uid", "deployment_uid",
                      "baseline_revision", "failure_revision", "normal_image", "failure_image",
                      "deadline_utc"}
            or plan["schema_version"] != 1
            or plan["target"] != "urlshortener/ai-ops-lab/lab-api/practice"
            or not all(isinstance(plan[key], str) and plan[key]
                       for key in ("application_uid", "deployment_uid"))
            or not all(isinstance(plan[key], str) and SHA.fullmatch(plan[key])
                       for key in ("baseline_revision", "failure_revision"))
            or plan["baseline_revision"] == plan["failure_revision"]
            or not all(isinstance(plan[key], str) and IMAGE.fullmatch(plan[key])
                       for key in ("normal_image", "failure_image"))
            or plan["normal_image"] == plan["failure_image"]
            or now.utcoffset() != timedelta(0)):
        raise ValueError("C-02 원복 계획이 고정 실습 범위를 벗어났습니다")
    try:
        deadline = datetime.fromisoformat(plan["deadline_utc"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("C-02 원복 기한이 유효하지 않습니다") from exc
    if deadline.utcoffset() != timedelta(0) or not now < deadline <= now + timedelta(minutes=15):
        raise ValueError("C-02 원복 기한은 15분 이내여야 합니다")
    return deadline


def rollback_decision(plan: dict, app: dict, deployment: dict) -> str:
    """현재 UID·대상·이미지를 검증한 뒤 원복 또는 안전한 대기를 결정한다."""
    try:
        source = app["spec"]["source"]
        destination = app["spec"]["destination"]
        revision = source["targetRevision"]
        image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
        if (app["metadata"]["name"] != "lab-target" or app["metadata"]["namespace"] != "argocd"
                or app["metadata"]["uid"] != plan["application_uid"]
                or app["spec"]["project"] != "b6-lab"
                or source.get("repoURL") != "https://github.com/f-lab-edu/F-Lab-DevOps.git"
                or source.get("path") != "ai-ops/lab"
                or destination != {"server": "https://kubernetes.default.svc", "namespace": "ai-ops-lab"}
                or app["spec"].get("syncPolicy", {}).get("automated") is not None
                or deployment["metadata"]["name"] != "lab-api"
                or deployment["metadata"]["namespace"] != "ai-ops-lab"
                or deployment["metadata"]["uid"] != plan["deployment_uid"]
                or deployment["spec"]["replicas"] != 1):
            raise ValueError("C-02 원복 대상의 소유권이 바뀌었습니다")
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("C-02 원복 대상 구조가 유효하지 않습니다") from exc
    if revision == plan["baseline_revision"]:
        if image == plan["normal_image"]:
            return "verify_normal"
        if image == plan["failure_image"]:
            return "sync_baseline"
    if revision == plan["failure_revision"] and image in (plan["normal_image"], plan["failure_image"]):
        return "set_and_sync_baseline"
    raise ValueError("C-02 원복 대상에 승인하지 않은 revision 또는 이미지가 있습니다")
