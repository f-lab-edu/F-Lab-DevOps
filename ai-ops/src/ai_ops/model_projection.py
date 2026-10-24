"""실습 snapshot에서 모델 반출 허용 필드만 선별하는 독립 계약이다."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from ai_ops.b_contracts import BSettings
from ai_ops.collection import CapturedSnapshot, CollectionEntry
from ai_ops.contracts import (Sha256, StrictModel, TimeWindow, UTCDateTime,
                              canonical_json_bytes, sha256_json)
from ai_ops.data_policy import validate_snapshot
from ai_ops.evidence import QUERY_OUTCOMES


POLICY = "captured-practice-projection-v1"
TARGET = dict(cluster_id="urlshortener", namespace="ai-ops-lab", workload="lab-api",
              service="lab-api", environment="practice")
Reason = CollectionEntry.model_fields["reason"].annotation
Status = Literal["available", "missing", "permission_denied", "stale", "invalid"]
ViewID = Annotated[StrictStr, Field(pattern=r"^ev-view-[0-9]{3}$")]
UID = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9-]{1,80}$")]
Number = Annotated[float, Field(ge=0, le=10**15, allow_inf_nan=False)]


class Workload(StrictModel):
    pod_alias: Annotated[StrictStr, Field(pattern=r"^pod-[1-9][0-9]*$")]
    phase: Literal["Pending", "Running", "Succeeded", "Failed", "Unknown"]
    ready: StrictBool
    restarts: Annotated[StrictInt, Field(ge=0)]


class Config(StrictModel):
    cache_intended_enabled: StrictBool | None
    redis_url_configured: StrictBool | None
    read_engine_role: Literal["primary", "replica", "unknown"]


class Sample(StrictModel):
    at: UTCDateTime
    outcome: Literal["hit", "miss", "error", "unavailable", "bypass", "consistency_primary",
                     "ratio", "requests", "p95", "p99", "count"]
    value: Number


class Metric(StrictModel):
    query_id: Literal["cache_outcomes", "cache_hit_ratio", "http_error_ratio", "api_latency", "db_latency"]
    unit: Literal["count", "ratio", "seconds"]
    dimension: Literal["route=/items", "route=/items/{item_id}", "route=/items/_db",
                       "operation=select_one", "operation=select_all", "operation=insert", "operation=delete"] | None
    samples: list[Sample] = Field(min_length=1, max_length=6)
    missing_outcomes: list[StrictStr] = Field(max_length=6)

    @model_validator(mode="after")
    def consistent(self):
        expected = QUERY_OUTCOMES[self.query_id]
        got = [s.outcome for s in self.samples]
        unit = "seconds" if self.query_id.endswith("latency") else "count" if self.query_id == "cache_outcomes" else "ratio"
        if (self.unit != unit or len(set(got)) != len(got) or not set(got) <= expected
                or set(self.missing_outcomes) != expected - set(got)
                or len(set(self.missing_outcomes)) != len(self.missing_outcomes)):
            raise ValueError("projection metric rejected")
        if self.query_id != "cache_outcomes" and (set(got) != expected or
                not any(s.outcome in {"requests", "count"} and s.value > 0 for s in self.samples)):
            raise ValueError("projection traffic missing")
        if any(s.outcome == "ratio" and s.value > 1 for s in self.samples):
            raise ValueError("projection ratio rejected")
        prefix = "route=" if self.query_id == "api_latency" else "operation=" if self.query_id == "db_latency" else None
        if self.dimension is not None and (prefix is None or not self.dimension.startswith(prefix)):
            raise ValueError("projection dimension rejected")
        return self


class ViewItem(StrictModel):
    evidence_id: ViewID
    kind: Literal["workload_status", "config_summary", "metric"]
    source_status: Status
    reason: Reason
    observed_at: UTCDateTime | None
    collected_at: UTCDateTime
    payload: Workload | Config | Metric | None

    @model_validator(mode="after")
    def consistent(self):
        payload_type = {"workload_status": Workload, "config_summary": Config, "metric": Metric}[self.kind]
        if self.source_status == "available":
            if not isinstance(self.payload, payload_type) or self.reason != "collected" or self.observed_at is None:
                raise ValueError("projection availability rejected")
        elif self.payload is not None or self.reason == "collected":
            raise ValueError("projection unavailable payload rejected")
        if self.observed_at is not None and self.observed_at > self.collected_at:
            raise ValueError("projection timestamp rejected")
        return self


class Excluded(StrictModel):
    kind: Literal["log", "event", "deployment"]
    source_status: Status
    count: Annotated[StrictInt, Field(ge=1, le=256)]
    reason: Literal["excluded_by_model_policy"] = "excluded_by_model_policy"


class ModelView(StrictModel):
    schema_version: Literal[1] = 1
    policy_version: Literal["captured-practice-projection-v1"] = POLICY
    origin_kind: Literal["captured_projection"] = "captured_projection"
    target_alias: Literal["service-1"] = "service-1"
    cluster_alias: Literal["cluster-1"] = "cluster-1"
    window: TimeWindow
    collected_at: UTCDateTime
    collection_status: Literal["complete", "partial", "unavailable"]
    deadline_exceeded: StrictBool
    expected_pods: Annotated[StrictInt, Field(ge=0)] | None
    observed_pods: Annotated[StrictInt, Field(ge=0)]
    scrape_quality: Literal["not_retained_in_source_snapshot"] = "not_retained_in_source_snapshot"
    items: list[ViewItem] = Field(min_length=1, max_length=64)
    excluded: list[Excluded] = Field(max_length=15)

    @model_validator(mode="after")
    def consistent(self):
        if len({i.evidence_id for i in self.items}) != len(self.items):
            raise ValueError("duplicate view evidence")
        pods = [i.payload.pod_alias for i in self.items if isinstance(i.payload, Workload)]
        if len(set(pods)) != len(pods) or len(pods) != self.observed_pods:
            raise ValueError("projection pod count rejected")
        if len({(x.kind, x.source_status) for x in self.excluded}) != len(self.excluded):
            raise ValueError("projection exclusion rejected")
        source_count = len(self.items) + sum(x.count for x in self.excluded)
        available = sum(x.source_status == "available" for x in self.items) + sum(
            x.count for x in self.excluded if x.source_status == "available")
        expected = "unavailable" if available == 0 else "complete" if available == source_count and not self.deadline_exceeded else "partial"
        if self.collection_status != expected:
            raise ValueError("projection collection status rejected")
        if self.window.end > self.collected_at:
            raise ValueError("projection window rejected")
        for item in self.items:
            if item.collected_at > self.collected_at or (item.observed_at is not None and
                    not self.window.start <= item.observed_at <= self.window.end):
                raise ValueError("projection time outside window")
            if isinstance(item.payload, Metric) and any(not self.window.start <= s.at <= self.window.end
                                                       for s in item.payload.samples):
                raise ValueError("projection sample outside window")
        return self


class SourceIdentity(StrictModel):
    cluster_fingerprint: Sha256
    deployment_uid: UID
    config_hash: Sha256
    incident_id: Annotated[StrictStr, Field(pattern=r"^inc-[0-9a-f]{32}$")]
    run_id: Annotated[StrictStr, Field(pattern=r"^run-[0-9a-f]{32}$")]
    exporter_sha256: Sha256


class Mapping(StrictModel):
    view_id: ViewID
    source_id: Annotated[StrictStr, Field(pattern=r"^ev-[a-z0-9][a-z0-9._-]{0,63}$")]
    source_content_hash: Sha256 | None


class Projection(StrictModel):
    view: ModelView
    identity: SourceIdentity
    source_bundle_hash: Sha256
    source_audit_hash: Sha256
    mapping: list[Mapping] = Field(min_length=1, max_length=64)
    source_count: Annotated[StrictInt, Field(ge=1, le=256)]
    view_hash: Sha256
    mapping_hash: Sha256

    @model_validator(mode="after")
    def consistent(self):
        if (sha256_json(self.view.model_dump(mode="json")) != self.view_hash
                or sha256_json([m.model_dump(mode="json") for m in self.mapping]) != self.mapping_hash
                or [m.view_id for m in self.mapping] != [i.evidence_id for i in self.view.items]
                or len({m.source_id for m in self.mapping}) != len(self.mapping)
                or self.source_count != len(self.view.items) + sum(x.count for x in self.view.excluded)):
            raise ValueError("projection binding rejected")
        for item, mapping in zip(self.view.items, self.mapping):
            if (item.source_status == "available") != (mapping.source_content_hash is not None):
                raise ValueError("projection source hash rejected")
        return self


def parse_projection(raw: bytes) -> Projection:
    """반출 응답은 파싱 전 크기를 제한하고 모든 필드를 다시 검사한다."""
    if len(raw) > 256 * 1024:
        raise ValueError("projection too large")
    return Projection.model_validate_json(raw)


def project_snapshot(snapshot: CapturedSnapshot, settings: BSettings, identity: SourceIdentity) -> Projection:
    """원본 출처를 유지하며 감사·대상·소유 관계를 확인한 필드만 새 객체에 복사한다."""
    snapshot = validate_snapshot(snapshot, settings)
    if (snapshot.bundle.target.model_dump() != TARGET or settings.config_hash != identity.config_hash
            or settings.runtime.llm_enabled is not False):
        raise ValueError("projection target rejected")
    audit = snapshot.audit
    entries = {x.evidence_id: x for x in audit.entries}
    # 재설치 전 자료와 현재 Pod·Deployment를 같은 것으로 취급하지 않는다.
    deployment = [x for x in audit.entries if x.evidence_id in
                  {i.evidence_id for i in snapshot.bundle.evidence if i.kind == "deployment"}
                  and x.resource_uid == identity.deployment_uid and x.time_field == "response_received_at"]
    if not deployment:
        raise ValueError("projection deployment identity missing")
    items, mapping, excluded = [], [], Counter()
    pod_count = 0
    seen_pods = set()
    pod_owners = {x.resource_uid for x in audit.entries if x.deployment_uid == identity.deployment_uid
                  and x.resource_uid and any(i.evidence_id == x.evidence_id and i.kind == "workload_status"
                                            for i in snapshot.bundle.evidence)}
    for source in snapshot.bundle.evidence:
        entry = entries[source.evidence_id]
        if source.kind not in {"workload_status", "config_summary", "metric"}:
            excluded[(source.kind, source.status)] += 1
            continue
        payload = None
        if source.status == "available":
            value = source.payload
            if source.kind == "workload_status" and entry.deployment_uid != identity.deployment_uid:
                raise ValueError("projection owner rejected")
            if source.kind == "workload_status":
                if not entry.resource_uid:
                    raise ValueError("projection pod identity missing")
                if entry.resource_uid in seen_pods:
                    raise ValueError("projection duplicate pod rejected")
                seen_pods.add(entry.resource_uid)
                pod_count += 1
                payload = Workload(pod_alias=f"pod-{pod_count}", phase=value["phase"],
                                   ready=value["ready"], restarts=value["restarts"])
            elif source.kind == "config_summary":
                if entry.resource_uid not in pod_owners:
                    raise ValueError("projection config owner rejected")
                payload = Config(cache_intended_enabled=value.get("cache_intended_enabled"),
                                 redis_url_configured=value.get("redis_url_configured"),
                                 read_engine_role=value.get("read_engine_role", "unknown"))
            else:
                payload = Metric(query_id=value["query_id"], unit=value["unit"], dimension=entry.dimension,
                    samples=[Sample(at=s["at"], outcome=s["outcome"], value=s["value"]) for s in value["samples"]],
                    missing_outcomes=sorted(QUERY_OUTCOMES[value["query_id"]] - {s["outcome"] for s in value["samples"]}))
        view_id = f"ev-view-{len(items)+1:03d}"
        items.append(ViewItem(evidence_id=view_id, kind=source.kind, source_status=source.status,
                     reason=entry.reason, observed_at=source.observed_at, collected_at=source.collected_at, payload=payload))
        mapping.append(Mapping(view_id=view_id, source_id=source.evidence_id, source_content_hash=source.content_hash))
    view = ModelView(window=snapshot.bundle.window, collected_at=snapshot.bundle.created_at,
        collection_status=audit.collection_status, deadline_exceeded=audit.deadline_exceeded,
        expected_pods=audit.workload_expected_pods, observed_pods=pod_count, items=items,
        excluded=[Excluded(kind=k, source_status=s, count=n) for (k, s), n in sorted(excluded.items())])
    return Projection(view=view, identity=identity, source_bundle_hash=snapshot.bundle.bundle_hash,
        source_audit_hash=sha256_json(audit.model_dump(mode="json")), mapping=mapping,
        source_count=len(snapshot.bundle.evidence), view_hash=sha256_json(view.model_dump(mode="json")),
        mapping_hash=sha256_json([x.model_dump(mode="json") for x in mapping]))
