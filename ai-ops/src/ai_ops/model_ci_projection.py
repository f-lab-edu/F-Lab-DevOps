"""검증된 S10 CI 두 단계만 v2 모델 입력으로 승격하는 오프라인 계약이다."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from ai_ops.b_contracts import BSettings
from ai_ops.ci_handoff import check_snapshot, pack_history
from ai_ops.collection import CapturedSnapshot
from ai_ops.contracts import StrictModel, UTCDateTime, canonical_json_bytes, sha256_json
from ai_ops.lab_ci_history import VerifiedLabHistory
from ai_ops.model_projection import (Excluded, Mapping, ModelView, Projection,
    SourceIdentity, ViewID, ViewItem, project_snapshot)


CI_POLICY = "captured-practice-projection-v2"


class CIStageSummary(StrictModel):
    """원본 이미지·실행 ID를 포함하지 않는 고정된 실패·원복 상태다."""

    stage: Literal["failed_deployment", "rollback_verification"]
    verification_result: Literal["failure", "success"]
    revision_role: Literal["candidate", "baseline"]
    ready_replicas: Literal[0, 1]
    image_pull_failure_observed: StrictBool
    user_path_recovery_verified: Literal[False]

    @model_validator(mode="after")
    def consistent(self):
        if type(self.ready_replicas) is not int:
            raise ValueError("CI ready replica type rejected")
        expected = (("failure", "candidate", 0, True) if self.stage == "failed_deployment"
                    else ("success", "baseline", 1, False))
        if (self.verification_result, self.revision_role, self.ready_replicas,
                self.image_pull_failure_observed) != expected or self.user_path_recovery_verified is not False:
            raise ValueError("CI stage summary conflict")
        return self


class CIViewItem(StrictModel):
    evidence_id: ViewID
    kind: Literal["ci_deployment_state"]
    source_status: Literal["available"]
    reason: Literal["collected"]
    observed_at: UTCDateTime
    collected_at: UTCDateTime
    payload: CIStageSummary

    @model_validator(mode="after")
    def consistent(self):
        if self.observed_at > self.collected_at:
            raise ValueError("CI observation after collection")
        return self


class CIExcluded(Excluded):
    reason: Literal["excluded_by_model_policy", "ci_pair_unverified"] = "excluded_by_model_policy"


class CIModelView(ModelView):
    schema_version: Literal[2] = 2
    policy_version: Literal["captured-practice-projection-v2"] = CI_POLICY
    items: list[ViewItem | CIViewItem] = Field(min_length=1, max_length=64)
    excluded: list[CIExcluded] = Field(max_length=15)

    @model_validator(mode="after")
    def ci_pair(self):
        stages = [item for item in self.items if isinstance(item, CIViewItem)]
        if stages:
            if (len(stages) != 2 or [item.payload.stage for item in stages] !=
                    ["failed_deployment", "rollback_verification"] or
                    stages[0].observed_at > stages[1].observed_at):
                raise ValueError("CI view requires ordered failure and rollback pair")
        return self


class CIProjection(Projection):
    view: CIModelView


def parse_ci_projection(raw: bytes) -> CIProjection:
    """정제 결과의 크기·버전·해시·대응표를 한 번 더 확인한다."""
    if len(raw) > 256 * 1024:
        raise ValueError("CI projection too large")
    return CIProjection.model_validate_json(raw)


def project_ci_snapshot(snapshot: CapturedSnapshot, settings: BSettings,
                        identity: SourceIdentity, history: VerifiedLabHistory) -> CIProjection:
    """검증된 이력과 저장 snapshot을 대조한 후 두 자료가 모두 유효할 때만 포함한다.

    호출자는 GitHub 검증·큐 HMAC을 통과한 VerifiedLabHistory를 제공해야 한다.
    Python 객체 타입 자체는 외부 입력에 대한 인증 수단이 아니다.
    """
    if type(history) is not VerifiedLabHistory:
        raise ValueError("verified CI history required")
    # 전달 계약도 재검사해 임의로 생성한 메모리 객체의 실행·revision·시각 불일치를 막는다.
    pack_history(history)
    check_snapshot(snapshot, history)
    failed, restored, _ = history.parsed()
    if identity.deployment_uid != restored.observed.deployment_uid:
        raise ValueError("CI projection deployment identity mismatch")
    base = project_snapshot(snapshot, settings, identity)
    sources = {item.evidence_id: item for item in snapshot.bundle.evidence}
    ci_entries = {entry.ci_stage: entry for entry in snapshot.audit.entries if entry.ci_stage}
    if set(ci_entries) != {"deployment", "rollback"}:
        raise ValueError("CI projection pair audit missing")
    pair = [(record, ci_entries[record.stage], sources[ci_entries[record.stage].evidence_id])
            for record in (failed, restored)]
    if any(entry.evidence_id != "ev-deployment-ci-" + record.stage or
           item.source_ref != "deployment/deployment-ci-" + record.stage + ".json"
           for record, entry, item in pair):
        raise ValueError("CI projection evidence source mismatch")
    eligible = all(
        item.status == "available" and entry.status == "available" and entry.reason == "collected"
        and entry.time_field == "artifact_observed_at" and item.content_hash is not None
        and item.observed_at == record.observed_at
        and snapshot.bundle.window.start <= record.observed_at <= snapshot.bundle.window.end
        for record, entry, item in pair
    )
    included_ids = {item.evidence_id for _, _, item in pair} if eligible else set()
    items: list[ViewItem | CIViewItem] = list(base.view.items)
    mapping = list(base.mapping)
    if eligible:
        for record, entry, item in pair:
            stage = "failed_deployment" if record.stage == "deployment" else "rollback_verification"
            summary = CIStageSummary(stage=stage, verification_result=record.verification_result,
                revision_role="candidate" if record.stage == "deployment" else "baseline",
                ready_replicas=record.observed.ready_replicas,
                image_pull_failure_observed=bool(record.observed.image_pull_reasons),
                user_path_recovery_verified=record.user_path_recovery_verified)
            view_id = f"ev-view-{len(items) + 1:03d}"
            items.append(CIViewItem(evidence_id=view_id, kind="ci_deployment_state",
                source_status="available", reason="collected", observed_at=item.observed_at,
                collected_at=item.collected_at, payload=summary))
            mapping.append(Mapping(view_id=view_id, source_id=item.evidence_id,
                                   source_content_hash=item.content_hash))
    ci_ids = {item.evidence_id for _, _, item in pair}
    excluded_counts = Counter()
    for source in snapshot.bundle.evidence:
        if source.kind in {"workload_status", "config_summary", "metric"} or source.evidence_id in included_ids:
            continue
        reason = ("ci_pair_unverified" if source.evidence_id in ci_ids else
                  "excluded_by_model_policy")
        excluded_counts[(source.kind, source.status, reason)] += 1
    view = CIModelView(window=base.view.window, collected_at=base.view.collected_at,
        collection_status=base.view.collection_status, deadline_exceeded=base.view.deadline_exceeded,
        expected_pods=base.view.expected_pods, observed_pods=base.view.observed_pods,
        items=items, excluded=[CIExcluded(kind=kind, source_status=status, count=count, reason=reason)
            for (kind, status, reason), count in sorted(excluded_counts.items())])
    projection = CIProjection(view=view, identity=base.identity,
        source_bundle_hash=base.source_bundle_hash, source_audit_hash=base.source_audit_hash,
        mapping=mapping, source_count=base.source_count,
        view_hash=sha256_json(view.model_dump(mode="json")),
        mapping_hash=sha256_json([entry.model_dump(mode="json") for entry in mapping]))
    # 직렬화 뒤에도 단일 pair·strict payload 계약이 유지되는지 확인한다.
    return parse_ci_projection(canonical_json_bytes(projection.model_dump(mode="json")))
