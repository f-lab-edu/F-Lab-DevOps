"""저장된 CI v2 모델 보고서를 원문 변경 없이 KST HTML로 표시한다."""

from __future__ import annotations

import html
import re
from datetime import datetime

from ai_ops.model_ci_projection import CIModelView, CIViewItem
from ai_ops.projected_ci_model_engine import validate_ci_report
from ai_ops.projected_model_engine import ProjectionAnalysisReport
from ai_ops.reports import _display_utc_times, format_kst


_UNQUALIFIED_CLOCK = re.compile(
    r"(?<![\d:])(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?!\d)(?!\s+(?:UTC|KST)\b)"
)


def _display_statement(value: str, evidence_ids: list[str], view: CIModelView) -> str:
    """인용된 관측과 초까지 일치하는 시각만 KST로 바꾼다."""
    cited = {item.evidence_id: item for item in view.items
             if item.evidence_id in evidence_ids and item.observed_at is not None}
    converted = _display_utc_times(value, view.window)

    def clock(match: re.Match[str]) -> str:
        matches: set[datetime] = {item.observed_at for item in cited.values()
                                  if item.observed_at.strftime("%H:%M:%S") == match.group(0)}
        if len(matches) != 1:
            return match.group(0) + " [시간대 확인 필요]"
        moment = matches.pop()
        return f"{format_kst(moment)} (원문 {match.group(0)} UTC)"

    return html.escape(_UNQUALIFIED_CLOCK.sub(clock, converted))


def render_ci_model_html(report: ProjectionAnalysisReport | dict,
                         view: CIModelView | dict) -> str:
    """검증된 모델 보고서에 입력 범위 안내와 근거 기반 시각 표시를 덧붙인다."""
    view = CIModelView.model_validate(view)
    report = validate_ci_report(report, view)

    def cited(ids: list[str]) -> str:
        return ", ".join(html.escape(item) for item in ids) or "없음"

    facts = "".join(
        f"<li>{_display_statement(fact.statement, fact.evidence_ids, view)} "
        f"<small>근거: {cited(fact.evidence_ids)}</small></li>"
        for fact in report.facts
    ) or "<li>확인 가능한 관측 사실 없음</li>"
    hypotheses = "".join(
        f"<li>{_display_statement(item.cause, item.supporting_evidence_ids, view)} "
        f"<small>지지 근거: {cited(item.supporting_evidence_ids)}; "
        f"반대 근거: {cited(item.contradicting_evidence_ids)}</small></li>"
        for item in report.hypotheses
    ) or "<li>제시한 원인 후보 없음</li>"
    limitations = "".join(
        f"<li>{_display_statement(item, [], view)}</li>" for item in report.limitations
    )
    actions = "".join(
        f"<li>{_display_statement(item.description, [], view)} "
        "<small>미실행</small></li>" for item in report.suggested_actions
    ) or "<li>제안한 조치 없음</li>"
    stages = "".join(
        f"<tr><td>{html.escape(item.evidence_id)}</td>"
        f"<td>{'후보 배포 실패' if item.payload.stage == 'failed_deployment' else '기준 버전 원복 검증'}</td>"
        f"<td>{html.escape(format_kst(item.observed_at))}</td>"
        f"<td>{item.payload.ready_replicas}</td>"
        f"<td>{'관측' if item.payload.image_pull_failure_observed else '미관측'}</td>"
        f"<td>{'검증' if item.payload.user_path_recovery_verified else '미검증'}</td></tr>"
        for item in view.items if isinstance(item, CIViewItem)
    )
    missing_metrics = sum(item.kind == "metric" and item.source_status != "available"
                          for item in view.items)
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CI v2 모델 보고서 보기</title>
<style>
body{{font:16px/1.65 system-ui,sans-serif;max-width:1040px;margin:0 auto;padding:32px;color:#173047;background:#f7fafc}}
h1,h2{{line-height:1.25}}section,header{{background:white;border:1px solid #dce5ed;border-radius:12px;padding:20px;margin:18px 0}}
.notice{{background:#e9f3ff;border-left:4px solid #3672b9;padding:12px 16px}}.warn{{background:#fff2d2;border-left:4px solid #bb8524;padding:12px 16px}}
li{{margin:10px 0}}small{{color:#53677a}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #dce5ed;padding:8px;text-align:left}}
.table-wrap{{overflow-x:auto}}code{{overflow-wrap:anywhere}}
</style></head><body>
<header><h1>CI v2 모델 보고서 보기</h1>
<p class="notice">이 보고서는 <strong>모델에 전달된 정제 입력</strong>만 분석한 과거 실습 결과입니다. “제공되지 않았다”나 “확인할 수 없다”는 표현은 우선 그 입력 범위를 뜻합니다. 원본 CI 기록에는 제외된 이미지·revision·최종 결론이 있을 수 있습니다.</p>
<p>저장된 보고서 원문은 변경하지 않았습니다. 인용한 관측과 일치하는 UTC 시각만 KST로 표시하며, 맞는 근거가 없는 시각은 변환하지 않습니다.</p>
<p class="warn">CI의 원복 검증은 사용자 요청 경로 복구나 현재 전체 서비스 정상 상태를 증명하지 않습니다. 모델이 조치를 실행한 결과도 아닙니다.</p>
<p>분석: {html.escape(report.analysis_status)} · 원인 판단: {html.escape(report.diagnosis_status)} · 현재 상태: {html.escape(report.current_state)} · 누락 지표: {missing_metrics}개</p></header>
<section><h2>관측 사실</h2><ul>{facts}</ul></section>
<section><h2>원인 후보</h2><ul>{hypotheses}</ul></section>
<section><h2>한계</h2><p>아래 문장은 모델 입력 기준입니다. 원본 CI 자료 전체의 부재를 뜻하지 않습니다.</p><ul>{limitations}</ul></section>
<section><h2>제안한 확인·조치</h2><ul>{actions}</ul></section>
<section><h2>모델에 전달된 CI 관측</h2><div class="table-wrap"><table><thead><tr><th>증거 ID</th><th>단계</th><th>관측 시각</th><th>Ready</th><th>이미지 취득 실패</th><th>사용자 경로 복구</th></tr></thead><tbody>{stages}</tbody></table></div></section>
</body></html>'''
