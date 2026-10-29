"""검증된 정상 CI와 현재 이미지 취득 실패를 읽기 전용으로 결합한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ai_ops.c_live_preflight import assess_live_preflight
from ai_ops.c_recovery import (DIGEST, IMAGE_REPOSITORY, IncidentProjection,
                               RecoveryRequest, _values)
from ai_ops.contracts import _utc_datetime, sha256_json
from ai_ops.normal_ci_contract import NormalCIBinding
from ai_ops.normal_ci_history import VerifiedNormalHistory


def _owner(item: dict, kind: str, uid: str) -> bool:
    owners = item.get("metadata", {}).get("ownerReferences", [])
    return (isinstance(owners, list) and len(owners) == 1
            and owners[0].get("controller") is True and owners[0].get("kind") == kind
            and owners[0].get("uid") == uid)


def assess_recovery_proof(application: dict, deployment: dict, lease: dict,
                          values_bytes: bytes, replica_set: dict, pods: list[dict],
                          binding: NormalCIBinding, history: VerifiedNormalHistory,
                          ecr_response: dict, bad_digest_absent: bool,
                          *, now: datetime | None = None) -> dict:
    """원본 API 검증 뒤 전달된 자료를 대조한다. 사건 연결 전에는 제안하지 않는다.

    호출자는 정상 CI를 GitHub의 ``verify_normal_ci_history``로 확인하고 ECR과
    Kubernetes 자료를 새로 읽어야 한다. 이 함수의 인자 자체는 인증 수단이 아니다.
    """
    base = assess_live_preflight(application, deployment, lease, values_bytes)
    blockers = [item for item in base["blockers"]
                if item != "prior_digest_and_failure_proof_required"]
    result = {**base, "blockers": blockers, "proof_ready": False,
              "proposal_allowed": False, "cluster_or_github_write_performed": False}
    if blockers:
        return result
    moment = now or datetime.now(timezone.utc)
    if moment.utcoffset() != timedelta(0):
        raise ValueError("복구 근거 확인 시각은 UTC여야 합니다")

    if (not isinstance(binding, NormalCIBinding)
            or not isinstance(history, VerifiedNormalHistory)
            or not isinstance(pods, list) or not isinstance(replica_set, dict)
            or not isinstance(ecr_response, dict) or type(bad_digest_absent) is not bool):
        raise ValueError("복구 근거 입력 형식이 유효하지 않습니다")
    receipt = history.receipt
    if (receipt.binding_hash != sha256_json(binding.model_dump(mode="json"))
            or receipt.identity.run_id != history.run_id
            or receipt.identity.run_attempt != history.run_attempt
            or receipt.identity.source_revision != history.source_revision
            or receipt.owned_data_removed is not True
            or receipt.baseline_unchanged is not True
            or receipt.lock_released is not True):
        blockers.append("normal_ci_origin_mismatch")

    old_image = binding.app.image
    old_digest = old_image.removeprefix(IMAGE_REPOSITORY + "@")
    bad_digest = _values(values_bytes)["api"]["image"]["digest"]
    app_uid = application["metadata"].get("uid")
    deployment_uid = deployment["metadata"].get("uid")
    revision = application["spec"]["source"]["targetRevision"]
    operation = application.get("status", {}).get("operationState", {})
    if (not DIGEST.fullmatch(old_digest)
            or old_digest == bad_digest
            or binding.app.deployment_uid != deployment_uid
            or receipt.finished_at >= moment
            or operation.get("phase") != "Succeeded"
            or operation.get("syncResult", {}).get("revision") != revision
            or application.get("status", {}).get("sync", {}).get("revision") != revision
            or application.get("status", {}).get("health", {}).get("status") not in
                    ("Degraded", "Progressing")):
        blockers.append("failure_revision_or_prior_baseline_unverified")

    containers = pods[0].get("spec", {}).get("containers", []) if len(pods) == 1 else []
    pod_owners = pods[0].get("metadata", {}).get("ownerReferences", []) if len(pods) == 1 else []
    if (len(pods) != 1 or replica_set.get("metadata", {}).get("namespace") != "ai-ops-lab"
            or not _owner(replica_set, "Deployment", deployment_uid)
            or not _owner(pods[0], "ReplicaSet", replica_set.get("metadata", {}).get("uid"))
            or pod_owners[0].get("name") != replica_set.get("metadata", {}).get("name")
            or pods[0].get("metadata", {}).get("namespace") != "ai-ops-lab"
            or not isinstance(pods[0].get("metadata", {}).get("uid"), str)
            or not pods[0]["metadata"]["uid"]
            or pods[0].get("metadata", {}).get("deletionTimestamp")
            or len(containers) != 1 or containers[0].get("name") != "api"
            or containers[0].get("image") != IMAGE_REPOSITORY + "@" + bad_digest
            or replica_set.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [{}])[0].get("image")
                    != IMAGE_REPOSITORY + "@" + bad_digest
            or deployment.get("status", {}).get("observedGeneration")
                    != deployment.get("metadata", {}).get("generation")
            or deployment.get("status", {}).get("readyReplicas", 0) != 0):
        blockers.append("current_pod_binding_unverified")
    else:
        states = pods[0].get("status", {}).get("containerStatuses", [])
        waiting = states[0].get("state", {}).get("waiting", {}) if len(states) == 1 else {}
        if (len(states) != 1 or states[0].get("name") != "api" or states[0].get("ready") is not False
                or states[0].get("image") != IMAGE_REPOSITORY + "@" + bad_digest
                or waiting.get("reason") not in ("ErrImagePull", "ImagePullBackOff")
                or _utc_datetime(pods[0]["metadata"]["creationTimestamp"]) <= receipt.finished_at
                or _utc_datetime(operation["startedAt"]) <= receipt.finished_at):
            blockers.append("current_image_pull_failure_unverified")

    details = ecr_response.get("imageDetails", [])
    if (not isinstance(details, list) or len(details) != 1
            or details[0].get("registryId") != "716174522908"
            or details[0].get("repositoryName") != "urlshortener"
            or details[0].get("imageDigest") != old_digest):
        blockers.append("prior_digest_not_found_in_ecr")
    if not bad_digest_absent:
        blockers.append("current_digest_absence_unverified")

    if blockers:
        return result
    result["proof_ready"] = True
    result["prior_digest"] = old_digest
    result["current_digest"] = bad_digest
    result["normal_ci_run_id"] = history.run_id
    result["normal_ci_artifact_digest"] = history.archive_digest
    result["application_uid"] = app_uid
    result["pod_uid_hash"] = sha256_json(pods[0]["metadata"]["uid"])
    result["deployment_uid_hash"] = sha256_json(deployment_uid)
    result["image_hash"] = sha256_json(IMAGE_REPOSITORY + "@" + bad_digest)
    result["pod_created_at"] = pods[0]["metadata"]["creationTimestamp"]
    result["proof_checked_at"] = moment.isoformat().replace("+00:00", "Z")
    result["blockers"] = ["incident_correlation_and_request_required"]
    return result


def assess_incident_request(live: dict, projection: IncidentProjection,
                            request: RecoveryRequest, *, now: datetime | None = None) -> dict:
    """인증된 조회·요청의 불변 해시를 현재 장애 근거에 묶는다.

    ``live``는 같은 실행에서 방금 재조회한 결과여야 한다. 저장된 JSON이나
    호출자가 만든 ``RecoveryRequest``만으로는 인증·현재 상태를 증명하지 못한다.
    """
    result = {**live, "incident_ready": False, "proposal_allowed": False,
              "cluster_or_github_write_performed": False}
    if live.get("proof_ready") is not True or live.get("blockers") != [
            "incident_correlation_and_request_required"]:
        return result
    if not isinstance(projection, IncidentProjection) or not isinstance(request, RecoveryRequest):
        raise ValueError("인증된 사건 조회와 해결 요청이 필요합니다")
    moment = now or datetime.now(timezone.utc)
    if moment.utcoffset() != timedelta(0):
        raise ValueError("사건 연결 시각은 UTC여야 합니다")
    failure = projection.image_failure
    if (failure is None or projection.diagnosis_status != "suspected_cause"
            or projection.current_state != "unknown"
            or request.incident_id != projection.incident_id or request.run_id != projection.run_id
            or request.target != projection.target or request.bundle_hash != projection.bundle_hash
            or request.report_hash != projection.report_hash
            or request.failure_binding_hash != sha256_json(failure.model_dump(mode="json"))
            or not set(failure.evidence_ids) <= set(projection.cited_evidence_ids)
            or failure.pod_uid_hash != live.get("pod_uid_hash")
            or failure.deployment_uid_hash != live.get("deployment_uid_hash")
            or failure.image_hash != live.get("image_hash")):
        result["blockers"] = ["incident_or_request_mismatch"]
        return result
    created = _utc_datetime(live["pod_created_at"])
    checked = _utc_datetime(live["proof_checked_at"])
    if (not created <= failure.observed_at <= checked <= moment
            or moment - checked > timedelta(seconds=60)
            or not failure.observed_at <= request.requested_at <= moment):
        result["blockers"] = ["incident_or_live_proof_stale"]
        return result
    result["incident_ready"] = True
    result["incident_id"] = projection.incident_id
    result["run_id"] = projection.run_id
    result["request_id"] = request.request_id
    result["bundle_hash"] = projection.bundle_hash
    result["report_hash"] = projection.report_hash
    result["failure_binding_hash"] = request.failure_binding_hash
    result["blockers"] = ["fresh_recheck_and_proposal_contract_required"]
    return result
