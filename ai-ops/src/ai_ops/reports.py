"""검증된 보고서의 근거를 확인하고 사용자용 KST Markdown을 만든다."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ai_ops.contracts import AnalysisReport, EvidenceBundle, IncidentRequest, TimeWindow


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


_UTC_ABSOLUTE = re.compile(
    r"(?<!\d)(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2})?)(?:\s+UTC(?![+-])|Z\b|\+00:00)"
)
_UTC_CLOCK = re.compile(r"(?<![\d:])(\d{2}:\d{2}(?::\d{2})?)\s+UTC(?![+-])")


def _display_utc_times(value: str, window: TimeWindow) -> str:
    """명시된 UTC 시각만 KST로 바꾼다. 날짜가 모호한 시각은 추정하지 않는다."""
    def absolute(match: re.Match[str]) -> str:
        try:
            moment = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}+00:00")
        except ValueError:
            return match.group(0)
        return format_kst(moment)

    converted = _UTC_ABSOLUTE.sub(absolute, value)
    start_date = window.start.astimezone(timezone.utc).date()
    end_date = window.end.astimezone(timezone.utc).date()
    if start_date != end_date:
        return converted

    def clock(match: re.Match[str]) -> str:
        try:
            moment = datetime.fromisoformat(f"{start_date.isoformat()}T{match.group(1)}+00:00")
        except ValueError:
            return match.group(0)
        return format_kst(moment) if window.start <= moment <= window.end else match.group(0)

    return _UTC_CLOCK.sub(clock, converted)


def validate_report(
    report: AnalysisReport, request: IncidentRequest, bundle: EvidenceBundle, run_id: str,
    delivered_evidence_ids: set[str] | None = None,
) -> None:
    """저장 전 보고서가 해당 사건·실행·제공된 증거에 속하는지 확인한다."""
    if report.incident_id != request.incident_id or report.run_id != run_id:
        raise ValueError("report incident or run does not match request")
    if report.input_hash != bundle.bundle_hash or report.target != request.target or report.window != request.window:
        raise ValueError("report input does not match evidence bundle")
    available_ids = {item.evidence_id for item in bundle.evidence if item.status == "available"}
    if report.execution.mode == "ai":
        if delivered_evidence_ids is None:
            raise ValueError("AI report requires delivered evidence IDs")
        available_ids &= delivered_evidence_ids
        if report.analysis_status not in ("complete", "partial"):
            raise ValueError("completed AI run cannot contain failed report")
        if any(not item.supporting_evidence_ids for item in report.hypotheses):
            raise ValueError("AI hypothesis requires delivered supporting evidence")
        if not all((report.execution.engine, report.execution.model, report.execution.prompt_version,
                    report.execution.tool_version, report.execution.input_tokens is not None,
                    report.execution.output_tokens is not None, report.execution.cost_usd is not None,
                    report.execution.account_label, report.execution.price_date)):
            raise ValueError("AI execution metadata is incomplete")
    cited = [evidence_id for fact in report.facts for evidence_id in fact.evidence_ids]
    cited += [evidence_id for hypothesis in report.hypotheses for evidence_id in
              hypothesis.supporting_evidence_ids + hypothesis.contradicting_evidence_ids]
    if any(evidence_id not in available_ids for evidence_id in cited):
        raise ValueError("report cites unavailable or unknown evidence")


def render_markdown(
    report: AnalysisReport, request: IncidentRequest, bundle: EvidenceBundle,
    delivered_evidence_ids: set[str] | None = None,
) -> str:
    """UTC JSON 보고서에서만 Markdown을 만들고 표시 시각만 KST로 변환한다."""
    if request.target != bundle.target or request.window != bundle.window or request.bundle_hash != bundle.bundle_hash:
        raise ValueError("request does not match evidence bundle")
    validate_report(report, request, bundle, report.run_id, delivered_evidence_ids)
    def display(value: str) -> str:
        return _safe_markdown(_display_utc_times(value, report.window))

    notice = ("AI가 합성 저장 증거를 읽고 작성한 조사 초안입니다. 실시간 클러스터 확인·변경 실행은 하지 않았습니다."
              if report.execution.mode == "ai" else
              "규칙 기반 로컬 조사 결과입니다. AI 분석·실시간 클러스터 확인·변경 실행은 하지 않았습니다.")
    lines = [
        "# 운영 조사 보고서",
        "",
        f"> {notice}",
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
        lines += [f"- {display(fact.statement)} (근거: {', '.join(fact.evidence_ids)})" for fact in report.facts]
    else:
        lines.append("- 확인 가능한 관측 사실이 부족합니다.")
    lines += ["", "## 원인 후보와 반대 근거", ""]
    if report.hypotheses:
        for hypothesis in report.hypotheses:
            lines.append(f"- {display(hypothesis.cause)}")
            lines.append(f"  - 지지 증거: {', '.join(hypothesis.supporting_evidence_ids) or '없음'}")
            lines.append(f"  - 반대 증거: {', '.join(hypothesis.contradicting_evidence_ids) or '없음'}")
    else:
        lines.append("- 제시할 원인 후보가 없습니다.")
    lines += ["", "## 부족한 증거", ""]
    lines += [f"- {display(item.source)}: {display(item.reason)}" for item in report.missing_evidence] or ["- 명시된 누락 항목이 없습니다."]
    lines += ["", "## 추가 확인", ""]
    lines += [f"- {display(item)}" for item in report.recommended_checks] or ["- 없음"]
    lines += ["", "## 대응 제안", ""]
    lines += [f"- {display(item.description)} (미실행)" for item in report.suggested_actions] or ["- 실행한 변경이나 대응 제안이 없습니다."]
    lines += ["", "## 증거 시각", "", "| 증거 ID | 상태 | 관측 시각 | 수집 시각 |", "|---|---|---|---|"]
    for item in bundle.evidence:
        lines.append(
            f"| `{item.evidence_id}` | {item.status} | {format_kst(item.observed_at)} | {format_kst(item.collected_at)} |"
        )
    lines += ["", "## 한계와 실행 정보", ""]
    lines += [f"- {display(item)}" for item in report.limitations]
    mode = "AI" if report.execution.mode == "ai" else "규칙"
    lines += [f"- 실행 방식: {mode} (`{report.execution.engine}`), 도구 호출 {report.execution.tool_calls or 0}회"]
    if report.execution.mode == "ai":
        lines += [f"- 모델: `{report.execution.model}` · 프롬프트: `{report.execution.prompt_version}`",
                  f"- 사용량: 입력 {report.execution.input_tokens} / 출력 {report.execution.output_tokens} 토큰",
                  f"- 추정 비용: ${report.execution.cost_usd:.6f} (표준 단가 기준)"]
    lines.append("")
    return "\n".join(lines)
