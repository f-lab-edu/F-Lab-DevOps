"""기존 비교를 보존하며 새 실행의 계약 실패 사유와 제한 초안을 남긴다."""

from datetime import datetime, timedelta
import hashlib
from pathlib import Path

from pydantic import ValidationError

from ai_ops.contracts import _utc_datetime, canonical_json_bytes, sha256_json
from ai_ops.model_comparison import DEFAULT_ROOT, utc_text
from ai_ops.model_sequence_projection import parse_sequence_projection
from ai_ops.path_policy import PERSONAL_ROOT
from ai_ops.projected_model_engine import CallPlan, ProjectionModelFailure, generate_once
from ai_ops.projected_sequence_model_engine import (SEQUENCE_PROMPT_VERSION,
    request_sequence_templates, rules_sequence_projection, validate_sequence_report)
from ai_ops.sequence_comparison import SequenceComparisonStore, implementation_hash as previous_hash


SAFE_FIELDS = frozenset({"input_hash", "analysis_status", "diagnosis_status", "current_state",
    "facts", "statement", "evidence_ids", "hypotheses", "cause", "supporting_evidence_ids",
    "contradicting_evidence_ids", "limitations", "suggested_actions", "description", "execution_status"})
CONTRACT_REASONS = {
    "projection_report_hash_mismatch": "input_hash_mismatch",
    "projection_report_unseen_citation": "unavailable_evidence_reference",
    "projection_hypothesis_unsupported": "hypothesis_without_support",
    "projection_diagnosis_mismatch": "diagnosis_hypothesis_conflict",
}


def diagnose(error):
    """오류 입력값·예외 원문을 빼고 고정 분류와 허용된 필드 경로만 남긴다."""
    if isinstance(error, ValidationError):
        paths = []
        for entry in error.errors(include_input=False, include_context=False, include_url=False)[:20]:
            parts = [str(part) if type(part) is int and 0 <= part < 1000 else
                     part if isinstance(part, str) and part in SAFE_FIELDS else "unrecognized"
                     for part in entry["loc"][:6]]
            paths.append(".".join(parts) or "report")
        return {"code": "report_schema_invalid", "fields": sorted(set(paths))}
    reason = CONTRACT_REASONS.get(str(error)) if type(error) is ValueError else None
    return {"code": reason or "report_validation_internal_error", "fields": []}


def implementation_hash():
    """기존 코드와 새 진단·실행 구현을 모두 새 승인에 결합한다."""
    return sha256_json({"previous": previous_hash(),
        "diagnostics": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


class DiagnosticSequenceStore(SequenceComparisonStore):
    """새 저장 루트에서만 실행하며 이전 실패 기록·코드 해시는 보존한다."""

    def __init__(self, root=PERSONAL_ROOT/"sequence-diagnostic-comparisons", *, now=None,
                 tick=None, budget_root=DEFAULT_ROOT):
        super().__init__(root, now=now, tick=tick, budget_root=budget_root)

    def prepare(self, projection, plan):
        projection = parse_sequence_projection(canonical_json_bytes(projection.model_dump(mode="json")))
        plan = CallPlan.model_validate_json(plan.model_dump_json())
        templates = request_sequence_templates(projection.view, plan)
        bound = {"projection": projection.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
            "templates_hash": sha256_json(templates), "implementation_hash": implementation_hash(),
            "prompt_version": SEQUENCE_PROMPT_VERSION, "diagnostic_version": 1}
        digest = sha256_json(bound); identifier = "cmp-" + digest[:32]
        now = _utc_datetime(self.now())
        record = {**bound, "id": identifier, "binding_hash": digest, "state": "prepared",
            "created_at": utc_text(now), "expires_at": utc_text(now+timedelta(days=7)),
            "rules": rules_sequence_projection(projection.view).model_dump(mode="json")}
        with self.locked():
            path = self._path(identifier)
            if path.exists():
                if self._read(path).get("binding_hash") != digest:
                    raise ValueError("diagnostic comparison identity conflict")
                return identifier
            self._write(path, record)
        return identifier

    def _validated(self, identifier):
        record = self._read(self._path(identifier))
        if record.get("id") != identifier or record.get("state") not in {
                "prepared", "approved", "sending", "failed", "needs_review"}:
            raise ValueError("diagnostic comparison record rejected")
        projection = parse_sequence_projection(canonical_json_bytes(record["projection"]))
        plan = CallPlan.model_validate_json(canonical_json_bytes(record["plan"]))
        templates = request_sequence_templates(projection.view, plan)
        bound = {key: record[key] for key in ("projection", "plan", "templates_hash",
            "implementation_hash", "prompt_version", "diagnostic_version")}
        if (record["diagnostic_version"] != 1 or record["prompt_version"] != SEQUENCE_PROMPT_VERSION or
                record["implementation_hash"] != implementation_hash() or
                record["templates_hash"] != sha256_json(templates) or
                record["binding_hash"] != sha256_json(bound) or
                record["rules"] != rules_sequence_projection(projection.view).model_dump(mode="json")):
            raise ValueError("diagnostic comparison code or input changed")
        if "model_report" in record:
            validate_sequence_report(record["model_report"], projection.view)
        if "rejected_draft" in record:
            if (len(canonical_json_bytes(record["rejected_draft"])) > 64*1024 or
                    sha256_json(record["rejected_draft"]) != record.get("rejected_draft_hash")):
                raise ValueError("diagnostic draft integrity rejected")
        return record, projection, plan, templates

    def run(self, identifier, *, transport=None):
        if transport is None or getattr(transport, "comparison_mode", None) not in {"mock", "live"}:
            raise ValueError("comparison_transport_not_enabled")
        with self.locked():
            record, projection, plan, templates = self._validated(identifier)
            if (transport.comparison_mode != plan.execution_mode or
                    (plan.execution_mode == "live" and
                     getattr(transport, "expected_credential", None) != plan.credential_fingerprint)):
                raise ValueError("comparison_transport_plan_mismatch")
            if record["state"] == "sending":
                record.update(state="needs_review", reason="projection_interrupted_unknown")
                self._write(self._path(identifier), record)
                raise ValueError("comparison_previous_send_unknown")
            if record["state"] in {"failed", "needs_review"}:
                return record
            now = _utc_datetime(self.now()); approval = record.get("approval", {})
            if (record["state"] != "approved" or approval.get("binding_hash") != record["binding_hash"] or
                    not _utc_datetime(approval["approved_at"]) <= now < _utc_datetime(approval["expires_at"])):
                raise ValueError("comparison_not_approved_or_expired")
            if plan.execution_mode == "live" and (
                    approval.get("provider_retention_acknowledged") is not True or
                    approval.get("optional_data_sharing_off_confirmed_by_operator") is not True):
                raise ValueError("comparison_account_confirmation_required")
            if not 0 <= (now.date()-datetime.fromisoformat(plan.price.checked_on).date()).days <= 7:
                raise ValueError("comparison_price_check_expired")
            self._reserve(now, plan.execution_mode)
            record.update(state="sending", started_at=utc_text(now), reserved_usd=0.10,
                          transport_kind=plan.execution_mode, external_model_calls=0)
            self._write(self._path(identifier), record)
            draft = None; usage = None
            try:
                draft, usage, model = generate_once(templates, plan, transport, clock=self.tick)
                report = validate_sequence_report(draft, projection.view)
                record.update(state="needs_review", reason="semantic_review_required", usage=usage,
                              response_model=model, model_report=report.model_dump(mode="json"))
            except ProjectionModelFailure as error:
                record.update(state="needs_review", reason=error.reason, usage=error.usage)
            except Exception as error:
                diagnostic = diagnose(error)
                try:
                    encoded = canonical_json_bytes(draft) if draft is not None else b""
                except (ValueError, TypeError, OverflowError, RecursionError):
                    encoded = b""
                if encoded and len(encoded) <= 64*1024:
                    record.update(rejected_draft=draft, rejected_draft_hash=sha256_json(draft))
                    diagnostic["draft_retained"] = True
                else:
                    diagnostic["draft_retained"] = False
                record.update(state="failed", reason="projection_report_contract_rejected",
                              usage=usage, failure_diagnostic=diagnostic)
            record["finished_at"] = utc_text(self.now())
            if plan.execution_mode == "live":
                record["external_model_calls"] = getattr(transport, "attempts", {}).get("/v1/responses", 0)
                record["external_request_attempts"] = dict(getattr(transport, "attempts", {}))
            self._write(self._path(identifier), record)
            return record

    def summary(self, identifier):
        """CLI에는 원문을 반환하지 않고 상태·사용량·안전한 진단만 반환한다."""
        record = self.show(identifier)
        return {key: record.get(key) for key in ("id", "state", "reason", "failure_diagnostic",
                                                "external_model_calls", "usage")}
