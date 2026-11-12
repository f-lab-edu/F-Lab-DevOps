"""시각이 없는 C-03 보존 자료를 성공 표본으로 보충하지 않고 C-04에 연결한다."""

from datetime import datetime

from ai_ops.c_verification import (
    HTTPReading, RecoveryBinding, SyncReceipt, VerificationInput, VerificationPolicy,
    VerificationSample, VerificationSeries,
)
from ai_ops.contracts import sha256_json


def from_c03_archive(*, proposal: dict, receipt: dict, http: dict, runtime: dict,
                     policy: VerificationPolicy, evaluated_at: datetime) -> VerificationInput:
    """이전 실행의 정체성을 대조한다. 현재 클러스터의 회복 증거로 재사용하지 않는다."""
    target = {"cluster_id": "urlshortener", "namespace": "ai-ops-lab", "workload": "lab-api",
              "service": "lab-api", "environment": "practice"}
    if (proposal.get("proposal_hash") != sha256_json({k: v for k, v in proposal.items() if k != "proposal_hash"})
            or proposal.get("target") != target
            or proposal.get("target_uid") != receipt.get("target_uid")
            or proposal.get("desired_digest") != receipt.get("desired_digest")
            or runtime.get("sha") != receipt.get("merge_sha")
            or runtime.get("image") != "764428911194.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener@"+receipt.get("desired_digest", "")
            or receipt.get("http_checks") != http):
        raise ValueError("보존된 제안·동기화·HTTP·이미지가 서로 일치하지 않습니다")
    binding = RecoveryBinding(application_uid=receipt["application_uid"], target_uid=receipt["target_uid"])
    execution = SyncReceipt(binding=binding, proposal_id=proposal["proposal_id"],
        proposal_hash=proposal["proposal_hash"], merge_sha=receipt["merge_sha"],
        desired_digest=receipt["desired_digest"], sync_verified=receipt["sync_verified"],
        service_resolved=receipt["service_resolved"], verified_at=None,
        source_hash=sha256_json({"proposal": proposal, "receipt": receipt, "http": http, "runtime": runtime}))
    series = [VerificationSeries(kind="runtime", name="lab-api", binding=binding,
                                 source_hash=sha256_json(runtime), status="missing")]
    for path in policy.http_paths:
        points = ([VerificationSample(observed_at=None, reading=HTTPReading(
            status_code=http[path], response_contract_valid=None))] if path in http else [])
        series.append(VerificationSeries(kind="http", name=path, binding=binding,
            source_hash=sha256_json(http), status="available" if points else "missing", samples=points))
    return VerificationInput(provenance="archived", execution=execution, policy=policy,
                             evaluated_at=evaluated_at, series=series)
