"""저장된 증거 파일을 제한된 범위에서 읽어 A1 계약으로 정규화한다.

이 모듈은 라이브 클러스터에 연결하지 않는다. 원시 파일은 모델 입력으로 전달하지
않으며, 명시적으로 허용한 필드와 마스킹한 문자열만 증거 묶음에 담는다.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictStr, ValidationError, field_validator, model_validator

from ai_ops.contracts import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceProvenance,
    IncidentRequest,
    RedactionSummary,
    Sha256,
    StrictModel,
    Target,
    TargetRegistry,
    TimeWindow,
    build_evidence_bundle,
    create_incident_request,
    sha256_json,
)


MANIFEST_LIMIT = 256 * 1024
RAW_FILE_LIMIT = 64 * 1024
RAW_TOTAL_LIMIT = 4 * 1024 * 1024
TIME_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z"
)
SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def normalize_source_time(raw: str) -> tuple[datetime, str]:
    """명시적 오프셋이 있는 원본 시각만 UTC로 바꾸고 원래 오프셋을 돌려준다."""
    if not isinstance(raw, str) or not TIME_PATTERN.fullmatch(raw):
        raise ValueError("source time requires ISO 8601 with explicit offset")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid source time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("source time has no offset")
    if abs(parsed.utcoffset()) > timedelta(hours=14):
        raise ValueError("source time offset exceeds 14 hours")
    offset = raw[-1:] if raw.endswith("Z") else raw[-6:]
    return parsed.astimezone(timezone.utc), offset


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# 알려진 비밀값 형태를 마스킹한다. 완전한 DLP가 아니므로 실제 캡처 자료는 사람이 검토한다.
REDACTION_RULES = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
    ("credential_url", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@"), "[REDACTED_CREDENTIAL_URL]"),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+"), "Bearer [REDACTED]"),
    ("json_secret", re.compile(r'(?i)"(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|redis_url|database_url|db_url|dsn)"\s*:\s*"(?:\\.|[^"\\])*"'), "[REDACTED_SECRET]"),
    ("named_secret", re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|redis_url|database_url|db_url|dsn)\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"), "[REDACTED_SECRET]"),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[REDACTED_JWT]"),
)


def redact_text(value: str) -> tuple[str, dict[str, int]]:
    """질문·로그·도구 출력에 같은 비밀값 규칙을 적용한다."""
    if not isinstance(value, str):
        raise TypeError("text must be a string")
    if "\x00" in value:
        raise ValueError("text contains NUL")
    counts: dict[str, int] = {}
    for category, pattern, replacement in REDACTION_RULES:
        value, count = pattern.subn(replacement, value)
        if count:
            counts[category] = count
    return value, counts


def _merge_counts(total: dict[str, int], part: dict[str, int]) -> None:
    for category, count in part.items():
        total[category] = total.get(category, 0) + count


def sanitize_question(question: str) -> tuple[str, RedactionSummary]:
    """조사 질문의 비밀값을 제거한 뒤 A1 요청에 넘길 값을 만든다."""
    if not isinstance(question, str) or len(question.encode("utf-8")) > 16 * 1024:
        raise ValueError("question exceeds input limit")
    cleaned, counts = redact_text(question)
    if not cleaned.strip() or len(cleaned) > 1000:
        raise ValueError("sanitized question must contain at most 1000 characters")
    return cleaned, RedactionSummary(categories=sorted(counts), count=sum(counts.values()))


def create_sanitized_incident_request(
    *, request_id: str, target: Target, question: str, window: TimeWindow,
    bundle: EvidenceBundle, registry: TargetRegistry, requested_at: datetime,
) -> tuple[IncidentRequest, RedactionSummary]:
    """원본 질문을 요청 계약에 넣기 전에 마스킹한다."""
    cleaned, summary = sanitize_question(question)
    request = create_incident_request(
        request_id=request_id, target=target, question=cleaned, window=window,
        bundle=bundle, registry=registry, requested_at=requested_at,
    )
    return request, summary


def _safe_parts(ref: str) -> list[str]:
    if not isinstance(ref, str) or not SAFE_REF.fullmatch(ref):
        raise ValueError("unsafe evidence path")
    parts = ref.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("unsafe evidence path")
    if redact_text(ref)[0] != ref:
        raise ValueError("evidence path contains a secret-like value")
    return parts


def _read_regular_file(root_fd: int, ref: str, limit: int) -> bytes:
    """각 경로 구성요소를 루트 FD에 상대적으로 열고 symlink를 따르지 않는다."""
    parts = _safe_parts(ref)
    directory_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
        )
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("evidence is not a regular file")
            if info.st_size > limit:
                raise ValueError("evidence file exceeds size limit")
            chunks = bytearray()
            while len(chunks) <= limit:
                block = os.read(file_fd, min(8192, limit + 1 - len(chunks)))
                if not block:
                    break
                chunks.extend(block)
            if len(chunks) > limit:
                raise ValueError("evidence file exceeds size limit")
            return bytes(chunks)
        finally:
            os.close(file_fd)
    except OSError:
        raise ValueError("evidence file cannot be opened safely") from None
    finally:
        os.close(directory_fd)


def _json_object(raw: bytes) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError("non-finite JSON number")

    try:
        value = json.loads(raw, object_pairs_hook=no_duplicates, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid JSON evidence") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON evidence must be an object")
    return value


class ManifestEntry(StrictModel):
    evidence_id: StrictStr = Field(pattern=r"^ev-[a-z0-9][a-z0-9._-]{0,63}$")
    kind: Literal["workload_status", "event", "log", "metric", "deployment", "config_summary", "runbook"]
    source_ref: StrictStr = Field(min_length=1, max_length=512)
    raw_sha256: Sha256 | None = None
    observed_at: StrictStr | None = None
    collected_at: StrictStr
    status: Literal["available", "missing", "permission_denied", "stale", "invalid"]
    missing_reason: StrictStr | None = None
    provenance: EvidenceProvenance

    @field_validator("source_ref")
    @classmethod
    def safe_source_ref(cls, value: str) -> str:
        _safe_parts(value)
        return value

    @model_validator(mode="after")
    def check_status(self) -> ManifestEntry:
        if self.status == "available":
            if self.raw_sha256 is None or self.observed_at is None or self.missing_reason is not None:
                raise ValueError("available entry requires hash and observation")
        elif self.raw_sha256 is not None or self.missing_reason is None:
            raise ValueError("unavailable entry requires reason and no raw hash")
        return self


class EvidenceManifest(StrictModel):
    schema_version: Literal[1]
    target: Target
    window: TimeWindow
    created_at: StrictStr
    entries: list[ManifestEntry] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def check_entries(self) -> EvidenceManifest:
        ids = [entry.evidence_id for entry in self.entries]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate manifest evidence ID")
        return self


# 원본의 추가 필드는 의도적으로 버린다. 중첩 항목도 각 스키마의 키만 남긴다.
PAYLOAD_FIELDS: dict[str, dict[str, Any]] = {
    "metric": {"query_id": str, "unit": str, "samples": [{"at": str, "outcome": str, "value": "number"}]},
    "log": {"container": str, "lines": [str]},
    "event": {"reason": str, "message": str, "type": str, "involved_uid": str},
    "workload_status": {"image": str, "phase": str, "ready": bool, "restarts": "number"},
    "deployment": {"image": str, "source_sha": str, "gitops_revision": str, "rollback_revision": str, "ci_run_id": str, "status": str},
    "config_summary": {"cache_intended_enabled": bool, "redis_url_configured": bool, "read_engine_role": str},
    "runbook": {"document_id": str, "version": str, "content_sha256": str},
}
ALLOWED_QUERY_IDS = {"cache_outcomes", "cache_hit_ratio", "http_error_ratio", "api_latency", "db_latency"}
QUERY_OUTCOMES = {
    "cache_outcomes": {"hit", "miss", "error", "unavailable", "bypass", "consistency_primary"},
    "cache_hit_ratio": {"ratio", "requests"},
    "http_error_ratio": {"ratio", "requests"},
    "api_latency": {"p95", "count"},
    "db_latency": {"p99", "count"},
}


def _project(value: Any, shape: Any, counts: dict[str, int], depth: int = 0) -> Any:
    if depth > 5:
        raise ValueError("evidence nesting exceeds limit")
    if shape is str:
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("evidence text exceeds field limit")
        cleaned, part = redact_text(value)
        _merge_counts(counts, part)
        return cleaned
    if shape is bool:
        if not isinstance(value, bool):
            raise ValueError("evidence boolean field is invalid")
        return value
    if shape == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("evidence number must be finite and nonnegative")
        if value < 0 or value > 10**15 or (isinstance(value, float) and not math.isfinite(value)):
            raise ValueError("evidence number must be finite and nonnegative")
        return value
    if isinstance(shape, list):
        if not isinstance(value, list) or len(value) > 256:
            raise ValueError("evidence list exceeds field limit")
        return [_project(item, shape[0], counts, depth + 1) for item in value]
    if not isinstance(value, dict):
        raise ValueError("evidence object field is invalid")
    result = {key: _project(value[key], spec, counts, depth + 1) for key, spec in shape.items() if key in value}
    if not result:
        raise ValueError("evidence has no allowed fields")
    return result


def _normalize_sample_times(payload: dict[str, Any], window: TimeWindow) -> None:
    for sample in payload.get("samples", []):
        if "at" not in sample:
            raise ValueError("metric sample has no source time")
        original = sample["at"]
        normalized, offset = normalize_source_time(original)
        if not window.start <= normalized <= window.end:
            raise ValueError("metric sample is outside the bundle window")
        sample["at"] = _utc_text(normalized)
        sample["source_at"] = original
        sample["source_offset"] = offset


def load_evidence_bundle(bundle_root: Path, registry: TargetRegistry) -> EvidenceBundle:
    """manifest와 파일을 검증해 마스킹된 EvidenceBundle을 생성한다."""
    try:
        root_fd = os.open(bundle_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise ValueError("evidence root cannot be opened safely") from None
    try:
        try:
            manifest = EvidenceManifest.model_validate(_json_object(_read_regular_file(root_fd, "manifest.json", MANIFEST_LIMIT)))
        except ValidationError:
            raise ValueError("invalid evidence manifest") from None
        registry.ensure_registered(manifest.target)
        created_at, _ = normalize_source_time(manifest.created_at)
        total_raw = 0
        items: list[EvidenceItem] = []
        for entry in manifest.entries:
            observed_at, observed_offset = (normalize_source_time(entry.observed_at) if entry.observed_at is not None else (None, None))
            collected_at, collected_offset = normalize_source_time(entry.collected_at)
            if observed_at is not None and not manifest.window.start <= observed_at <= manifest.window.end:
                raise ValueError("evidence observation is outside the bundle window")
            if observed_at is not None and observed_at > collected_at:
                raise ValueError("evidence observation follows collection")
            if collected_at > created_at:
                raise ValueError("bundle creation precedes collection")
            counts: dict[str, int] = {}
            origin, origin_counts = redact_text(entry.provenance.origin)
            _merge_counts(counts, origin_counts)
            fixture_version = entry.provenance.fixture_version
            if fixture_version is not None and redact_text(fixture_version)[0] != fixture_version:
                raise ValueError("fixture version contains a secret-like value")
            provenance = EvidenceProvenance.model_validate({
                **entry.provenance.model_dump(), "origin": origin,
            })
            payload = None
            content_hash = None
            reason = None
            if entry.status == "available":
                raw = _read_regular_file(root_fd, entry.source_ref, RAW_FILE_LIMIT)
                total_raw += len(raw)
                if total_raw > RAW_TOTAL_LIMIT:
                    raise ValueError("raw bundle exceeds 4 MiB")
                if hashlib.sha256(raw).hexdigest() != entry.raw_sha256:
                    raise ValueError("raw evidence hash mismatch")
                if entry.kind == "log":
                    if not entry.source_ref.endswith(".log"):
                        raise ValueError("log evidence requires .log file")
                    try:
                        source_payload = {"lines": raw.decode("utf-8").splitlines()}
                    except UnicodeDecodeError as exc:
                        raise ValueError("log evidence is not UTF-8") from exc
                else:
                    if not entry.source_ref.endswith(".json"):
                        raise ValueError("structured evidence requires .json file")
                    source_payload = _json_object(raw)
                payload = _project(source_payload, PAYLOAD_FIELDS[entry.kind], counts)
                if entry.kind == "metric":
                    if payload.get("query_id") not in ALLOWED_QUERY_IDS:
                        raise ValueError("metric query ID is not allowed")
                    if not payload.get("samples") or any(
                        not {"at", "outcome", "value"}.issubset(sample)
                        or sample["outcome"] not in QUERY_OUTCOMES[payload["query_id"]]
                        for sample in payload["samples"]
                    ):
                        raise ValueError("metric outcome or samples are invalid")
                    _normalize_sample_times(payload, manifest.window)
                payload["source_time"] = {
                    "observed_raw": entry.observed_at,
                    "observed_offset": observed_offset,
                    "collected_raw": entry.collected_at,
                    "collected_offset": collected_offset,
                }
                content_hash = sha256_json(payload)
            else:
                reason, reason_counts = redact_text(entry.missing_reason)
                _merge_counts(counts, reason_counts)
            items.append(EvidenceItem(
                schema_version=1, evidence_id=entry.evidence_id, kind=entry.kind,
                source_ref=entry.source_ref, observed_at=observed_at,
                collected_at=collected_at, target=manifest.target,
                time_range=manifest.window, status=entry.status,
                missing_reason=reason, provenance=provenance,
                payload=payload, content_hash=content_hash,
                redaction_summary=RedactionSummary(
                    categories=sorted(counts), count=sum(counts.values())
                ),
            ))
        return build_evidence_bundle(
            target=manifest.target, window=manifest.window,
            created_at=created_at, evidence=items,
        )
    finally:
        os.close(root_fd)
