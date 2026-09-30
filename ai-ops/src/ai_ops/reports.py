"""검증된 보고서의 근거를 확인하고 사용자용 KST Markdown을 만든다."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ai_ops.contracts import AnalysisReport, EvidenceBundle, IncidentRequest


KST = ZoneInfo("Asia/Seoul")
DIAGNOSIS = {
    "suspected_cause": "원인 후보 있음",
    "no_incident": "제공된 증거에서 장애 징후 미확인",
    "insufficient_evidence": "증거 부족",
}
CURRENT_STATE = {
    "affected": "영향 있음",
    "recovered": "복구됨(저장된 증거 기준)",
    "healthy": "정상(저장된 증거 기준)",
    "unknown": "확인 불가",
}


def format_kst(value: datetime | None) -> str:
    """UTC 기록을 같은 순간의 사용자용 KST 문자열로 변환한다."""
    if value is None:
        return "없음"
    if value.tzinfo is None:
        raise ValueError("display time must be timezone aware")
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST (UTC+09:00)")


def _safe_markdown(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = re.sub(r"(?i)https?://[^\s]+", "[외부 링크 생략]", value)
    return re.sub(r"([\\`*_{}\[\]()<>#+!|~])", r"\\\1", value)


def validate_report(
    report: AnalysisReport, request: IncidentRequest, bundle: EvidenceBundle, run_id: str
) -> None:
    """저장 전 보고서가 해당 사건·실행·제공된 증거에 속하는지 확인한다."""
    if report.incident_id != request.incident_id or report.run_id != run_id:
        raise ValueError("report incident or run does not match request")
    if report.input_hash != bundle.bundle_hash or report.target != request.target or report.window != request.window:
        raise ValueError("report input does not match evidence bundle")
    if report.execution.mode != "rules":
        raise ValueError("A3 accepts rules reports only")
    available_ids = {item.evidence_id for item in bundle.evidence if item.status == "available"}
    cited = [evidence_id for fact in report.facts for evidence_id in fact.evidence_ids]
    cited += [evidence_id for hypothesis in report.hypotheses for evidence_id in
              hypothesis.supporting_evidence_ids + hypothesis.contradicting_evidence_ids]
    if any(evidence_id not in available_ids for evidence_id in cited):
        raise ValueError("report cites unavailable or unknown evidence")


def render_markdown(
    report: AnalysisReport, request: IncidentRequest, bundle: EvidenceBundle
) -> str:
    """UTC JSON 보고서에서만 Markdown을 만들고 표시 시각만 KST로 변환한다."""
    if request.target != bundle.target or request.window != bundle.window or request.bundle_hash != bundle.bundle_hash:
        raise ValueError("request does not match evidence bundle")
    validate_report(report, request, bundle, report.run_id)
    lines = [
        "# 운영 조사 보고서",
        "",
        "> 규칙 기반 로컬 조사 결과입니다. AI 분석·실시간 클러스터 확인·변경 실행은 하지 않았습니다.",
        "",
        f"- 사건 ID: `{report.incident_id}`",
        f"- 실행 ID: `{report.run_id}`",
        f"- 요청 시각: {format_kst(request.requested_at)}",
        f"- 조사 범위: {format_kst(report.window.start)} ~ {format_kst(report.window.end)}",
        f"- 진단: {DIAGNOSIS[report.diagnosis_status]}",
        f"- 최신 상태 판단: {CURRENT_STATE[report.current_state]}",
        f"- 입력 묶음 SHA-256: `{report.input_hash}`",
        "",
        "## 관측 사실",
        "",
    ]
    if report.facts:
        lines += [f"- {_safe_markdown(fact.statement)} (근거: {', '.join(fact.evidence_ids)})" for fact in report.facts]
    else:
        lines.append("- 확인 가능한 관측 사실이 부족합니다.")
    lines += ["", "## 원인 후보와 반대 근거", ""]
    if report.hypotheses:
        for hypothesis in report.hypotheses:
            lines.append(f"- {_safe_markdown(hypothesis.cause)}")
            lines.append(f"  - 지지 증거: {', '.join(hypothesis.supporting_evidence_ids) or '없음'}")
            lines.append(f"  - 반대 증거: {', '.join(hypothesis.contradicting_evidence_ids) or '없음'}")
    else:
        lines.append("- 제시할 원인 후보가 없습니다.")
    lines += ["", "## 부족한 증거", ""]
    lines += [f"- {_safe_markdown(item.source)}: {_safe_markdown(item.reason)}" for item in report.missing_evidence] or ["- 명시된 누락 항목이 없습니다."]
    lines += ["", "## 추가 확인", ""]
    lines += [f"- {_safe_markdown(item)}" for item in report.recommended_checks] or ["- 없음"]
    lines += ["", "## 대응 제안", ""]
    lines += [f"- {_safe_markdown(item.description)} (미실행)" for item in report.suggested_actions] or ["- 실행한 변경이나 대응 제안이 없습니다."]
    lines += ["", "## 증거 시각", "", "| 증거 ID | 상태 | 관측 시각 | 수집 시각 |", "|---|---|---|---|"]
    for item in bundle.evidence:
        lines.append(
            f"| `{item.evidence_id}` | {item.status} | {format_kst(item.observed_at)} | {format_kst(item.collected_at)} |"
        )
    lines += ["", "## 한계와 실행 정보", ""]
    lines += [f"- {_safe_markdown(item)}" for item in report.limitations]
    lines += [f"- 실행 방식: 규칙 (`{report.execution.engine}`), 도구 호출 {report.execution.tool_calls or 0}회", ""]
    return "\n".join(lines)
