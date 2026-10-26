"""v3 실습 조회 순서의 미전송 요청과 동일 입력 모의 보고서다."""

from __future__ import annotations

from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.model_sequence_projection import SequenceModelView, SequenceViewItem
from ai_ops.projected_model_engine import (CallPlan, ProjectionAnalysisReport,
    _strict_schema, validate_projected_report)


SEQUENCE_PROMPT_VERSION = "projection-sequence-read-v3"
SEQUENCE_QUESTION = "제공된 한 실습 아이템의 캐시 조회 순서와 관측 한계를 설명하세요."
SEQUENCE_INSTRUCTIONS = """너는 읽기 전용 SRE 조사자다. 입력은 허용 필드만 선별한 과거 실습 관측이다.
cache_probe_sequence는 같은 실습 아이템의 검증된 조회 순서다. 원본 아이템 ID·로그·주입 명령·원인은 제공하지 않았다.
첫 miss 다음 hit는 이 아이템의 제한된 캐시 동작과 일치한다. 자연 TTL 만료·Redis 장애·전체 서비스 정상은 확정하지 않는다.
누락 지표는 0이 아니다. 실제 포함된 available 증거 ID만 인용한다. 현재 상태는 unknown,
분석 상태는 partial, 모든 조치는 not_executed다. 입력 view_hash를 input_hash로 그대로 사용하고 JSON만 반환한다."""


def request_sequence_templates(view: SequenceModelView, plan: CallPlan) -> dict:
    """승인 전 두 HTTP 본문의 전체 내용을 구성하지만 전송하지 않는다."""
    view = SequenceModelView.model_validate_json(view.model_dump_json())
    payload = {"question": SEQUENCE_QUESTION,
               "view_hash": sha256_json(view.model_dump(mode="json")),
               "view": view.model_dump(mode="json")}
    body = {"model": plan.model, "store": False, "background": False,
            "service_tier": "default", "reasoning": {"effort": plan.effort},
            "instructions": SEQUENCE_INSTRUCTIONS,
            "input": [{"role": "user", "content": canonical_json_bytes(payload).decode()}],
            "text": {"format": {"type": "json_schema", "name": "projection_report_sequence_v3",
                                "strict": True, "schema": _strict_schema()}},
            "max_output_tokens": plan.max_output_tokens}
    count = {key: value for key, value in body.items()
             if key in {"model", "input", "instructions", "reasoning", "text"}}
    if any(len(canonical_json_bytes(request)) > plan.max_input_bytes for request in (count, body)):
        raise ValueError("sequence projection request too large")
    return {"/v1/responses/input_tokens": count, "/v1/responses": body}


def rules_sequence_projection(view: SequenceModelView) -> ProjectionAnalysisReport:
    """모의 검증용 요약이며 실제 모델 의미 품질의 근거는 아니다."""
    view = SequenceModelView.model_validate_json(view.model_dump_json())
    item = next(value for value in view.items if isinstance(value, SequenceViewItem))
    outcomes = " → ".join(read.cache_outcome for read in item.payload.reads)
    report = {"input_hash": sha256_json(view.model_dump(mode="json")),
        "analysis_status": "partial", "diagnosis_status": "insufficient_evidence",
        "current_state": "unknown",
        "facts": [{"statement": "같은 실습 아이템의 HTTP 200 조회에서 캐시 결과 " + outcomes +
                   " 순서가 관측됐다.", "evidence_ids": [item.evidence_id]}],
        "hypotheses": [],
        "limitations": ["이 실습 아이템의 과거 조회이며 자연 TTL 만료나 전체 서비스 정상은 확인하지 않았다.",
                         "원본 로그·아이템 ID·실습 조작은 모델 입력에 없다."],
        "suggested_actions": []}
    return validate_sequence_report(report, view)


def validate_sequence_report(report, view: SequenceModelView) -> ProjectionAnalysisReport:
    """기존 인용·해시 계약을 적용하며 자연어 의미 채점은 별도로 남긴다."""
    view = SequenceModelView.model_validate_json(view.model_dump_json())
    return validate_projected_report(report, view)
