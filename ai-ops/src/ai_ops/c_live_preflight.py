"""실제 실습 상태가 이미지 변경안의 선행 조건을 만족하는지 읽기 전용으로 판정한다."""

from __future__ import annotations

import re

from ai_ops.c_recovery import IMAGE_REPOSITORY, _values


SHA = re.compile(r"[0-9a-f]{40}\Z")
REPO = "https://github.com/f-lab-edu/F-Lab-DevOps.git"


def assess_live_preflight(application: dict, deployment: dict, lease: dict,
                          values_bytes: bytes) -> dict:
    """현재 배포가 이미 복구됐거나 values가 비활성이면 초안 연결을 거부한다."""
    spec = application.get("spec", {})
    source = spec.get("source", {})
    target = spec.get("destination", {})
    revision = source.get("targetRevision", "")
    if (application.get("metadata", {}).get("name") != "lab-target"
            or application["metadata"].get("namespace") != "argocd"
            or spec.get("project") != "b6-lab" or source.get("repoURL") != REPO
            or source.get("path") != "ai-ops/lab" or not SHA.fullmatch(revision)
            or target != {"server": "https://kubernetes.default.svc", "namespace": "ai-ops-lab"}
            or spec.get("syncPolicy", {}).get("automated") is not None
            or deployment.get("metadata", {}).get("name") != "lab-api"
            or deployment["metadata"].get("namespace") != "ai-ops-lab"
            or deployment.get("spec", {}).get("replicas") != 1
            or lease.get("metadata", {}).get("name") != "b6-scenario-lock"
            or lease["metadata"].get("namespace") != "ai-ops-lab"):
        raise ValueError("변경안 실시간 대상의 고정 범위가 일치하지 않습니다")
    values = _values(values_bytes)
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    values_image = IMAGE_REPOSITORY + "@" + values["api"]["image"]["digest"]
    blockers = []
    if set(source) != {"repoURL", "path", "targetRevision"}:
        blockers.append("values_source_inactive")
    if image != values_image:
        blockers.append("values_stale")
    if (application.get("status", {}).get("sync", {}).get("status") != "Synced"
            or application.get("status", {}).get("operationState", {}).get("phase") == "Running"):
        blockers.append("argo_not_stable")
    if (deployment.get("status", {}).get("readyReplicas") == 1
            and application.get("status", {}).get("health", {}).get("status") == "Healthy"):
        blockers.append("already_recovered")
    if lease.get("spec", {}).get("holderIdentity"):
        blockers.append("lab_locked")
    if not blockers:
        blockers.append("prior_digest_and_failure_proof_required")
    return {"schema_version": 1, "target": "urlshortener/ai-ops-lab/lab-api/practice",
            "base_sha": revision, "target_uid": deployment["metadata"].get("uid"),
            "blockers": blockers, "proposal_allowed": False,
            "cluster_or_github_write_performed": False}
