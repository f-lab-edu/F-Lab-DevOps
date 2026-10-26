"""실습 조회 순서를 검증해 원본 식별자 없는 v3 모델 입력으로 만든다."""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from ai_ops.b_contracts import BSettings
from ai_ops.collection import CapturedSnapshot
from ai_ops.contracts import (Sha256, StrictModel, TimeWindow, UTCDateTime,
                              canonical_json_bytes, sha256_json)
from ai_ops.data_policy import validate_snapshot
from ai_ops.evidence import normalize_source_time
from ai_ops.model_projection import (Mapping, ModelView, Projection, UID, ViewID,
                                     ViewItem, project_snapshot)


SEQUENCE_POLICY = "captured-practice-projection-v3"
Outcome = Literal["hit", "miss", "error", "unavailable", "bypass", "consistency_primary"]
Count = Annotated[StrictInt, Field(ge=0, le=10**15)]
ItemID = Annotated[StrictInt, Field(ge=1, le=2**63-1)]
LOG = re.compile(r"cache_(miss|hit) endpoint=get_item item_id=([1-9][0-9]*)\Z")


def probe_log_hash(reads: list[ProbeRead]) -> str:
    """검증에 사용한 제한 로그 줄을 순서대로 묶는다."""
    return sha256_json([{"at": read.log_at.isoformat().replace("+00:00", "Z"),
                         "message": read.log_message} for read in reads])


class ProbeRead(StrictModel):
    """실습기·앱 로그의 원본 대조 자료다. 모델에 직접 보내지 않는다."""

    started_at: UTCDateTime
    log_at: UTCDateTime
    finished_at: UTCDateTime
    http_status: Annotated[StrictInt, Field(ge=200, le=200)]
    response_item_id: ItemID
    counters_before: dict[Outcome, Count]
    counters_after: dict[Outcome, Count]
    log_message: Annotated[StrictStr, Field(min_length=1, max_length=120)]

    @model_validator(mode="after")
    def consistent(self):
        if not self.started_at <= self.log_at <= self.finished_at:
            raise ValueError("probe log outside request")
        if (self.finished_at-self.started_at).total_seconds() > 15:
            raise ValueError("probe request too long")
        match = LOG.fullmatch(self.log_message)
        if match is None or int(match.group(2)) != self.response_item_id:
            raise ValueError("probe log item mismatch")
        expected = match.group(1)
        for outcome in set(self.counters_before) | set(self.counters_after):
            before = self.counters_before.get(outcome)
            after = self.counters_after.get(outcome)
            if outcome == expected:
                if after is None or after != (0 if before is None else before) + 1:
                    raise ValueError("probe expected counter mismatch")
            elif before != after:
                raise ValueError("probe concurrent counter change")
        if expected not in self.counters_after:
            raise ValueError("probe expected counter missing")
        return self

    @property
    def outcome(self) -> str:
        return LOG.fullmatch(self.log_message).group(1)


class ProbeRecord(StrictModel):
    """출처 해시·소유 대상을 묶은 비공개 기록이며 타입만으로 출처를 인증하지 않는다."""

    schema_version: Literal[1] = 1
    source_kind: Literal["isolated_lab_probe"] = "isolated_lab_probe"
    source_bundle_hash: Sha256
    source_audit_hash: Sha256
    config_hash: Sha256
    deployment_uid: UID
    pod_uid: UID
    image_reference_hash: Sha256
    window: TimeWindow
    recorded_at: UTCDateTime
    item_id: ItemID
    reads: list[ProbeRead] = Field(min_length=2, max_length=4)
    log_source_hash: Sha256
    raw_log_sha256: Sha256

    @model_validator(mode="after")
    def consistent(self):
        outcomes = [read.outcome for read in self.reads]
        if outcomes not in (["miss", "hit"], ["miss", "hit", "miss", "hit"]):
            raise ValueError("probe sequence rejected")
        if any(read.response_item_id != self.item_id or
               not self.window.start <= read.started_at <= read.log_at <= read.finished_at <= self.window.end
               for read in self.reads):
            raise ValueError("probe item or time mismatch")
        if any(a.finished_at >= b.started_at for a, b in zip(self.reads, self.reads[1:])):
            raise ValueError("probe requests overlap")
        if any(a.counters_after != b.counters_before for a, b in zip(self.reads, self.reads[1:])):
            raise ValueError("probe counter changes between reads")
        if self.recorded_at < self.reads[-1].finished_at:
            raise ValueError("probe recorded before reads")
        if self.log_source_hash != probe_log_hash(self.reads):
            raise ValueError("probe log hash mismatch")
        return self


def verify_probe_logs(raw: bytes, probe: ProbeRecord, *, pod_uid: str, truncated: bool) -> None:
    """같은 Pod의 제한 로그에서 해당 아이템의 줄을 빠짐없이 순서 대조한다."""
    if (type(raw) is not bytes or len(raw) > 64*1024 or truncated is not False or
            pod_uid != probe.pod_uid or hashlib.sha256(raw).hexdigest() != probe.raw_log_sha256):
        raise ValueError("probe log source rejected")
    text = raw.decode("utf-8", errors="strict")
    lines = text.splitlines()
    if len(lines) > 256:
        raise ValueError("probe log line limit")
    selected = []
    prefix = "INFO:app.api.routes.item:"
    owned = re.compile(r"item_id=" + str(probe.item_id) + r"(?:\D|$)")
    for line in lines:
        timestamp, separator, message = line.partition(" ")
        if "cache_" not in line or not owned.search(line):
            continue
        if not separator or not message.startswith(prefix):
            raise ValueError("probe log format rejected")
        message = message[len(prefix):]
        match = LOG.fullmatch(message)
        if match is None or int(match.group(2)) != probe.item_id:
            raise ValueError("probe log outcome rejected")
        at, _ = normalize_source_time(timestamp)
        if not probe.window.start <= at <= probe.window.end:
            raise ValueError("probe log outside window")
        selected.append((at, message))
    if selected != [(read.log_at, read.log_message) for read in probe.reads]:
        raise ValueError("probe logs missing duplicated or reordered")


class SequenceRead(StrictModel):
    order: Annotated[StrictInt, Field(ge=1, le=4)]
    cache_outcome: Literal["miss", "hit"]
    http_status: Annotated[StrictInt, Field(ge=200, le=200)]


class SequenceSummary(StrictModel):
    probe_alias: Literal["probe-1"] = "probe-1"
    same_item: StrictBool = True
    reads: list[SequenceRead] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def consistent(self):
        if (self.same_item is not True or
                [read.order for read in self.reads] != list(range(1, len(self.reads)+1)) or
                [read.cache_outcome for read in self.reads] not in
                (["miss", "hit"], ["miss", "hit", "miss", "hit"])):
            raise ValueError("sequence summary rejected")
        return self


class SequenceViewItem(StrictModel):
    evidence_id: ViewID
    kind: Literal["cache_probe_sequence"] = "cache_probe_sequence"
    source_status: Literal["available"] = "available"
    reason: Literal["collected"] = "collected"
    observed_at: UTCDateTime
    collected_at: UTCDateTime
    payload: SequenceSummary

    @model_validator(mode="after")
    def consistent(self):
        if self.observed_at > self.collected_at:
            raise ValueError("sequence collected before observation")
        return self


class SequenceModelView(ModelView):
    schema_version: Literal[3] = 3
    policy_version: Literal["captured-practice-projection-v3"] = SEQUENCE_POLICY
    items: list[ViewItem | SequenceViewItem] = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def one_sequence(self):
        if (len([item for item in self.items if isinstance(item, SequenceViewItem)]) != 1 or
                not isinstance(self.items[-1], SequenceViewItem)):
            raise ValueError("sequence evidence missing or duplicated")
        return self


def _view(base: Projection, probe: ProbeRecord) -> SequenceModelView:
    if len(base.view.items) >= 64:
        raise ValueError("sequence view item limit")
    summary = SequenceSummary(reads=[SequenceRead(order=n, cache_outcome=read.outcome,
        http_status=read.http_status) for n, read in enumerate(probe.reads, 1)])
    item = SequenceViewItem(evidence_id=f"ev-view-{len(base.view.items)+1:03d}",
        observed_at=probe.reads[-1].log_at,
        collected_at=max(base.view.collected_at, probe.recorded_at), payload=summary)
    data = base.view.model_dump(mode="json")
    data.update(schema_version=3, policy_version=SEQUENCE_POLICY,
                collected_at=item.collected_at.isoformat().replace("+00:00", "Z"),
                items=[*data["items"], item.model_dump(mode="json")])
    return SequenceModelView.model_validate(data)


class SequenceProjection(StrictModel):
    """v1 입력과 별도 probe를 결합하고 재파싱 때도 같은 view를 재생성한다."""

    base: Projection
    probe: ProbeRecord
    probe_hash: Sha256
    view: SequenceModelView
    view_hash: Sha256
    mapping: list[Mapping] = Field(min_length=2, max_length=64)
    mapping_hash: Sha256

    @model_validator(mode="after")
    def consistent(self):
        probe_hash = sha256_json(self.probe.model_dump(mode="json"))
        if (self.probe.source_bundle_hash != self.base.source_bundle_hash or
                self.probe.source_audit_hash != self.base.source_audit_hash or
                self.probe.config_hash != self.base.identity.config_hash or
                self.probe.deployment_uid != self.base.identity.deployment_uid or
                self.probe.window != self.base.view.window or
                self.probe.recorded_at > self.base.view.collected_at or
                self.probe_hash != probe_hash):
            raise ValueError("sequence source binding rejected")
        if (self.view != _view(self.base, self.probe) or
                self.view_hash != sha256_json(self.view.model_dump(mode="json")) or
                self.mapping != [*self.base.mapping, Mapping(view_id=self.view.items[-1].evidence_id,
                    source_id="ev-probe-sequence", source_content_hash=probe_hash)] or
                self.mapping_hash != sha256_json([m.model_dump(mode="json") for m in self.mapping])):
            raise ValueError("sequence projection binding rejected")
        return self


def project_probe(base: Projection, probe: ProbeRecord, snapshot: CapturedSnapshot,
                  settings: BSettings, *, raw_logs: bytes, log_pod_uid: str,
                  logs_truncated: bool) -> SequenceProjection:
    """검증된 snapshot의 Pod 소유·이미지와 비공개 probe를 대조한 뒤 view를 만든다."""
    base = Projection.model_validate_json(base.model_dump_json())
    probe = ProbeRecord.model_validate_json(probe.model_dump_json())
    snapshot = validate_snapshot(snapshot, settings)
    verify_probe_logs(raw_logs, probe, pod_uid=log_pod_uid, truncated=logs_truncated)
    if (project_snapshot(snapshot, settings, base.identity) != base or
            snapshot.bundle.bundle_hash != base.source_bundle_hash or
            sha256_json(snapshot.audit.model_dump(mode="json")) != base.source_audit_hash or
            snapshot.bundle.window != probe.window):
        raise ValueError("probe snapshot mismatch")
    # Pod status.image는 같은 digest라도 런타임 태그로 표시될 수 있다.
    # 소유 Deployment의 저장된 원하는 이미지를 고정 digest와 대조한다.
    deployments = [entry for entry in snapshot.audit.entries if
        entry.resource_uid == probe.deployment_uid and entry.status == "available" and
        any(item.evidence_id == entry.evidence_id and item.kind == "deployment" and
            item.status == "available" and item.payload is not None and
            isinstance(item.payload.get("image"), str) and
            sha256_json(item.payload["image"]) == probe.image_reference_hash
            for item in snapshot.bundle.evidence)]
    if len(deployments) != 1:
        raise ValueError("probe deployment image rejected")
    owned = [entry for entry in snapshot.audit.entries if
             entry.resource_uid == probe.pod_uid and
             entry.deployment_uid == base.identity.deployment_uid and
             entry.status == "available" and
             any(item.evidence_id == entry.evidence_id and item.kind == "workload_status" and
                 item.status == "available" and item.payload is not None
                 for item in snapshot.bundle.evidence)]
    if len(owned) != 1:
        raise ValueError("probe pod or image ownership rejected")
    view = _view(base, probe)
    probe_hash = sha256_json(probe.model_dump(mode="json"))
    mapping = [*base.mapping, Mapping(view_id=view.items[-1].evidence_id,
               source_id="ev-probe-sequence", source_content_hash=probe_hash)]
    result = SequenceProjection(base=base, probe=probe, probe_hash=probe_hash, view=view,
        view_hash=sha256_json(view.model_dump(mode="json")), mapping=mapping,
        mapping_hash=sha256_json([m.model_dump(mode="json") for m in mapping]))
    return parse_sequence_projection(canonical_json_bytes(result.model_dump(mode="json")))


def parse_sequence_projection(raw: bytes) -> SequenceProjection:
    """비공개 결합 자료의 크기와 모든 출처·view 해시를 재검사한다."""
    if len(raw) > 256 * 1024:
        raise ValueError("sequence projection too large")
    return SequenceProjection.model_validate_json(raw)
