"""수집 감사의 CI 출처를 대조해 최초 실패와 자동 원복의 확인 범위를 설명한다."""

from __future__ import annotations

import re

from ai_ops.contracts import AnalysisReport
from ai_ops.evidence import normalize_source_time
from ai_ops.lab_ci_history import REGISTRY, WORKFLOW


ENGINE = "rules-captured-ci-v1"
IDENTITY_FIELDS = ("ci_run_id", "ci_run_attempt", "ci_artifact_id", "ci_archive_digest",
                   "ci_source_revision", "resource_uid", "deployment_uid")


def has_ci_history(bundle, audit) -> bool:
    """CI 감사 또는 CI 출처가 붙은 배포 입력은 일반 롤백 규칙과 분리한다."""
    return (any(entry.ci_run_id or entry.time_field == "artifact_observed_at" for entry in audit.entries)
            if audit is not None else False) or any(
                item.kind == "deployment" and item.provenance.kind == "captured"
                and item.payload and item.payload.get("ci_run_id") for item in bundle.evidence)


def _eligible(item, entry) -> bool:
    if (item.kind != "deployment" or item.status != "available" or item.provenance.kind != "captured"
            or entry is None or entry.status != "available" or entry.reason != "collected"
            or entry.time_field != "artifact_observed_at" or entry.ci_workflow != WORKFLOW
            or entry.ci_run_conclusion != "failure" or entry.ci_user_path_recovery_verified is not False
            or not all(getattr(entry, key) for key in IDENTITY_FIELDS)):
        return False
    if (item.target.cluster_id != "urlshortener" or item.target.environment != "practice"
            or item.target.namespace != "ai-ops-lab" or item.target.workload != "lab-api"
            or item.target.service != "lab-api"):
        return False
    payload = item.payload
    if payload.get("ci_run_id") != entry.ci_run_id or payload.get("source_sha") != entry.ci_source_revision:
        return False
    if not re.fullmatch(r"[0-9a-f]{40}", payload.get("gitops_revision", "")):
        return False
    if entry.ci_stage == "deployment":
        return (entry.ci_verification_result == "failure" and payload.get("status") == "failed"
                and bool(re.fullmatch(re.escape(REGISTRY) + r":b6-missing-[0-9a-f]{32}", payload.get("image", ""))))
    return (entry.ci_stage == "rollback" and entry.ci_verification_result == "success"
            and payload.get("status") == "healthy_after_rollback"
            and payload.get("gitops_revision") == payload.get("rollback_revision")
            and bool(re.fullmatch(re.escape(REGISTRY) + r"@sha256:[0-9a-f]{64}", payload.get("image", ""))))


def _utc_text(moment):
    # 보고서 JSON은 UTC를 유지하고 Markdown의 기존 변환기가 KST로 표시한다.
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def analyze_ci_snapshot(request, bundle, run_id, audit=None) -> AnalysisReport:
    """같은 실행의 유효한 두 관측만 연결하며 전체 서비스 복구는 판단하지 않는다."""
    facts, hypotheses = [], []
    checks = ["같은 run/attempt의 실패·원복 artifact와 대상 UID·revision을 함께 확인한다.",
              "사용자 HTTP 경로·DB·Redis의 복구는 해당 관측 이후의 별도 자료로 확인한다."]
    limitations = [
        "수집 시점의 CI 배포 이력을 규칙으로 해석한 결과다. AI 분석이나 변경 실행은 하지 않았다.",
        "배포 관측과 CI 단계 결과만 해석하며 다른 경보 원인이나 현재 전체 서비스 건강을 판정하지 않는다.",
        "사용자 요청 경로 복구는 확인하지 않았다. 자동 원복 기록만으로 HTTP·DB·Redis 정상이나 서비스 복구 완료를 주장하지 않는다.",
    ]
    entries = {entry.evidence_id: entry for entry in audit.entries} if audit else {}
    valid = [(item, entries.get(item.evidence_id)) for item in bundle.evidence
             if _eligible(item, entries.get(item.evidence_id))]
    failures = [(item, entry) for item, entry in valid if entry.ci_stage == "deployment"]
    rollbacks = [(item, entry) for item, entry in valid if entry.ci_stage == "rollback"]
    pair = None
    if len(failures) == len(rollbacks) == 1:
        failed, before = failures[0]
        restored, after = rollbacks[0]
        if (all(getattr(before, key) == getattr(after, key) for key in IDENTITY_FIELDS)
                and failed.observed_at < restored.observed_at
                and failed.payload["gitops_revision"] != restored.payload["gitops_revision"]):
            pair = (failed, restored, before)
    if pair:
        failed, restored, entry = pair
        facts = [
            {"statement": f"CI 실행 {entry.ci_run_id}, attempt {entry.ci_run_attempt}의 최초 배포 검증은 {_utc_text(failed.observed_at)}에 실패로 기록됐다.",
             "evidence_ids": [failed.evidence_id]},
            {"statement": f"같은 CI 실행의 자동 원복 단계는 {_utc_text(restored.observed_at)}에 원래 revision의 배포 정상 상태를 확인한 것으로 기록됐다.",
             "evidence_ids": [restored.evidence_id]},
            {"statement": "CI 최종 결과 failure는 최초 배포 실패를 보존한 결과다. 같은 실행의 자동 원복 단계 success와 구분한다.",
             "evidence_ids": [failed.evidence_id, restored.evidence_id]},
        ]
        hypotheses = [{"cause": "이미지 변경 배포가 실패한 뒤 원래 revision으로 자동 원복된 흐름이 관측됐다. 사용자 영향의 원인과 복구 범위는 추가 확인이 필요하다.",
                       "supporting_evidence_ids": [failed.evidence_id, restored.evidence_id]}]
        limitations.append("이후 상태 변화는 관측하지 않았고 현재 전체 서비스의 복구 여부는 확인 불가다.")
    else:
        limitations.append("동일 run/attempt·artifact·소스·대상 UID와 시간 순서를 만족하는 실패/원복 쌍이 없어 자동 원복 흐름을 확정하지 않았다.")
        # 쌍이 없을 때도 검증된 단일 관측은 그 단계의 사실로만 남긴다.
        for item, entry in valid[:10]:
            label = "배포 검증 failure" if entry.ci_stage == "deployment" else "원복 검증 success"
            facts.append({"statement": f"CI 실행 {entry.ci_run_id}, attempt {entry.ci_run_attempt}에 {_utc_text(item.observed_at)}의 {label} 단일 관측이 있다. 전후 흐름과 사용자 복구는 별도 확인이 필요하다.",
                          "evidence_ids": [item.evidence_id]})
    if valid:
        limitations.append("해석에 사용한 CI 관측의 사용자 요청 경로 복구 확인 값은 false다.")
    workloads = [item for item in bundle.evidence if item.kind == "workload_status"
                 and item.status == "available" and item.provenance.kind == "captured"][:20]
    if workloads:
        ready = sum(item.payload.get("phase") == "Running" and item.payload.get("ready") is True for item in workloads)
        facts.append({"statement": f"수집 당시 확보한 Pod 상태 {len(workloads)}개 중 {ready}개가 Running/Ready였다. 이 관측이 과거 CI 실패를 지우거나 현재 전체 서비스 복구를 입증하지는 않는다.",
                      "evidence_ids": [item.evidence_id for item in workloads]})
    ci_entries = [entry for entry in entries.values() if entry.ci_run_id or entry.time_field == "artifact_observed_at"]
    excluded = [entry for entry in ci_entries if entry.status == "stale"]
    for entry in excluded[:10]:
        try:
            observed, _ = normalize_source_time(entry.observed_raw)
            moment = _utc_text(observed)
        except (ValueError, TypeError):
            moment = "관측 시각 확인 불가"
        limitations.append(f"CI 자료 {entry.evidence_id}의 원래 관측은 {moment}이다. 현재 조사 범위 밖인 오래된 자료(stale)여서 최초 실패·자동 원복·원인 판단의 근거에서 제외했다.")
    if any(entry.status == "available" and entry.evidence_id not in {item.evidence_id for item, _ in valid} for entry in ci_entries):
        limitations.append("일부 CI 배포 자료의 단계·실행 출처 감사가 불완전하거나 내용과 일치하지 않아 해석에 사용하지 않았다.")
    missing = [{"source": item.source_ref,
                "reason": "관측 시각이 조사 범위 밖인 오래된 자료(stale)여서 판단에서 제외했다." if item.status == "stale" else item.missing_reason}
               for item in bundle.evidence if item.status != "available"]
    if len(missing) > 30:
        limitations.append("누락 목록은 30개까지만 표시하며 전체 목록은 증거 묶음과 수집 감사를 확인한다.")
    if len(excluded) > 10:
        limitations.append("오래된 CI 자료의 시각 설명은 10개까지만 표시하며 전체 목록은 수집 감사를 확인한다.")
    complete = audit is not None and audit.collection_status == "complete" and not audit.deadline_exceeded
    return AnalysisReport(
        schema_version=1, incident_id=request.incident_id, run_id=run_id, input_hash=bundle.bundle_hash,
        target=request.target, window=request.window, analysis_status="complete" if complete else "partial",
        diagnosis_status="suspected_cause" if pair else "insufficient_evidence", current_state="unknown",
        facts=facts, hypotheses=hypotheses, missing_evidence=missing[:30], recommended_checks=checks,
        suggested_actions=[], limitations=limitations,
        execution={"mode": "rules", "engine": ENGINE, "tool_calls": 0},
    )
