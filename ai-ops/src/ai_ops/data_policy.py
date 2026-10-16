"""자료 출처와 snapshot 정합성 검사. captured 외부 전송은 기본 거부한다."""

from __future__ import annotations

from datetime import timedelta

from ai_ops.b_contracts import BSettings
from ai_ops.collection import CapturedSnapshot, CollectionAudit
from ai_ops.contracts import EvidenceBundle, TargetRegistry, canonical_json_bytes, parse_evidence_bundle_json
from ai_ops.evidence import _safe_parts, normalize_captured_payload, normalize_source_time


POLICY_VERSION = "synthetic-only-v1"


class ModelDataDenied(ValueError):
    """외부 전송이 허용되지 않은 입력을 고정된 사유로 거부한다."""

    reason = "captured_data_not_approved"

    def __init__(self):
        super().__init__("AI mode accepts synthetic practice evidence only")


def ensure_external_model_allowed(bundle: EvidenceBundle) -> None:
    """설정 flag나 API 키가 있어도 captured 전송을 자동 허용하지 않는다."""
    if bundle.target.environment != "practice" or any(item.provenance.kind != "synthetic" for item in bundle.evidence):
        raise ModelDataDenied()
    parse_evidence_bundle_json(canonical_json_bytes(bundle.model_dump(mode="json")))


def validate_local_bundle(bundle: EvidenceBundle, registry: TargetRegistry) -> EvidenceBundle:
    """객체 내부가 바뀐 경우도 직렬화 재검증으로 해시·시간·대상을 다시 확인한다."""
    try:
        bundle = parse_evidence_bundle_json(canonical_json_bytes(bundle.model_dump(mode="json")))
    except (ValueError, TypeError, OverflowError):
        raise ValueError("invalid evidence bundle") from None
    registry.ensure_registered(bundle.target)
    for item in bundle.evidence:
        if (item.time_range != bundle.window or item.collected_at > bundle.created_at
                or item.observed_at is not None and not bundle.window.start <= item.observed_at <= bundle.window.end):
            raise ValueError("bundle contains inconsistent collection times")
    return bundle


def validate_snapshot(snapshot: CapturedSnapshot, settings: BSettings | None = None) -> CapturedSnapshot:
    """증거와 수집 감사를 함께 검사하며 민감한 원문을 오류에 넣지 않는다."""
    try:
        target = snapshot.bundle.target
        bundle = validate_local_bundle(snapshot.bundle, TargetRegistry(schema_version=1, targets=[target]))
        audit = CollectionAudit.model_validate_json(snapshot.audit.model_dump_json())
        if target.environment != "practice" or audit.collector_version != "captured-snapshot-v1":
            raise ValueError("unsupported snapshot scope")
        if settings is not None:
            settings.bindings.resolve(target)
            if (audit.config_hash != settings.config_hash or audit.query_catalog_hash != settings.metrics.content_hash
                    or bundle.window.start != audit.started_at - timedelta(seconds=settings.runtime.limits.lookback_seconds)):
                raise ValueError("snapshot settings mismatch")
        if (audit.completed_at != bundle.created_at or not bundle.window.start < audit.started_at <= audit.completed_at
                or len(audit.entries) != len(bundle.evidence)):
            raise ValueError("snapshot audit times mismatch")
        entries = {entry.evidence_id: entry for entry in audit.entries}
        if len(entries) != len(audit.entries):
            raise ValueError("duplicate audit IDs")
        for item in bundle.evidence:
            entry = entries[item.evidence_id]
            allowed_time_fields = {
                "workload_status": {"response_received_at"}, "config_summary": {"response_received_at"},
                "log": {"log_timestamp"}, "event": {"eventTime", "lastTimestamp"},
                "metric": {"evaluation_time"},
                "deployment": {"response_received_at", "artifact_observed_at", "status_reconciled_at", "history_deployed_at"},
            }
            if entry.time_field not in allowed_time_fields.get(item.kind, set()):
                raise ValueError("snapshot source time field mismatch")
            if entry.observed_raw is not None:
                if len(entry.observed_raw) > 64 or normalize_source_time(entry.observed_raw)[1] != entry.observed_offset:
                    raise ValueError("invalid audit source timestamp")
            elif entry.observed_offset is not None:
                raise ValueError("audit offset without source time")
            if settings is not None and entry.dimension is not None:
                binding = settings.bindings.resolve(target)
                kind, value = entry.dimension.split("=", 1)
                if value not in (binding.routes if kind == "route" else binding.operations):
                    raise ValueError("unregistered audit query dimension")
            _safe_parts(item.source_ref)
            if (not item.source_ref.startswith(("kubernetes/", "prometheus/", "deployment/"))
                    or item.provenance.kind != "captured" or item.provenance.fixture_version is not None
                    or item.provenance.origin != f"{audit.collector_version}:{audit.config_hash}"
                    or entry.status != item.status or entry.collected_at != item.collected_at):
                raise ValueError("snapshot provenance mismatch")
            if item.status != "available":
                if item.missing_reason != entry.reason:
                    raise ValueError("snapshot failure mismatch")
                continue
            if entry.reason != "collected":
                raise ValueError("snapshot availability mismatch")
            payload = item.payload
            source_time = payload["source_time"]
            if set(source_time) != {"observed_raw", "observed_offset", "collected_raw", "collected_offset", "time_field"}:
                raise ValueError("invalid source time metadata")
            observed, observed_offset = normalize_source_time(source_time["observed_raw"])
            collected, collected_offset = normalize_source_time(source_time["collected_raw"])
            if (observed != item.observed_at or collected != item.collected_at
                    or observed_offset != source_time["observed_offset"] or collected_offset != source_time["collected_offset"]
                    or entry.observed_raw != source_time["observed_raw"] or entry.observed_offset != observed_offset
                    or entry.time_field != source_time["time_field"]):
                raise ValueError("source time mismatch")
            raw = {key: value for key, value in payload.items() if key not in {"source_time", "line_times"}}
            if item.kind == "metric":
                raw["samples"] = [{"at": sample["source_at"], "outcome": sample["outcome"], "value": sample["value"]}
                                  for sample in payload["samples"]]
            if item.kind == "log":
                line_times = payload["line_times"]
                if len(line_times) != len(payload["lines"]):
                    raise ValueError("log timestamp count mismatch")
                raw["lines"] = [time["source_at"] + " " + line.partition(" ")[2]
                                for line, time in zip(payload["lines"], line_times)]
            normalized, _ = normalize_captured_payload(item.kind, raw, bundle.window)
            if {**normalized, "source_time": source_time} != payload:
                raise ValueError("snapshot contains non-normalized fields")
            if item.kind == "metric" and (item.observed_at != audit.started_at or
                    any(normalize_source_time(sample["at"])[0] != audit.started_at for sample in payload["samples"])):
                raise ValueError("snapshot metric evaluation mismatch")
        count = sum(item.status == "available" for item in bundle.evidence)
        expected_status = "unavailable" if count == 0 else "complete" if count == len(bundle.evidence) and not audit.deadline_exceeded else "partial"
        if audit.collection_status != expected_status:
            raise ValueError("snapshot collection status mismatch")
        return CapturedSnapshot(bundle, audit)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ValueError("invalid captured snapshot or collection audit") from None
