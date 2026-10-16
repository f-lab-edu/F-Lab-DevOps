"""검증된 증거 묶음과 고정 런북에서만 읽는 A4 조회 도구."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_ops.contracts import EvidenceBundle, Target, TimeWindow, canonical_json_bytes
from ai_ops.evidence import ALLOWED_QUERY_IDS, redact_text


TOOL_NAMES = frozenset({
    "get_workload_status", "get_recent_events", "get_pod_logs",
    "query_service_metrics", "get_deployment_context", "get_runbook",
})
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK_ROOT = Path(os.environ.get("AI_OPS_RUNBOOK_ROOT", PACKAGE_ROOT / "runbooks"))
SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
SAFE_HASH = re.compile(r"[0-9a-f]{64}\Z")


class ToolDenied(ValueError):
    """허용 목록·인자·대상 경계를 벗어난 호출이다."""


@dataclass(frozen=True)
class ToolResponse:
    status: str
    evidence: list[dict[str, Any]]
    runbook: dict[str, Any] | None = None

    @property
    def evidence_ids(self) -> list[str]:
        return [item["evidence_id"] for item in self.evidence]


def _exact_arguments(arguments: Any, expected: set[str]) -> dict[str, Any]:
    if not isinstance(arguments, dict) or set(arguments) != expected:
        raise ToolDenied("invalid_arguments")
    try:
        encoded = canonical_json_bytes(arguments)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ToolDenied("invalid_arguments") from None
    if len(encoded) > 8 * 1024:
        raise ToolDenied("arguments_too_large")
    return arguments


def _target(value: Any, bundle: EvidenceBundle) -> None:
    if not isinstance(value, dict):
        raise ToolDenied("invalid_target")
    try:
        target = Target.model_validate(value)
    except Exception:
        raise ToolDenied("invalid_target") from None
    if target != bundle.target:
        raise ToolDenied("out_of_scope_target")


def _window(value: Any, bundle: EvidenceBundle) -> TimeWindow:
    if not isinstance(value, dict):
        raise ToolDenied("invalid_window")
    try:
        window = TimeWindow.model_validate(value)
    except Exception:
        raise ToolDenied("invalid_window") from None
    if window.start < bundle.window.start or window.end > bundle.window.end:
        raise ToolDenied("out_of_scope_window")
    return window


def _items(bundle: EvidenceBundle, kinds: set[str], window: TimeWindow | None = None) -> list[dict[str, Any]]:
    output = []
    for item in bundle.evidence:
        if item.kind not in kinds:
            continue
        moment = item.observed_at or item.collected_at
        if window is not None and not window.start <= moment <= window.end:
            continue
        output.append(item.model_dump(mode="json"))
    return output


def _runbook(document_id: str) -> dict[str, Any]:
    """A0 목록의 문서 경로·해시를 확인하고 원문을 읽는다."""
    if not isinstance(document_id, str) or not SAFE_NAME.fullmatch(document_id):
        raise ToolDenied("invalid_document_id")
    if RUNBOOK_ROOT.is_symlink() or not RUNBOOK_ROOT.is_dir():
        raise ToolDenied("runbook_root_unavailable")
    index = RUNBOOK_ROOT / "index.yaml"
    if index.is_symlink() or not index.is_file() or index.stat().st_size > 64 * 1024:
        raise ToolDenied("invalid_runbook_index")
    current: dict[str, str] | None = None
    entries: list[dict[str, str]] = []
    for line in index.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r'  - document_id: "([a-z0-9._-]+)"', line)
        if match:
            current = {"document_id": match.group(1)}
            entries.append(current)
        elif current is not None:
            field = re.fullmatch(r'    (document_version|path|content_sha256): (?:"([^"]+)"|(\d+))', line)
            if field:
                current[field.group(1)] = field.group(2) or field.group(3)
    matches = [entry for entry in entries if entry["document_id"] == document_id]
    if len(matches) != 1:
        raise ToolDenied("runbook_not_registered")
    entry = matches[0]
    expected_path = f"ai-ops/runbooks/{document_id}.md"
    if entry.get("path") != expected_path or not SAFE_HASH.fullmatch(entry.get("content_sha256", "")):
        raise ToolDenied("invalid_runbook_entry")
    if not entry.get("document_version", "").isdigit():
        raise ToolDenied("invalid_runbook_entry")
    path = RUNBOOK_ROOT / f"{document_id}.md"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ToolDenied("invalid_runbook_file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["content_sha256"]:
        raise ToolDenied("runbook_hash_mismatch")
    try:
        content = raw.decode("utf-8")
        content, _ = redact_text(content)
    except (UnicodeDecodeError, ValueError):
        raise ToolDenied("invalid_runbook_content") from None
    if len(content.encode("utf-8")) > 64 * 1024:
        raise ToolDenied("runbook_output_too_large")
    return {
        "document_id": document_id,
        "version": entry["document_version"],
        "content_sha256": entry["content_sha256"],
        "content": content,
    }


def query_bundle(bundle: EvidenceBundle, name: str, arguments: Any) -> ToolResponse:
    """도구와 인자를 고정하고 이미 정규화된 자료만 반환한다."""
    if name not in TOOL_NAMES:
        raise ToolDenied("tool_not_allowed")
    if name == "get_runbook":
        args = _exact_arguments(arguments, {"document_id"})
        return ToolResponse(status="available", evidence=[], runbook=_runbook(args["document_id"]))

    expected = {"target"}
    if name in {"get_recent_events", "get_pod_logs", "query_service_metrics", "get_deployment_context"}:
        expected.add("window")
    if name == "get_pod_logs":
        expected.add("container")
    if name == "query_service_metrics":
        expected.add("query_id")
    args = _exact_arguments(arguments, expected)
    _target(args["target"], bundle)
    window = _window(args["window"], bundle) if "window" in args else None
    kinds = {
        "get_workload_status": {"workload_status"},
        "get_recent_events": {"event"},
        "get_pod_logs": {"log"},
        "query_service_metrics": {"metric"},
        "get_deployment_context": {"deployment", "config_summary"},
    }[name]
    items = _items(bundle, kinds, None if name == "query_service_metrics" else window)
    if name == "get_pod_logs":
        container = args["container"]
        if not isinstance(container, str) or not SAFE_NAME.fullmatch(container):
            raise ToolDenied("invalid_container")
        items = [item for item in items if container == "all" or (item["status"] == "available" and item["payload"].get("container") == container)]
    if name == "query_service_metrics":
        query_id = args["query_id"]
        if not isinstance(query_id, str) or query_id not in ALLOWED_QUERY_IDS:
            raise ToolDenied("query_not_allowed")
        items = [item for item in items if item["status"] == "available" and item["payload"].get("query_id") == query_id]
        selected = []
        for item in items:
            samples = item["payload"].get("samples", [])
            item["payload"]["samples"] = [
                sample for sample in samples
                if window.start <= datetime.fromisoformat(sample["at"].replace("Z", "+00:00")) <= window.end
            ]
            if item["payload"]["samples"]:
                item["source_content_hash"] = item.pop("content_hash")
                item["selected_from_original"] = True
                selected.append(item)
        items = selected
    return ToolResponse(status="available" if any(item["status"] == "available" for item in items) else "unavailable", evidence=items)
