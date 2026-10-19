"""제한된 읽기 응답과 captured 후보를 불변 해시의 A 증거 묶음으로 고정한다.

원시 응답·예외 원문은 이 객체의 증거나 감사 기록에 보관하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from ai_ops.b_contracts import BSettings, Clock, ReadRequest, ReadResponse, ReadTransport, SystemClock
from ai_ops.contracts import (
    EvidenceBundle, EvidenceId, EvidenceItem, Sha256, StrictModel, Target, TimeWindow,
    UTCDateTime, _utc_datetime, build_evidence_bundle, canonical_json_bytes, sha256_json,
)
from ai_ops.evidence import _safe_parts, normalize_captured_payload, normalize_source_time


class CapturedSource(StrictModel):
    evidence_id: EvidenceId
    kind: Literal["workload_status", "event", "log", "metric", "deployment", "config_summary"]
    source_ref: StrictStr = Field(min_length=1, max_length=512)
    target: Target
    time_field: Literal["response_received_at", "eventTime", "lastTimestamp", "log_timestamp", "evaluation_time", "artifact_observed_at", "status_reconciled_at", "history_deployed_at"]


class CollectionEntry(StrictModel):
    evidence_id: EvidenceId
    status: Literal["available", "missing", "permission_denied", "stale", "invalid"]
    reason: Literal[
        "collected", "permission_denied", "not_found", "timeout", "upstream_error",
        "response_too_large", "raw_budget_exceeded", "log_budget_exceeded",
        "deadline_exceeded", "invalid_payload", "invalid_time", "outside_window",
        "future_observation", "payload_too_large",
        "scope_mismatch", "truncated", "empty_response", "scrape_unavailable",
        "invalid_metric", "no_traffic", "revision_changed", "not_configured", "missing_history",
    ]
    time_field: StrictStr
    observed_raw: StrictStr | None
    observed_offset: StrictStr | None
    collected_at: UTCDateTime
    future_within_tolerance: StrictBool = False
    diagnostic_projection: StrictBool = False
    resource_uid: StrictStr | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,80}$")
    resource_version: StrictStr | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,100}$")
    deployment_uid: StrictStr | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,80}$")
    replicaset_uid: StrictStr | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{1,80}$")
    replicaset_resource_version: StrictStr | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,100}$")
    dimension: StrictStr | None = Field(default=None, max_length=160,
        pattern=r"^(?:route=/[A-Za-z0-9_{}./-]{0,120}|operation=(?:select_one|select_all|insert|delete))$")
    truncated: StrictBool = False
    redis_secret_reference: StrictBool | None = None
    missing_outcomes: list[Literal["hit", "miss", "error", "unavailable", "bypass", "consistency_primary"]] = Field(default_factory=list, max_length=6)


class CollectionAudit(StrictModel):
    schema_version: Literal[1] = 1
    collector_version: StrictStr
    config_hash: Sha256
    query_catalog_hash: Sha256
    started_at: UTCDateTime
    completed_at: UTCDateTime
    collection_status: Literal["complete", "partial", "unavailable"]
    deadline_exceeded: StrictBool
    raw_bytes_read: StrictInt = Field(ge=0)
    workload_expected_pods: StrictInt | None = Field(default=None, ge=0)
    entries: list[CollectionEntry] = Field(min_length=1, max_length=256)


@dataclass(frozen=True)
class ReadFailure:
    status: Literal["missing", "permission_denied", "invalid"]
    reason: str

    def __post_init__(self) -> None:
        allowed = {"permission_denied", "not_found", "timeout", "upstream_error",
                   "response_too_large", "raw_budget_exceeded", "log_budget_exceeded", "deadline_exceeded", "invalid_payload",
                   "scope_mismatch", "truncated", "empty_response", "scrape_unavailable",
                   "invalid_metric", "no_traffic", "revision_changed", "not_configured", "missing_history"}
        if self.status not in {"missing", "permission_denied", "invalid"} or self.reason not in allowed:
            raise ValueError("read failure requires a fixed status and reason code")


@dataclass(frozen=True)
class CapturedSnapshot:
    bundle: EvidenceBundle
    audit: CollectionAudit


class SnapshotBuilder:
    """등록 대상의 한 번의 수집을 묶으며 통신과 시계는 모의 구현으로 바꿀 수 있다."""

    def __init__(self, settings: BSettings, target: Target, *, clock: Clock | None = None):
        self.settings = settings
        self.binding = settings.bindings.resolve(target)
        self.clock = clock or SystemClock()
        self.started_at = _utc_datetime(self.clock.now())
        self.started_tick = self.clock.monotonic()
        self.items: list[EvidenceItem] = []
        self.entries: list[CollectionEntry] = []
        self.raw_bytes = 0
        self.log_bytes = 0
        self.raw_log_bytes_read = 0
        self.workload_expected_pods: int | None = None
        self.closed = False

    def _open(self) -> None:
        if self.closed:
            raise ValueError("snapshot is already frozen")

    def _remaining(self) -> float:
        return self.settings.runtime.limits.deadline_seconds - (self.clock.monotonic() - self.started_tick)

    def _validate_read_scope(self, request: ReadRequest) -> None:
        binding = self.binding
        if request.connection == binding.prometheus_connection:
            valid = set()
            for query in self.settings.metrics.queries:
                dimensions = ([None, *binding.routes] if query.dimension == "route" else
                              [None, *binding.operations] if query.dimension == "operation" else [None])
                for dimension in dimensions:
                    valid.update(self.settings.metrics.render(query.query_id, binding, dimension=dimension).values())
            valid.update(expression.replace("@namespace@", binding.target.namespace)
                         .replace("@service@", binding.target.service)
                         for expression in self.settings.metrics.quality_expressions.values())
            params = dict(request.params)
            if (request.path != "/api/v1/query" or len(params) != len(request.params)
                    or set(params) != {"query", "time"} or params["query"] not in valid
                    or params["time"] != self.started_at.isoformat().replace("+00:00", "Z")):
                raise ValueError("Prometheus request is outside the registered instant query scope")
        elif request.connection == binding.kubernetes_connection:
            namespace = binding.target.namespace
            exact = {
                f"/apis/apps/v1/namespaces/{namespace}/deployments/{binding.target.workload}",
                f"/api/v1/namespaces/{namespace}/services/{binding.target.service}",
                f"/apis/argoproj.io/v1alpha1/namespaces/{binding.argo.namespace}/applications/{binding.argo.application}",
                f"/apis/apps/v1/namespaces/{namespace}/replicasets",
                f"/api/v1/namespaces/{namespace}/events",
                f"/api/v1/namespaces/{namespace}/pods",
            }
            pod = rf"/api/v1/namespaces/{namespace}/pods/[a-z0-9][a-z0-9.-]{{0,252}}(?:/log)?"
            if request.path not in exact and not re.fullmatch(pod, request.path):
                raise ValueError("Kubernetes request is outside the registered namespace and resource scope")
            params = dict(request.params)
            if len(params) != len(request.params):
                raise ValueError("duplicate Kubernetes read parameters")
            if request.path.endswith("/log"):
                limits = self.settings.runtime.limits
                required = {
                    "container": binding.container, "timestamps": "true", "follow": "false",
                    "sinceTime": (self.started_at - timedelta(seconds=limits.lookback_seconds)).isoformat().replace("+00:00", "Z"),
                }
                if (any(params.get(key) != value for key, value in required.items())
                        or set(params) - {*required, "tailLines", "limitBytes", "previous"}
                        or params.get("previous", "false") not in {"true", "false"}
                        or not params.get("tailLines", "").isdigit()
                        or not 1 <= int(params["tailLines"]) <= limits.log_lines_per_pod
                        or not params.get("limitBytes", "").isdigit()
                        or not 1 <= int(params["limitBytes"]) <= limits.raw_log_bytes):
                    raise ValueError("Pod log request exceeds the fixed read policy")
            elif request.path.endswith(("/pods", "/replicasets")):
                selector = ",".join(f"{key}={value}" for key, value in sorted(binding.pod_selector.items()))
                if (params.get("labelSelector") != selector or set(params) - {"labelSelector", "limit"}
                        or not params.get("limit", "").isdigit()
                        or not 1 <= int(params["limit"]) <= self.settings.runtime.limits.max_pods + 1):
                    raise ValueError("workload list requires a registered selector and bounded limit")
            elif request.path.endswith("/events"):
                if (set(params) != {"fieldSelector", "limit"}
                        or not re.fullmatch(r"involvedObject.uid=[a-zA-Z0-9-]{1,80}", params["fieldSelector"])
                        or not params["limit"].isdigit()
                        or not 1 <= int(params["limit"]) <= self.settings.runtime.limits.max_events + 1):
                    raise ValueError("event list requires an owner UID and bounded limit")
            elif params:
                raise ValueError("object snapshot does not accept extra parameters")
        else:
            raise ValueError("connection is outside the target binding")

    def read(self, request: ReadRequest, transport: ReadTransport) -> ReadResponse | ReadFailure:
        """수집기만 호출하는 읽기 경계. HTTP 실패 원문과 예외 메시지는 버린다."""
        self._open()
        self._validate_read_scope(request)
        limits = self.settings.runtime.limits
        remaining = self._remaining()
        if remaining <= 0:
            return ReadFailure("missing", "deadline_exceeded")
        max_bytes = min(limits.response_bytes, limits.total_response_bytes - self.raw_bytes)
        if request.path.endswith("/log"):
            remaining_logs = limits.raw_log_bytes - self.raw_log_bytes_read
            if remaining_logs <= 0:
                return ReadFailure("missing", "log_budget_exceeded")
            max_bytes = min(max_bytes, int(dict(request.params)["limitBytes"]), remaining_logs)
        if max_bytes <= 0:
            return ReadFailure("missing", "raw_budget_exceeded")
        try:
            response = transport.get(request, timeout_seconds=min(remaining, limits.request_timeout_seconds), max_bytes=max_bytes)
        except TimeoutError:
            return ReadFailure("missing", "timeout")
        except Exception:
            return ReadFailure("missing", "upstream_error")
        if not isinstance(response, ReadResponse) or not isinstance(response.body, bytes):
            return ReadFailure("invalid", "invalid_payload")
        self.raw_bytes += len(response.body)
        if request.path.endswith("/log"):
            # 무효·거부 응답으로 버린 내용도 읽기 예산에서는 차감한다.
            self.raw_log_bytes_read += len(response.body)
        if self._remaining() <= 0:
            return ReadFailure("missing", "deadline_exceeded")
        if len(response.body) > max_bytes:
            return ReadFailure("invalid", "response_too_large")
        if response.status_code == 403:
            return ReadFailure("permission_denied", "permission_denied")
        if response.status_code == 404:
            return ReadFailure("missing", "not_found")
        if response.status_code != 200:
            return ReadFailure("missing", "upstream_error")
        return response

    def add(
        self, source: CapturedSource, *, observed_at: str | None,
        payload: dict[str, Any] | None = None, failure: ReadFailure | None = None,
        audit_context: dict[str, Any] | None = None,
    ) -> EvidenceItem:
        """어댑터가 추출한 후보를 정규화한다. 시각·필드가 틀리면 payload를 남기지 않는다."""
        self._open()
        if source.target != self.binding.target:
            raise ValueError("captured source target does not match binding")
        allowed_time_fields = {
            "workload_status": {"response_received_at"}, "config_summary": {"response_received_at"},
            "deployment": {"response_received_at", "artifact_observed_at", "status_reconciled_at", "history_deployed_at"},
            "metric": {"evaluation_time"}, "log": {"log_timestamp"},
            "event": {"eventTime", "lastTimestamp"},
        }
        if source.time_field not in allowed_time_fields[source.kind]:
            raise ValueError("time field does not describe the captured evidence kind")
        _safe_parts(source.source_ref)
        if not source.source_ref.startswith(("kubernetes/", "prometheus/", "deployment/")):
            raise ValueError("captured source requires a relative collector reference")
        if len(self.items) >= self.settings.runtime.limits.max_items:
            raise ValueError("captured evidence item limit exceeded")
        if any(item.evidence_id == source.evidence_id for item in self.items):
            raise ValueError("duplicate captured evidence ID")
        collected_at = _utc_datetime(self.clock.now())
        if collected_at < self.started_at:
            raise ValueError("collection clock moved before snapshot start")
        limits = self.settings.runtime.limits
        window = TimeWindow(start=self.started_at - timedelta(seconds=limits.lookback_seconds), end=collected_at)
        status, reason = (failure.status, failure.reason) if failure else ("available", "collected")
        normalized = None
        observed = None
        offset = None
        raw_time = None
        counts: dict[str, int] = {}
        within_tolerance = False
        if self._remaining() <= 0:
            status, reason = "missing", "deadline_exceeded"
        if status == "available":
            try:
                if not isinstance(observed_at, str) or len(observed_at) > 64:
                    raise ValueError("captured observation has an invalid time length")
                observed, offset = normalize_source_time(observed_at)
                raw_time = observed_at
            except (ValueError, TypeError):
                status, reason = "invalid", "invalid_time"
            if observed is not None:
                if observed > collected_at:
                    within_tolerance = (observed - collected_at).total_seconds() <= limits.future_tolerance_seconds
                    status, reason = "invalid", "future_observation"
                    observed = None
                elif not window.start <= observed <= window.end:
                    status, reason = "stale", "outside_window"
                    # 범위 밖 원시 시각은 감사에 보존하고 분석용 관측 시각은 비운다.
                    observed = None
            if status == "available":
                try:
                    candidate_bytes = len(canonical_json_bytes(payload))
                    if candidate_bytes > limits.response_bytes:
                        status, reason = "invalid", "response_too_large"
                    elif source.kind == "log" and self.log_bytes + candidate_bytes > limits.raw_log_bytes:
                        status, reason = "invalid", "log_budget_exceeded"
                    else:
                        if source.kind == "log":
                            self.log_bytes += candidate_bytes
                            if len(payload.get("lines", [])) > limits.log_lines_per_pod:
                                raise ValueError("log line policy exceeded")
                        normalized, counts = normalize_captured_payload(source.kind, payload, window)
                        if source.kind == "metric" and any(
                            sample["at"] != observed.isoformat().replace("+00:00", "Z")
                            for sample in normalized["samples"]
                        ):
                            raise ValueError("instant sample and observation disagree")
                        if source.kind == "metric" and observed != self.started_at:
                            raise ValueError("metric evaluation must use snapshot start")
                        normalized["source_time"] = {
                            "observed_raw": raw_time, "observed_offset": offset,
                            "collected_raw": collected_at.isoformat().replace("+00:00", "Z"),
                            "collected_offset": "Z", "time_field": source.time_field,
                        }
                        if len(canonical_json_bytes(normalized)) > limits.payload_bytes:
                            status, reason = "invalid", "payload_too_large"
                except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
                    status, reason = "invalid", "invalid_payload"
        if status != "available":
            normalized = None
        item = EvidenceItem(
            schema_version=1, evidence_id=source.evidence_id, kind=source.kind,
            source_ref=source.source_ref, target=source.target, observed_at=observed,
            collected_at=collected_at, time_range=window, status=status,
            missing_reason=None if status == "available" else reason,
            provenance={"kind": "captured", "origin": f"{self.settings.runtime.collector_version}:{self.settings.config_hash}"},
            payload=normalized, content_hash=sha256_json(normalized) if normalized is not None else None,
            redaction_summary={"categories": sorted(counts), "count": sum(counts.values())},
        )
        entry = CollectionEntry(
            evidence_id=item.evidence_id, status=item.status, reason=reason,
            time_field=source.time_field, observed_raw=raw_time, observed_offset=offset,
            collected_at=collected_at, future_within_tolerance=within_tolerance,
            diagnostic_projection=source.kind in {"log", "event"} and status == "available",
            **(audit_context or {}),
        )
        self.items.append(item)
        self.entries.append(entry)
        return item

    def finish(self) -> CapturedSnapshot:
        """수집 완료 시각으로 구간을 고정하고 묶음·감사를 함께 반환한다."""
        self._open()
        if not self.items:
            raise ValueError("cannot freeze an empty snapshot")
        completed_at = _utc_datetime(self.clock.now())
        if completed_at < max(item.collected_at for item in self.items):
            raise ValueError("snapshot creation precedes collection")
        window = TimeWindow(start=self.started_at - timedelta(seconds=self.settings.runtime.limits.lookback_seconds), end=completed_at)
        items = [EvidenceItem.model_validate({**item.model_dump(), "time_range": window}) for item in self.items]
        bundle = build_evidence_bundle(target=self.binding.target, window=window, created_at=completed_at, evidence=items)
        if len(canonical_json_bytes(bundle.model_dump(mode="json"))) > self.settings.runtime.limits.bundle_bytes:
            raise ValueError("captured bundle exceeds configured size limit")
        available = sum(item.status == "available" for item in items)
        deadline_exceeded = self._remaining() <= 0
        audit = CollectionAudit(
            collector_version=self.settings.runtime.collector_version, config_hash=self.settings.config_hash,
            query_catalog_hash=self.settings.metrics.content_hash, started_at=self.started_at,
            completed_at=completed_at, collection_status=("complete" if available == len(items) and not deadline_exceeded else
                                                         "unavailable" if available == 0 else "partial"),
            deadline_exceeded=deadline_exceeded, raw_bytes_read=self.raw_bytes,
            workload_expected_pods=self.workload_expected_pods, entries=self.entries,
        )
        self.closed = True
        return CapturedSnapshot(bundle, audit)
