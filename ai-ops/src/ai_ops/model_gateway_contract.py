"""실습 worker에서 gateway로 보내는 정제 입력과 서비스 서명 계약이다."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Annotated, Literal

from pydantic import Field, StrictStr, model_validator

from ai_ops.contracts import Sha256, StrictModel, canonical_json_bytes, sha256_json
from ai_ops.model_ci_projection import CIModelView, CIProjection, parse_ci_projection
from ai_ops.model_projection import ModelView, Projection, parse_projection
from ai_ops.model_sequence_projection import (SequenceModelView, SequenceProjection,
                                              parse_sequence_projection)


MAX_SUBMISSION = 32768
CampaignID = Annotated[StrictStr, Field(pattern=r"^campaign-[0-9a-f]{32}$")]
RunID = Annotated[StrictStr, Field(pattern=r"^run-[0-9a-f]{32}$")]
UID = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9-]{1,80}$")]
ReportRef = Annotated[StrictStr, Field(pattern=r"^inc-[0-9a-f]{32}/run-[0-9a-f]{32}/report\.md$")]


class ModelSubmission(StrictModel):
    """원본 증거·대응표·CI artifact 없이 서버 장부에 필요한 결합값만 운반한다."""

    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    campaign_id: CampaignID
    source_run: RunID
    rules_ref: ReportRef
    deployment_uid: UID
    config_hash: Sha256
    cluster_fingerprint: Sha256
    exporter_sha256: Sha256
    source_bundle_hash: Sha256
    source_audit_hash: Sha256
    mapping_hash: Sha256
    view_hash: Sha256
    view: ModelView

    @model_validator(mode="after")
    def consistent(self):
        if (sha256_json(self.view.model_dump(mode="json")) != self.view_hash or
                self.rules_ref.split("/")[1] != self.source_run or
                self.view.target_alias != "service-1"):
            raise ValueError("gateway_submission_binding_rejected")
        return self

    @classmethod
    def from_projection(cls, campaign_id: str, projection: Projection, *, rules_ref: str):
        """worker의 검증된 Projection에서 원본 대응표를 제거한다."""
        projection = parse_projection(canonical_json_bytes(projection.model_dump(mode="json")))
        identity = projection.identity
        if rules_ref != f"{identity.incident_id}/{identity.run_id}/report.md":
            raise ValueError("gateway_submission_rules_binding_rejected")
        return cls(campaign_id=campaign_id, source_run=identity.run_id, rules_ref=rules_ref,
                   deployment_uid=identity.deployment_uid, config_hash=identity.config_hash,
                   cluster_fingerprint=identity.cluster_fingerprint,
                   exporter_sha256=identity.exporter_sha256,
                   source_bundle_hash=projection.source_bundle_hash,
                   source_audit_hash=projection.source_audit_hash,
                   mapping_hash=projection.mapping_hash, view_hash=projection.view_hash,
                   view=projection.view)


class CIModelSubmission(ModelSubmission):
    """v2 입력은 명시된 schema 버전과 별도 view 계약으로만 접수한다."""

    schema_version: Literal[2] = 2
    view: CIModelView

    @classmethod
    def from_projection(cls, campaign_id: str, projection: CIProjection, *, rules_ref: str):
        projection = parse_ci_projection(canonical_json_bytes(projection.model_dump(mode="json")))
        identity = projection.identity
        if rules_ref != f"{identity.incident_id}/{identity.run_id}/report.md":
            raise ValueError("gateway_submission_rules_binding_rejected")
        return cls(campaign_id=campaign_id, source_run=identity.run_id, rules_ref=rules_ref,
                   deployment_uid=identity.deployment_uid, config_hash=identity.config_hash,
                   cluster_fingerprint=identity.cluster_fingerprint,
                   exporter_sha256=identity.exporter_sha256,
                   source_bundle_hash=projection.source_bundle_hash,
                   source_audit_hash=projection.source_audit_hash,
                   mapping_hash=projection.mapping_hash, view_hash=projection.view_hash,
                   view=projection.view)


class SequenceModelSubmission(ModelSubmission):
    """검증된 조회 순서 v3만 명시적 버전으로 받아 원본 probe를 제외한다."""

    schema_version: Literal[3] = 3
    view: SequenceModelView

    @classmethod
    def from_projection(cls, campaign_id: str, projection: SequenceProjection, *, rules_ref: str):
        projection = parse_sequence_projection(canonical_json_bytes(projection.model_dump(mode="json")))
        base = projection.base
        identity = base.identity
        if rules_ref != f"{identity.incident_id}/{identity.run_id}/report.md":
            raise ValueError("gateway_submission_rules_binding_rejected")
        return cls(campaign_id=campaign_id, source_run=identity.run_id, rules_ref=rules_ref,
                   deployment_uid=identity.deployment_uid, config_hash=identity.config_hash,
                   cluster_fingerprint=identity.cluster_fingerprint,
                   exporter_sha256=identity.exporter_sha256,
                   source_bundle_hash=base.source_bundle_hash,
                   source_audit_hash=base.source_audit_hash,
                   mapping_hash=projection.mapping_hash, view_hash=projection.view_hash,
                   view=projection.view)


def parse_model_submission(raw: bytes) -> ModelSubmission | CIModelSubmission | SequenceModelSubmission:
    """유효한 서명 뒤에도 버전이 없거나 알 수 없으면 v1로 낮추지 않는다."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SUBMISSION:
        raise ValueError("gateway_submission_size_rejected")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("gateway_submission_json_rejected") from None
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise ValueError("gateway_submission_version_rejected")
    contract = {1: ModelSubmission, 2: CIModelSubmission, 3: SequenceModelSubmission}.get(value["schema_version"])
    if contract is None:
        raise ValueError("gateway_submission_version_rejected")
    submission = contract.model_validate(value)
    if canonical_json_bytes(submission.model_dump(mode="json")) != raw:
        raise ValueError("gateway_submission_noncanonical")
    return submission


def _credential(value: bytes) -> bytes:
    if not 32 <= len(value) <= 256 or any(byte < 33 or byte > 126 for byte in value):
        raise ValueError("gateway_service_credential_rejected")
    return value


def sign_submission(raw: bytes, credential: bytes) -> str:
    """요청의 바이트와 서비스 자격을 결합한다. 서명 대상은 정규 JSON뿐이다."""
    _credential(credential)
    if not 0 < len(raw) <= MAX_SUBMISSION:
        raise ValueError("gateway_submission_size_rejected")
    return hmac.new(credential, raw, hashlib.sha256).hexdigest()


def verify_submission(raw: bytes, authorization: str, signature: str,
                      credential: bytes) -> ModelSubmission | CIModelSubmission | SequenceModelSubmission:
    """인증·서명·엄격 계약을 확인하고 모호한 JSON 표기를 거부한다."""
    _credential(credential)
    if (not isinstance(raw, bytes) or not isinstance(authorization, str) or
            not isinstance(signature, str) or
            not authorization.startswith("Bearer ") or
            not hmac.compare_digest(authorization[7:].encode("ascii", errors="ignore"), credential) or
            not re.fullmatch(r"[0-9a-f]{64}", signature) or
            not hmac.compare_digest(sign_submission(raw, credential), signature)):
        raise ValueError("gateway_submission_auth_rejected")
    return parse_model_submission(raw)
