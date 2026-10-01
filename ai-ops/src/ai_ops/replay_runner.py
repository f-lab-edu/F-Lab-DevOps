"""모델 선택을 흉내 내는 A4 재생 실행기와 조회 감사 기록."""

from __future__ import annotations

import copy
import re
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ai_ops.contracts import EvidenceBundle, IncidentRequest
from ai_ops.evidence import redact_text
from ai_ops.replay_tools import TOOL_NAMES, ToolDenied, query_bundle


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Stop:
    """조회 종료 신호다. A5 모델은 검증 전 보고서 초안을 함께 돌려준다."""

    final_output: dict[str, Any] | None = None


class TransientModelError(Exception):
    """최대 한 번만 다시 시도할 수 있는 모델 오류다."""

    def __init__(self, reason: str = "model_retry_exhausted"):
        super().__init__(reason)
        self.reason = reason


class ModelRejected(Exception):
    """재시도할 수 없는 모델·예산·출력 계약 거부다."""


class ReplayTimeout(Exception):
    """실제 벽시계 기준으로 호출 시간이 끝났다."""


@contextmanager
def _wall_timeout(seconds: float):
    """로컬 메인 스레드에서 느린 모델·도구 호출을 중단한다."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("replay requires the main thread for hard timeouts")
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    if old_timer[0] > 0:
        raise RuntimeError("replay cannot replace an active alarm")

    def alarm(_signum, _frame):
        raise ReplayTimeout()

    signal.signal(signal.SIGALRM, alarm)
    try:
        signal.setitimer(signal.ITIMER_REAL, max(0.001, seconds))
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


class SelectionModel(Protocol):
    def next_action(self, view: dict[str, Any], timeout_seconds: float) -> ToolCall | Stop:
        """조회 도구 또는 종료를 선택한다. timeout_seconds 이내 반환해야 한다."""


@dataclass(frozen=True)
class ReplayLimits:
    total_seconds: float = 120.0
    max_tool_calls: int = 10
    tool_seconds: float = 10.0
    model_retries: int = 1

    def __post_init__(self) -> None:
        if self.total_seconds <= 0 or self.max_tool_calls <= 0 or self.tool_seconds <= 0 or self.model_retries < 0:
            raise ValueError("invalid replay limits")


@dataclass(frozen=True)
class ReplayResult:
    run_id: str
    input_hash: str
    status: str
    failure_reason: str | None
    tool_calls: int
    model_errors: int
    delivered_evidence_ids: list[str]
    delivered_runbook_ids: list[str]
    audit: list[dict[str, Any]]
    delivered_runbook_versions: dict[str, str] | None = None
    final_output: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "run_id": self.run_id, "input_hash": self.input_hash,
            "status": self.status, "failure_reason": self.failure_reason,
            "tool_calls": self.tool_calls, "model_errors": self.model_errors,
            "delivered_evidence_ids": self.delivered_evidence_ids,
            "delivered_runbook_ids": self.delivered_runbook_ids,
            "delivered_runbook_versions": self.delivered_runbook_versions or {}, "audit": self.audit,
        }


def _safe_arguments(call: ToolCall, allowed: bool) -> dict[str, Any]:
    """거부된 호출의 임의 문자열은 저장하지 않고 정상 인자만 다시 마스킹한다."""
    if not allowed:
        return {"rejected": True}

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return redact_text(value[:512])[0]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    return clean(call.arguments)


def run_replay(
    *, request: IncidentRequest, bundle: EvidenceBundle, run_id: str,
    model: SelectionModel, limits: ReplayLimits = ReplayLimits(),
    clock: Callable[[], float] = time.monotonic,
) -> ReplayResult:
    """도구 호출 수·시간·범위를 제한하며 모델에 제공한 ID만 추적한다."""
    if request.target != bundle.target or request.window != bundle.window or request.bundle_hash != bundle.bundle_hash:
        raise ValueError("request does not match replay bundle")
    if not re.fullmatch(r"run-[0-9a-f]{32}", run_id):
        raise ValueError("invalid replay run ID")
    started = clock()
    audit: list[dict[str, Any]] = []
    delivered: dict[str, dict[str, Any]] = {}
    runbooks: dict[str, dict[str, Any]] = {}
    last_result: dict[str, Any] | None = None
    calls = 0
    model_errors = 0
    status = "completed"
    reason: str | None = None
    final_output: dict[str, Any] | None = None

    while True:
        remaining = limits.total_seconds - (clock() - started)
        if remaining <= 0:
            status, reason = "limited", "total_timeout"
            break
        view = {
            "run_id": run_id,
            "question": request.question,
            "target": bundle.target.model_dump(mode="json"),
            "window": bundle.window.model_dump(mode="json"),
            "catalog": [{
                "evidence_id": item.evidence_id, "kind": item.kind,
                "status": item.status, "observed_at": item.observed_at.isoformat() if item.observed_at else None,
                "query_id": item.payload.get("query_id") if item.kind == "metric" and item.payload else None,
            } for item in bundle.evidence],
            "provided_evidence": list(delivered.values()),
            "provided_runbooks": list(runbooks.values()),
            "last_result": last_result,
        }
        try:
            with _wall_timeout(remaining):
                action = model.next_action(copy.deepcopy(view), remaining)
        except ReplayTimeout:
            status, reason = "limited", "total_timeout"
            break
        except TransientModelError as exc:
            model_errors += 1
            if model_errors > limits.model_retries:
                status, reason = "failed", exc.reason
                break
            continue
        except ModelRejected as exc:
            status, reason = "failed", str(exc)
            break
        except KeyboardInterrupt:
            status, reason = "cancelled", "user_cancelled"
            break
        except Exception:
            status, reason = "failed", "model_error"
            break
        if clock() - started >= limits.total_seconds:
            status, reason = "limited", "total_timeout"
            break
        if isinstance(action, Stop):
            final_output = action.final_output
            break
        if not isinstance(action, ToolCall):
            status, reason = "failed", "invalid_model_action"
            break
        if calls >= limits.max_tool_calls:
            audit.append({
                "run_id": run_id,
                "tool_name": action.name if isinstance(action.name, str) and action.name in TOOL_NAMES else "rejected_tool",
                "arguments": {"rejected": True}, "result_evidence_ids": [],
                "result_runbook_id": None, "duration_ms": 0, "error": "tool_call_limit",
            })
            status, reason = "limited", "tool_call_limit"
            break

        calls += 1
        call_start = clock()
        remaining = limits.total_seconds - (call_start - started)
        allowed = isinstance(action.name, str) and action.name in TOOL_NAMES
        response = None
        error = None
        try:
            with _wall_timeout(min(limits.tool_seconds, max(0.001, remaining))):
                response = query_bundle(bundle, action.name, action.arguments)
        except ReplayTimeout:
            error = "tool_timeout"
        except KeyboardInterrupt:
            error = "user_cancelled"
        except ToolDenied as exc:
            error = str(exc)
        except Exception:
            error = "tool_error"
        elapsed = clock() - call_start
        if elapsed > limits.tool_seconds:
            response, error = None, "tool_timeout"
        if clock() - started >= limits.total_seconds:
            response, error = None, "total_timeout"
        ids = response.evidence_ids if response is not None else []
        doc_id = response.runbook["document_id"] if response is not None and response.runbook else None
        audit.append({
            "run_id": run_id, "tool_name": action.name if allowed else "rejected_tool",
            "arguments": _safe_arguments(action, allowed and error is None),
            "result_evidence_ids": ids, "result_runbook_id": doc_id,
            "duration_ms": max(0, int(elapsed * 1000)), "error": error,
        })
        if error == "total_timeout":
            status, reason = "limited", error
            break
        if error == "tool_timeout":
            status, reason = "limited", error
            break
        if error == "user_cancelled":
            status, reason = "cancelled", error
            break
        if response is not None:
            for item in response.evidence:
                delivered[item["evidence_id"]] = item
            if response.runbook is not None:
                runbooks[response.runbook["document_id"]] = response.runbook
        last_result = {"status": response.status if response else "denied", "evidence_ids": ids,
                       "runbook_id": doc_id, "error": error}

    return ReplayResult(
        run_id=run_id, input_hash=bundle.bundle_hash, status=status, failure_reason=reason,
        tool_calls=calls, model_errors=model_errors,
        delivered_evidence_ids=list(delivered), delivered_runbook_ids=list(runbooks), audit=audit,
        delivered_runbook_versions={name: doc["version"] for name, doc in runbooks.items()},
        final_output=final_output,
    )


class ScriptedModel:
    """실제 AI 없이 추가 증거 선택을 재현하는 시험용 모델이다."""

    def __init__(self, actions: list[ToolCall | Stop | Exception]):
        self.actions = iter(actions)
        self.views: list[dict[str, Any]] = []

    def next_action(self, view: dict[str, Any], timeout_seconds: float) -> ToolCall | Stop:
        self.views.append(view)
        action = next(self.actions, Stop())
        if isinstance(action, Exception):
            raise action
        return action
