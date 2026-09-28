"""운영 조사 데이터 계약 v1. 앱 DB·Kubernetes·모델·네트워크 연결은 없다.

1. 공통 규칙: UTC 시간·ID 형식·내용 해시를 검사한다.
2. 조사 대상: 등록된 클러스터·서비스만 허용한다.
3. 조사 증거: 출처·시각·상태·내용을 기록하고 묶음 무결성을 확인한다.
4. 조사 요청: 질문과 대상·시간을 증거 묶음에 연결한다.
5. 조사 보고서: 사실·원인 후보·부족한 증거·미실행 제안을 구분한다.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


# 1. 공통 규칙: 시간을 UTC로 통일하고 ID·해시 형식을 제한한다.
def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)",
            value,
        ):
            raise ValueError("time must be UTC ISO 8601 with Z or +00:00")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("time must be a timezone-aware UTC datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError("time must use UTC offset +00:00")
    return value.astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, BeforeValidator(_utc_datetime)]
Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
IncidentId = Annotated[StrictStr, Field(pattern=r"^inc-[0-9a-f]{32}$")]
RunId = Annotated[StrictStr, Field(pattern=r"^run-[0-9a-f]{32}$")]
EvidenceId = Annotated[StrictStr, Field(pattern=r"^ev-[a-z0-9][a-z0-9._-]{0,63}$")]
RequestId = Annotated[StrictStr, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")]
K8sName = Annotated[
    StrictStr,
    Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", max_length=63),
]
ClusterId = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$", max_length=80)]
Summary = Annotated[StrictStr, Field(min_length=1, max_length=1000)]


def canonical_json_bytes(value: Any) -> bytes:
    """검증된 JSON 호환 데이터를 일정한 바이트 형식으로 인코딩한다."""
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class TimeWindow(StrictModel):
    start: UTCDateTime
    end: UTCDateTime

    @model_validator(mode="after")
    def check_order(self) -> TimeWindow:
        span = self.end - self.start
        if span <= timedelta(0) or span > timedelta(hours=24):
            raise ValueError("window must be positive and at most 24 hours")
        return self


# 클러스터·네임스페이스·워크로드·서비스를 하나의 조사 대상으로 묶는다.
# 2. 조사 대상: 등록부에 있는 정확한 대상만 요청에 사용한다.
class Target(StrictModel):
    cluster_id: ClusterId
    namespace: K8sName
    workload: K8sName
    service: K8sName
    environment: Literal["practice", "production"]

class TargetRegistry(StrictModel):
    schema_version: Literal[1]
    targets: list[Target] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_targets(self) -> TargetRegistry:
        identities = [
            (t.cluster_id, t.namespace, t.workload, t.service) for t in self.targets
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("target registry contains duplicate identities")
        return self

    def ensure_registered(self, target: Target) -> None:
        if target not in self.targets:
            raise ValueError("target is not registered")


def load_target_registry(path: Path) -> TargetRegistry:
    """신뢰할 수 있는 로컬 설정 파일을 읽는다. A1에서는 원격 등록부를 지원하지 않는다."""
    if path.stat().st_size > 32 * 1024:
        raise ValueError("target registry exceeds 32 KiB")
    return TargetRegistry.model_validate_json(path.read_bytes())


def parse_incident_request_json(raw: bytes) -> IncidentRequest:
    """신뢰할 수 없는 요청의 크기를 JSON 파싱과 모델 검증 전에 제한한다."""
    if not isinstance(raw, bytes) or len(raw) > 16 * 1024:
        raise ValueError("request must be bytes of at most 16 KiB")
    return IncidentRequest.model_validate_json(raw)


# 3. 조사 증거: 관측값과 누락을 구분하고 내용·묶음 해시를 검사한다.
class EvidenceProvenance(StrictModel):
    kind: Literal["captured", "synthetic"]
    origin: Annotated[StrictStr, Field(min_length=1, max_length=160)]
    fixture_version: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None = None

    @model_validator(mode="after")
    def check_fixture_version(self) -> EvidenceProvenance:
        if self.kind == "synthetic" and self.fixture_version is None:
            raise ValueError("synthetic evidence requires fixture_version")
        if self.kind == "captured" and self.fixture_version is not None:
            raise ValueError("captured evidence cannot have fixture_version")
        return self


class RedactionSummary(StrictModel):
    categories: list[Annotated[StrictStr, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list, max_length=20
    )
    count: StrictInt = Field(ge=0)


class EvidenceItem(StrictModel):
    schema_version: Literal[1]
    evidence_id: EvidenceId
    kind: Literal[
        "workload_status", "event", "log", "metric", "deployment", "config_summary", "runbook"
    ]
    source_ref: Annotated[StrictStr, Field(min_length=1, max_length=512)]
    observed_at: UTCDateTime | None
    collected_at: UTCDateTime
    target: Target
    time_range: TimeWindow
    status: Literal["available", "missing", "permission_denied", "stale", "invalid"]
    missing_reason: Annotated[StrictStr, Field(min_length=1, max_length=500)] | None
    provenance: EvidenceProvenance
    payload: dict[str, Any] | None
    content_hash: Sha256 | None
    redaction_summary: RedactionSummary

    @field_validator("source_ref")
    @classmethod
    def relative_source_ref(cls, value: str) -> str:
        # A2에서 묶음의 루트 경로를 기준으로 다시 확인하고 심볼릭 링크를 거부한다.
        path = PurePosixPath(value)
        if (
            "\\" in value
            or "\x00" in value
            or "://" in value
            or path.is_absolute()
            or any(part in ("..", ".") for part in value.split("/"))
        ):
            raise ValueError("source_ref must be a safe relative POSIX reference")
        return value

    @model_validator(mode="after")
    def check_content(self) -> EvidenceItem:
        if self.observed_at is not None and self.observed_at > self.collected_at:
            raise ValueError("observation cannot follow collection")
        if self.status == "available":
            if self.payload is None or self.content_hash is None or self.observed_at is None:
                raise ValueError("available evidence requires payload, hash and observation")
            if self.missing_reason is not None:
                raise ValueError("available evidence cannot have missing_reason")
            try:
                encoded = canonical_json_bytes(self.payload)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("payload must contain finite JSON data") from exc
            if len(encoded) > 64 * 1024:
                raise ValueError("evidence payload exceeds 64 KiB")
            if hashlib.sha256(encoded).hexdigest() != self.content_hash:
                raise ValueError("evidence payload hash mismatch")
        elif self.payload is not None or self.content_hash is not None:
            raise ValueError("unavailable evidence must not include payload or hash")
        elif self.missing_reason is None:
            raise ValueError("unavailable evidence requires missing_reason")
        return self


class EvidenceBundle(StrictModel):
    schema_version: Literal[1]
    target: Target
    window: TimeWindow
    created_at: UTCDateTime
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=256)
    bundle_hash: Sha256

    @model_validator(mode="after")
    def check_bundle(self) -> EvidenceBundle:
        ids = [item.evidence_id for item in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate evidence ID")
        if any(item.target != self.target for item in self.evidence):
            raise ValueError("evidence target does not match bundle target")
        value = self.model_dump(mode="json", exclude={"bundle_hash"})
        encoded = canonical_json_bytes(value)
        if len(encoded) > 4 * 1024 * 1024:
            raise ValueError("evidence bundle exceeds 4 MiB")
        if hashlib.sha256(encoded).hexdigest() != self.bundle_hash:
            raise ValueError("bundle hash mismatch")
        return self


def parse_evidence_bundle_json(raw: bytes) -> EvidenceBundle:
    """직렬화된 증거 묶음의 크기를 JSON 파싱 전에 제한한다."""
    if not isinstance(raw, bytes) or len(raw) > 4 * 1024 * 1024:
        raise ValueError("bundle must be bytes of at most 4 MiB")
    return EvidenceBundle.model_validate_json(raw)


def build_evidence_bundle(
    *, target: Target, window: TimeWindow, created_at: datetime, evidence: list[EvidenceItem]
) -> EvidenceBundle:
    value = {
        "schema_version": 1,
        "target": target.model_dump(mode="json"),
        "window": window.model_dump(mode="json"),
        "created_at": _utc_datetime(created_at).isoformat().replace("+00:00", "Z"),
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    value["bundle_hash"] = sha256_json(value)
    return EvidenceBundle.model_validate(value)


# 4. 조사 요청: 질문·대상·시간을 검증된 증거 묶음에 연결한다.
class IncidentRequest(StrictModel):
    schema_version: Literal[1]
    incident_id: IncidentId
    request_id: RequestId
    source: Literal["manual_replay"]
    target: Target
    question: Annotated[StrictStr, Field(min_length=1, max_length=1000)]
    window: TimeWindow
    bundle_hash: Sha256
    requested_at: UTCDateTime

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("question must contain visible text and no NUL")
        return value

    @model_validator(mode="after")
    def check_size(self) -> IncidentRequest:
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > 16 * 1024:
            raise ValueError("request exceeds 16 KiB")
        return self


def validate_request_bundle(
    request: IncidentRequest, bundle: EvidenceBundle, registry: TargetRegistry
) -> None:
    registry.ensure_registered(request.target)
    if request.target != bundle.target or request.window != bundle.window:
        raise ValueError("request target or window does not match evidence bundle")
    if request.bundle_hash != bundle.bundle_hash:
        raise ValueError("request bundle_hash does not match evidence bundle")


def create_incident_request(
    *, request_id: str, target: Target, question: str, window: TimeWindow,
    bundle: EvidenceBundle, registry: TargetRegistry, requested_at: datetime,
) -> IncidentRequest:
    request = IncidentRequest(
        schema_version=1,
        incident_id=f"inc-{uuid.uuid4().hex}",
        request_id=request_id,
        source="manual_replay",
        target=target,
        question=question,
        window=window,
        bundle_hash=bundle.bundle_hash,
        requested_at=requested_at,
    )
    validate_request_bundle(request, bundle, registry)
    return request


# 5. 조사 보고서: 관측 사실과 가설, 부족한 정보와 제안을 나눠 담는다.
class Fact(StrictModel):
    statement: Summary
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=20)


class Hypothesis(StrictModel):
    cause: Summary
    supporting_evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=20)
    contradicting_evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=20)


class MissingEvidence(StrictModel):
    source: Summary
    reason: Summary


class SuggestedAction(StrictModel):
    description: Summary
    execution_status: Literal["not_executed"]


class ExecutionMetadata(StrictModel):
    mode: Literal["rules", "ai"]
    engine: Annotated[StrictStr, Field(min_length=1, max_length=100)] | None = None
    model: Annotated[StrictStr, Field(min_length=1, max_length=100)] | None = None
    prompt_version: Annotated[StrictStr, Field(min_length=1, max_length=100)] | None = None
    tool_version: Annotated[StrictStr, Field(min_length=1, max_length=100)] | None = None
    runbook_versions: dict[str, StrictStr] = Field(default_factory=dict, max_length=50)
    duration_ms: StrictInt | None = Field(default=None, ge=0)
    tool_calls: StrictInt | None = Field(default=None, ge=0)
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    cost_usd: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


class AnalysisReport(StrictModel):
    schema_version: Literal[1]
    incident_id: IncidentId
    run_id: RunId
    input_hash: Sha256
    target: Target
    window: TimeWindow
    analysis_status: Literal["complete", "partial", "failed", "cancelled"]
    diagnosis_status: Literal[
        "suspected_cause", "no_incident", "insufficient_evidence"
    ]
    current_state: Literal["affected", "recovered", "healthy", "unknown"]
    facts: list[Fact] = Field(default_factory=list, max_length=100)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=3)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list, max_length=30)
    recommended_checks: list[Summary] = Field(default_factory=list, max_length=30)
    suggested_actions: list[SuggestedAction] = Field(default_factory=list, max_length=30)
    limitations: list[Summary] = Field(default_factory=list, max_length=30)
    execution: ExecutionMetadata

    @model_validator(mode="after")
    def check_diagnosis(self) -> AnalysisReport:
        if self.diagnosis_status == "suspected_cause" and not self.hypotheses:
            raise ValueError("suspected_cause requires a hypothesis")
        if self.diagnosis_status == "no_incident" and self.hypotheses:
            raise ValueError("no_incident cannot include hypotheses")
        return self
