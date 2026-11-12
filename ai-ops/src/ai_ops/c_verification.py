"""C-03 동기화 결과와 로컬 관측 자료로 서비스 회복을 판정한다.

파일의 해시는 입력의 동일성만 나타낸다. 이 모듈은 수집 출처를 인증하거나
승인을 발급하지 않으며 GitHub·클러스터·모델을 호출하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from ai_ops.contracts import Sha256, StrictModel, UTCDateTime, sha256_json


Revision = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
Digest = Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
UID = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")]
Name = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")]
Number = Annotated[float, Field(allow_inf_nan=False)]


class RecoveryBinding(StrictModel):
    """같은 이름으로 다시 설치한 대상을 섞지 않도록 UID까지 결합한다."""

    cluster_id: Literal["urlshortener"] = "urlshortener"
    namespace: Literal["ai-ops-lab"] = "ai-ops-lab"
    workload: Literal["lab-api"] = "lab-api"
    application_uid: UID
    target_uid: UID


class SyncReceipt(StrictModel):
    """C-03 결과의 서비스 미판정 상태를 C-04의 입력으로 유지한다."""

    binding: RecoveryBinding
    proposal_id: Annotated[StrictStr, Field(pattern=r"^cprop-[0-9a-f]{32}$")]
    proposal_hash: Sha256
    merge_sha: Revision
    desired_digest: Digest
    sync_verified: Literal[True]
    service_resolved: Literal[False]
    verified_at: UTCDateTime | None
    source_hash: Sha256


class MetricRequirement(StrictModel):
    """조회식·대상 조건의 해시와 운영자가 정한 회복 기준을 묶는다."""

    name: Name
    definition_hash: Sha256
    comparison: Literal["lte", "gte"]
    threshold: Number


class AlertRequirement(StrictModel):
    name: Name
    definition_hash: Sha256


class VerificationPolicy(StrictModel):
    """자료 누락과 표본 부족을 정상으로 바꾸지 않는 관찰 조건이다."""

    window_start: UTCDateTime | None
    window_end: UTCDateTime | None
    minimum_span_seconds: Annotated[StrictInt, Field(ge=30, le=900)] = 120
    maximum_gap_seconds: Annotated[StrictInt, Field(ge=1, le=120)] = 45
    minimum_samples: Annotated[StrictInt, Field(ge=2, le=100)] = 4
    maximum_report_delay_seconds: Annotated[StrictInt, Field(ge=1, le=900)] = 120
    http_paths: list[Literal["/readyz", "/items"]] = Field(min_length=2, max_length=2)
    metrics: list[MetricRequirement] = Field(min_length=1, max_length=12)
    alerts: list[AlertRequirement] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_window(self):
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("관찰 창의 시작과 끝을 함께 지정해야 합니다")
        if self.window_start is not None:
            span = (self.window_end - self.window_start).total_seconds()
            if not self.minimum_span_seconds <= span <= 900:
                raise ValueError("관찰 창은 최소 관찰 시간 이상, 15분 이하여야 합니다")
        if (set(self.http_paths) != {"/readyz", "/items"}
                or len({x.name for x in self.metrics}) != len(self.metrics)
                or len({x.name for x in self.alerts}) != len(self.alerts)):
            raise ValueError("필수 확인 항목이 없거나 중복됐습니다")
        return self


class RuntimeReading(StrictModel):
    kind: Literal["runtime"] = "runtime"
    revision: Revision
    operation_revision: Revision
    argo_sync: Literal["Synced", "OutOfSync", "Unknown"]
    argo_health: Literal["Healthy", "Progressing", "Degraded", "Missing", "Unknown"]
    operation_phase: Literal["Succeeded", "Running", "Failed", "Error", "Unknown"]
    generation: Annotated[StrictInt, Field(ge=1)]
    observed_generation: Annotated[StrictInt, Field(ge=0)]
    replicas: Annotated[StrictInt, Field(ge=0)]
    ready_replicas: Annotated[StrictInt, Field(ge=0)]
    pod_count: Annotated[StrictInt, Field(ge=0)]
    ready_pods: Annotated[StrictInt, Field(ge=0)]
    pod_images: list[Digest] = Field(max_length=20)


class HTTPReading(StrictModel):
    kind: Literal["http"] = "http"
    status_code: Annotated[StrictInt, Field(ge=100, le=599)] | None
    response_contract_valid: StrictBool | None


class MetricReading(StrictModel):
    kind: Literal["metric"] = "metric"
    value: Number | None


class AlertReading(StrictModel):
    """빈 ALERTS 결과만으로는 해당 규칙의 정상 평가를 입증할 수 없다."""

    kind: Literal["alert"] = "alert"
    rule_present: StrictBool
    evaluation: Literal["ok", "error", "no_data"]
    evaluated_at: UTCDateTime | None
    active_count: Annotated[StrictInt, Field(ge=0)] | None


Reading = Annotated[RuntimeReading | HTTPReading | MetricReading | AlertReading,
                    Field(discriminator="kind")]


class VerificationSample(StrictModel):
    observed_at: UTCDateTime | None
    reading: Reading


class VerificationSeries(StrictModel):
    kind: Literal["runtime", "http", "metric", "alert"]
    name: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    binding: RecoveryBinding
    definition_hash: Sha256 | None = None
    source_hash: Sha256
    status: Literal["available", "missing", "collection_failed"]
    samples: list[VerificationSample] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_samples(self):
        if self.status != "available" and self.samples:
            raise ValueError("미수집 자료에 관측 표본을 넣을 수 없습니다")
        if any(x.reading.kind != self.kind for x in self.samples):
            raise ValueError("관측 종류가 자료 묶음과 다릅니다")
        return self


class VerificationInput(StrictModel):
    schema_version: Literal[1] = 1
    provenance: Literal["synthetic", "archived", "operator_supplied"]
    execution: SyncReceipt
    policy: VerificationPolicy
    evaluated_at: UTCDateTime
    series: list[VerificationSeries] = Field(max_length=32)

    @model_validator(mode="after")
    def unique_series(self):
        if len({(x.kind, x.name) for x in self.series}) != len(self.series):
            raise ValueError("같은 확인 항목의 자료가 중복됐습니다")
        return self


def _reading_result(reading: Reading, execution: SyncReceipt, requirement, at, gap):
    """확인된 불일치와 자료 부족을 구분한다. 없음은 0이 아니다."""
    if isinstance(reading, RuntimeReading):
        if (reading.argo_sync == "Unknown" or reading.argo_health == "Unknown"
                or reading.operation_phase == "Unknown"):
            return "unknown"
        healthy = (reading.revision == reading.operation_revision == execution.merge_sha
            and reading.argo_sync == "Synced" and reading.argo_health == "Healthy"
            and reading.operation_phase == "Succeeded"
            and reading.generation == reading.observed_generation
            and reading.replicas == reading.ready_replicas == reading.pod_count == reading.ready_pods == 1
            and reading.pod_images == [execution.desired_digest])
        return "passed" if healthy else "failed"
    if isinstance(reading, HTTPReading):
        if reading.status_code is not None and reading.status_code != 200:
            return "failed"
        if reading.response_contract_valid is False:
            return "failed"
        return "passed" if reading.status_code == 200 and reading.response_contract_valid is True else "unknown"
    if isinstance(reading, MetricReading):
        if reading.value is None:
            return "unknown"
        passed = (reading.value <= requirement.threshold if requirement.comparison == "lte"
                  else reading.value >= requirement.threshold)
        return "passed" if passed else "failed"
    if (not reading.rule_present or reading.evaluation != "ok"
            or reading.evaluated_at is None or reading.active_count is None
            or not 0 <= (at - reading.evaluated_at).total_seconds() <= gap):
        return "unknown"
    return "passed" if reading.active_count == 0 else "failed"


def verify_recovery(request: VerificationInput) -> dict:
    """정해진 모든 확인 항목이 관찰 창에서 통과한 경우에만 해결로 판정한다."""
    execution, policy = request.execution, request.policy
    expected = [("runtime", "lab-api", None), *[("http", p, None) for p in policy.http_paths],
                *[("metric", x.name, x) for x in policy.metrics],
                *[("alert", x.name, x) for x in policy.alerts]]
    indexed = {(x.kind, x.name): x for x in request.series}
    checks = []
    for kind, name, requirement in expected:
        series = indexed.get((kind, name))
        reasons, outcomes, times, failed_readings = [], [], [], []
        if series is None or series.status != "available":
            reasons.append("evidence_missing" if series is None or series.status == "missing" else "collection_failed")
        elif series.binding != execution.binding:
            reasons.append("target_identity_mismatch")
        elif requirement is not None and series.definition_hash != requirement.definition_hash:
            reasons.append("query_or_rule_definition_mismatch")
        elif (execution.verified_at is None or policy.window_start is None
                or policy.window_end > request.evaluated_at
                or (request.evaluated_at-policy.window_end).total_seconds() > policy.maximum_report_delay_seconds
                or execution.verified_at > policy.window_start):
            reasons.append("observation_window_unavailable_or_invalid")
        else:
            for sample in series.samples:
                at = sample.observed_at
                if at is None or not policy.window_start <= at <= policy.window_end:
                    reasons.append("sample_time_unavailable_or_outside_window")
                    continue
                times.append(at)
                outcome = _reading_result(sample.reading, execution, requirement, at,
                                          policy.maximum_gap_seconds)
                outcomes.append(outcome)
                if outcome == "failed":
                    failed_readings.append(sample.model_dump(mode="json"))
            if len(times) < policy.minimum_samples:
                reasons.append("insufficient_samples")
            if times:
                gaps = [(b-a).total_seconds() for a, b in zip(times, times[1:])]
                if any(g <= 0 or g > policy.maximum_gap_seconds for g in gaps):
                    reasons.append("unordered_duplicate_or_gapped_samples")
                if ((times[-1]-times[0]).total_seconds() < policy.minimum_span_seconds
                        or (times[0]-policy.window_start).total_seconds() > policy.maximum_gap_seconds
                        or (policy.window_end-times[-1]).total_seconds() > policy.maximum_gap_seconds):
                    reasons.append("incomplete_observation_window")
            if "unknown" in outcomes:
                reasons.append("reading_unavailable_or_evaluation_failed")
        if "failed" in outcomes:
            state = "failed"
            reasons.append("recovery_condition_not_met")
        else:
            state = "unknown" if reasons or not outcomes else "passed"
        checks.append({"kind": kind, "name": name, "state": state,
                       "reasons": sorted(set(reasons)), "eligible_samples": len(times),
                       "failed_readings": failed_readings,
                       "requirement": requirement.model_dump(mode="json") if requirement else None,
                       "evidence_hash": sha256_json(series.model_dump(mode="json")) if series else None})
    extras = sorted(f"{k}:{n}" for k, n in indexed if (k, n) not in {(k, n) for k, n, _ in expected})
    failed = any(c["state"] == "failed" for c in checks)
    passed = all(c["state"] == "passed" for c in checks) and not extras
    status = "unresolved" if failed else "resolved" if passed else "inconclusive"
    return {"schema_version": 1, "record_type": "c04_recovery_verification",
            "status": status, "label": {"resolved": "해결", "unresolved": "미해결", "inconclusive": "검증불가"}[status],
            "service_resolved": status == "resolved", "proposal_id": execution.proposal_id,
            "binding": execution.binding.model_dump(mode="json"), "merge_sha": execution.merge_sha,
            "desired_digest": execution.desired_digest, "provenance": request.provenance,
            "evaluated_at": request.evaluated_at.isoformat(), "checks": checks,
            "observation_policy": policy.model_dump(mode="json"),
            "unexpected_series": extras, "input_hash": sha256_json(request.model_dump(mode="json")),
            "execution_hash": sha256_json(execution.model_dump(mode="json")),
            "policy_hash": sha256_json(policy.model_dump(mode="json")),
            "next_step": "report_result" if status == "resolved" else "stop_and_report",
            "execution_allowed": False, "external_model_calls": 0}


class FollowUpChange(StrictModel):
    """후속 후보도 실습 대상의 고정 GitOps 동기화로 한정한다."""

    binding: RecoveryBinding
    action: Literal["gitops_sync"] = "gitops_sync"
    revision: Revision
    digest: Digest


class FollowUpScope(StrictModel):
    """기존 승인 범위와의 로컬 비교 입력이며 승인 진위를 보증하지 않는다."""

    proposal_id: Annotated[StrictStr, Field(pattern=r"^cprop-[0-9a-f]{32}$")]
    proposal_hash: Sha256
    execution_hash: Sha256
    change: FollowUpChange
    issued_at: UTCDateTime
    expires_at: UTCDateTime
    revoked: StrictBool = False
    attempts_used: Annotated[StrictInt, Field(ge=0)] = 0
    maximum_attempts: Literal[1] = 1

    @model_validator(mode="after")
    def valid_period(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("승인 범위의 만료 시각은 발급 이후여야 합니다")
        return self


def plan_follow_up(request: VerificationInput, *, scope: FollowUpScope | None,
                   change: FollowUpChange | None, now: datetime) -> dict:
    """검증을 재계산하고 범위 안의 후보와 새 승인이 필요한 변경을 구분한다.

    실행 직전 원본 승인·잠금·대상 재검증을 대체하지 않는다. 이 함수는
    쓰기 adapter를 받지 않으며 모든 반환에서 실행 허용은 false다.
    """
    from ai_ops.contracts import _utc_datetime

    moment = _utc_datetime(now)
    report = verify_recovery(request)
    decision, reason = "stop_and_report", "verification_not_resolved"
    if report["status"] == "resolved":
        decision, reason = "report_result", "recovery_verified"
    elif not 0 <= (moment-request.evaluated_at).total_seconds() <= 120:
        reason = "verification_result_stale_or_in_future"
    elif change is not None:
        expected = FollowUpChange(binding=request.execution.binding,
                                  revision=request.execution.merge_sha,
                                  digest=request.execution.desired_digest)
        if (scope is None or change != expected or scope.change != expected
                or scope.proposal_id != request.execution.proposal_id
                or scope.proposal_hash != request.execution.proposal_hash
                or scope.execution_hash != report["execution_hash"]):
            decision, reason = "request_new_approval", "outside_existing_scope"
        elif scope.revoked or not scope.issued_at <= moment < scope.expires_at:
            decision, reason = "request_new_approval", "scope_expired_revoked_or_not_yet_valid"
        elif scope.attempts_used >= scope.maximum_attempts:
            reason = "follow_up_attempt_limit_reached"
        elif (report["status"] == "inconclusive"
                or any(x["state"] == "unknown" or set(x["reasons"])-{"recovery_condition_not_met"}
                       for x in report["checks"])):
            reason = "collect_missing_evidence_before_follow_up"
        else:
            decision, reason = "same_change_candidate", "fresh_execution_revalidation_required"
    return {"record_type": "c04_failure_handling", "verification_input_hash": report["input_hash"],
            "verification_status": report["status"], "decision": decision, "reason": reason,
            "scope_hash": sha256_json(scope.model_dump(mode="json")) if scope else None,
            "change_hash": sha256_json(change.model_dump(mode="json")) if change else None,
            "execution_allowed": False, "requires_execution_revalidation": decision == "same_change_candidate"}
