"""수집기의 JSON·리소스 정체성·증거 추가 경계를 공유한다."""

from __future__ import annotations

import re
from typing import Any

from ai_ops.b_contracts import ReadRequest, ReadTransport
from ai_ops.collection import CapturedSource, ReadFailure, SnapshotBuilder
from ai_ops.evidence import _json_object


def utc_now(builder: SnapshotBuilder) -> str:
    return builder.clock.now().isoformat().replace("+00:00", "Z")


def read_json(builder: SnapshotBuilder, transport: ReadTransport, request: ReadRequest) -> dict | ReadFailure:
    response = builder.read(request, transport)
    if isinstance(response, ReadFailure):
        return response
    try:
        return _json_object(response.body)
    except (ValueError, TypeError, RecursionError):
        return ReadFailure("invalid", "invalid_payload")


def metadata(obj: dict, *, kind: str, namespace: str, name: str | None = None) -> dict:
    """API 응답의 실제 namespace·이름·UID를 대조한다."""
    meta = obj.get("metadata", {})
    if (obj.get("kind") != kind or meta.get("namespace") != namespace
            or not isinstance(meta.get("name"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", meta["name"])
            or name is not None and meta["name"] != name
            or not isinstance(meta.get("uid"), str)
            or not re.fullmatch(r"[A-Za-z0-9-]{1,80}", meta["uid"])
            or not isinstance(meta.get("resourceVersion"), str)
            or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", meta["resourceVersion"])):
        raise ValueError("resource identity mismatch")
    return meta


def owned_by(obj: dict, *, kind: str, name: str, uid: str) -> bool:
    owners = obj.get("metadata", {}).get("ownerReferences", [])
    controllers = [owner for owner in owners if owner.get("controller") is True]
    return len(controllers) == 1 and all(
        controllers[0].get(key) == value for key, value in
        {"kind": kind, "name": name, "uid": uid, "apiVersion": "apps/v1"}.items()
    )


def labels_match(obj: dict, selector: dict[str, str]) -> bool:
    labels = obj.get("metadata", {}).get("labels", {})
    return isinstance(labels, dict) and all(labels.get(key) == value for key, value in selector.items())


def list_items(value: dict, *, kind: str, limit: int) -> list[dict]:
    if value.get("kind") != kind or not isinstance(value.get("items"), list):
        raise ValueError("invalid list response")
    if value.get("metadata", {}).get("continue") or len(value["items"]) > limit:
        raise OverflowError("resource list was truncated")
    if any(not isinstance(item, dict) for item in value["items"]):
        raise ValueError("invalid list item")
    return value["items"]


def add(builder: SnapshotBuilder, key: str, kind: str, *, at: str | None = None,
        payload: dict | None = None, failure: ReadFailure | None = None,
        time_field: str | None = None, context: dict[str, Any] | None = None):
    """파일 저장 없이 정제 후보와 고정된 감사 필드만 전달한다."""
    time_field = time_field or {"metric": "evaluation_time", "event": "eventTime",
                                "log": "log_timestamp"}.get(kind, "response_received_at")
    source = CapturedSource(
        evidence_id="ev-" + key, kind=kind, target=builder.binding.target,
        source_ref=("prometheus/" if kind == "metric" else "deployment/" if kind == "deployment" else "kubernetes/") + key + ".json",
        time_field=time_field,
    )
    return builder.add(source, observed_at=at, payload=payload, failure=failure, audit_context=context)
