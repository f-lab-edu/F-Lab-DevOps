"""저장 증거만 읽는 규칙 기반 비교군. 모델·클러스터 호출은 없다."""

from __future__ import annotations

import re

from ai_ops.contracts import AnalysisReport, EvidenceBundle, EvidenceItem, Fact, Hypothesis, IncidentRequest


RULES_VERSION = "rules-v1"


def _metric(bundle: EvidenceBundle, query_id: str, outcome: str) -> tuple[float | None, str | None]:
    for item in bundle.evidence:
        if item.status != "available" or item.kind != "metric" or item.payload["query_id"] != query_id:
            continue
        values = [sample["value"] for sample in item.payload["samples"] if sample["outcome"] == outcome]
        if values:
            return float(sum(values)), item.evidence_id
    return None, None


def _first(bundle: EvidenceBundle, kind: str, field: str, expected: str) -> EvidenceItem | None:
    return next((item for item in bundle.evidence if item.status == "available"
                 and item.kind == kind and item.payload.get(field) == expected), None)


def _log_contains(bundle: EvidenceBundle, fragment: str) -> EvidenceItem | None:
    return next((item for item in bundle.evidence if item.status == "available"
                 and item.kind == "log" and any(fragment.lower() in line.lower()
                 for line in item.payload.get("lines", []))), None)


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else format(value, ".3g")


def analyze_rules(request: IncidentRequest, bundle: EvidenceBundle, run_id: str) -> AnalysisReport:
    """제공된 증거 ID로만 사실·가설을 구성하고 부족한 자료를 명시한다."""
    facts: list[dict] = []
    hypotheses: list[dict] = []
    checks: list[str] = []
    limitations = ["로컬 저장 증거에 대한 규칙 기반 요약이며 AI 분석이나 실시간 상태 확인이 아니다."]
    missing = [
        {"source": item.source_ref, "reason": item.missing_reason}
        for item in bundle.evidence if item.status != "available"
    ][:30]
    if sum(item.status != "available" for item in bundle.evidence) > 30:
        limitations.append("누락 항목은 보고서에 최대 30개만 표시하며 전체 목록은 입력 증거와 수집 감사에서 확인한다.")
    diagnosis = "insufficient_evidence"
    state = "unknown"

    failed = _first(bundle, "deployment", "status", "failed")
    recovered = _first(bundle, "deployment", "status", "healthy_after_rollback")
    image_event = _first(bundle, "event", "reason", "ImagePullBackOff")
    progressing = _first(bundle, "deployment", "status", "progressing")
    cache_error, cache_error_id = _metric(bundle, "cache_outcomes", "error")
    cache_miss, cache_miss_id = _metric(bundle, "cache_outcomes", "miss")
    consistency, consistency_id = _metric(bundle, "cache_outcomes", "consistency_primary")
    http_ratio, http_ratio_id = _metric(bundle, "http_error_ratio", "ratio")
    http_requests, http_requests_id = _metric(bundle, "http_error_ratio", "requests")
    db_p99, db_p99_id = _metric(bundle, "db_latency", "p99")
    redis_log = _log_contains(bundle, "RedisError")
    marker_log = _log_contains(bundle, "recent write marker")
    healthy_workload = next((item for item in bundle.evidence if item.status == "available"
                             and item.kind == "workload_status" and item.payload.get("ready") is True
                             and item.payload.get("phase") == "Running"), None)

    if failed and recovered and failed.observed_at < recovered.observed_at:
        facts.extend([
            {"statement": "이전 배포 시도는 실패 상태로 기록되었다.", "evidence_ids": [failed.evidence_id]},
            {"statement": "그 뒤 롤백된 배포가 정상 상태로 기록되었다.", "evidence_ids": [recovered.evidence_id]},
        ])
        hypotheses.append({"cause": "배포 변경 과정의 실패가 사건의 원인 후보이며 이후 롤백으로 복구되었을 가능성이 있다.",
                           "supporting_evidence_ids": [failed.evidence_id, recovered.evidence_id]})
        checks.append("실패 이미지의 존재·인증과 정확한 GitOps revision, 롤백 뒤 사용자 경로를 확인한다.")
        limitations.append("이전 이미지가 실제로 정상 버전인지와 현재 라이브 상태는 검증하지 않았다.")
        diagnosis, state = "suspected_cause", "recovered"
    elif image_event and progressing:
        facts.extend([
            {"statement": "배포 변경이 진행 중인 상태로 기록되었다.", "evidence_ids": [progressing.evidence_id]},
            {"statement": "ImagePullBackOff 이벤트가 관측되었다.", "evidence_ids": [image_event.evidence_id]},
        ])
        hypotheses.append({"cause": "변경된 이미지를 가져오지 못해 배포가 진행되지 않았을 가능성이 있다.",
                           "supporting_evidence_ids": [progressing.evidence_id, image_event.evidence_id]})
        checks.append("이미지 태그의 존재, 레지스트리 접근·인증, 해당 Pod의 현재 상태를 확인한다.")
        diagnosis = "suspected_cause"
    elif cache_error is not None and cache_error > 0:
        facts.append({"statement": f"캐시 error 결과가 {_number(cache_error)}건 관측되었다.",
                      "evidence_ids": [cache_error_id]})
        supports = [cache_error_id]
        if redis_log:
            facts.append({"statement": "캐시 조회 로그에 RedisError가 기록되었다.",
                          "evidence_ids": [redis_log.evidence_id]})
            supports.append(redis_log.evidence_id)
        hypotheses.append({"cause": "캐시 조회 또는 데이터 처리 중 오류가 있었을 가능성이 있다.",
                           "supporting_evidence_ids": supports})
        checks.append("캐시 설정 의도, 예외 종류, 사용자 요청의 DB fallback·지연을 확인한다.")
        limitations.append("error만으로 Redis 네트워크 단절이나 서버 장애를 확정할 수 없다.")
        diagnosis = "suspected_cause"
    elif consistency is not None and consistency > 0 and marker_log:
        facts.extend([
            {"statement": f"consistency_primary 결과가 {_number(consistency)}건 관측되었다.",
             "evidence_ids": [consistency_id]},
            {"statement": "최근 쓰기 marker가 활성화된 로그가 기록되었다.",
             "evidence_ids": [marker_log.evidence_id]},
        ])
        checks.append("쓰기 시각과 marker 유효 구간, 이후 캐시 경로를 대조한다.")
        limitations.append("Primary 조회는 정상 일관성 처리일 수 있으며 현재 서비스 상태는 별도 확인이 필요하다.")
        diagnosis = "no_incident"
    elif cache_miss is not None and cache_miss > 0 and cache_error == 0:
        facts.extend([
            {"statement": f"캐시 miss가 {_number(cache_miss)}건 관측되었다.", "evidence_ids": [cache_miss_id]},
            {"statement": "동일 입력의 캐시 error 값은 0이다.", "evidence_ids": [cache_error_id]},
        ])
        checks.append("cold key·TTL·세대 변경과 Redis 접속 상태, 사용자 경로를 확인한다.")
        limitations.append("miss 증가만으로 장애를 단정하지 않는다.")
        diagnosis = "no_incident"
    elif healthy_workload and http_ratio == 0 and http_requests is not None and http_requests > 0:
        facts.extend([
            {"statement": "관측된 workload는 Running/Ready 상태다.", "evidence_ids": [healthy_workload.evidence_id]},
            {"statement": f"요청 {_number(http_requests)}건에서 HTTP 오류 비율 0이 기록되었다.",
             "evidence_ids": [http_ratio_id, http_requests_id]},
        ])
        checks.append("동일 시간대의 사용자 경로와 추가 오류 지표를 확인한다.")
        limitations.append("정상 판정은 제공된 관측 시간 범위에 한정된다.")
        diagnosis, state = "no_incident", "healthy"
    elif db_p99 is not None:
        facts.append({"statement": f"앱 측 DB 지연 P99 값이 {_number(db_p99)}초로 기록되었다.",
                      "evidence_ids": [db_p99_id]})
        checks.append("기준 지연, 표본 수, ORM·캐시·실습 지연과 DB 서버 지표를 분리해 확인한다.")
        limitations.append("앱 측 지연만으로 SQL 또는 RDS 서버 원인을 확정할 수 없다.")
    else:
        checks.append("누락된 로그·이벤트·지표와 대상 시간 범위를 먼저 확보한다.")

    if missing:
        limitations.append("일부 증거가 누락·거부·오래됨 상태이므로 비어 있는 값을 0으로 해석하지 않는다.")
    return AnalysisReport(
        schema_version=1, incident_id=request.incident_id, run_id=run_id,
        input_hash=bundle.bundle_hash, target=request.target, window=request.window,
        analysis_status="complete", diagnosis_status=diagnosis, current_state=state,
        facts=facts, hypotheses=hypotheses, missing_evidence=missing,
        recommended_checks=checks, suggested_actions=[], limitations=limitations,
        execution={"mode": "rules", "engine": RULES_VERSION, "tool_calls": 0},
    )


def analyze_snapshot_rules_v1(request: IncidentRequest, bundle: EvidenceBundle, run_id: str, audit=None) -> AnalysisReport:
    """captured 묶음의 출처·전체 Pod·부분 수집을 구분하는 별도 규칙 버전이다."""
    report = analyze_rules(request, bundle, run_id)
    report.execution.engine = "rules-captured-v1"
    report.limitations[0] = "수집 시점의 captured 증거 묶음에 대한 규칙 요약이다. 이후 상태 변화는 관측하지 않았으며 AI 분석·변경 실행은 하지 않았다."
    report.limitations.append("지표 increase는 추정 증가량이므로 소수 값을 정수 사건 수로 확정하지 않는다.")
    report.limitations.append("로그·이벤트 자유 문장을 진단 표식으로 축약했으므로 원문 정보가 일부 생략됐다.")
    workloads = [item for item in bundle.evidence if item.kind == "workload_status"]
    complete = audit is not None and audit.collection_status == "complete" and not audit.deadline_exceeded
    entries = {entry.evidence_id: entry for entry in audit.entries} if audit else {}
    available = [item for item in bundle.evidence if item.status == "available"]
    metrics = {item.payload.get("query_id") for item in available if item.kind == "metric"}
    expected = audit.workload_expected_pods if audit else None
    pod_ids = {entries[item.evidence_id].resource_uid for item in workloads if item.evidence_id in entries}
    deployment_ids = {entries[item.evidence_id].deployment_uid for item in workloads if item.evidence_id in entries}
    cache_error, _ = _metric(bundle, "cache_outcomes", "error")
    cache_unavailable, _ = _metric(bundle, "cache_outcomes", "unavailable")
    api_p95, _ = _metric(bundle, "api_latency", "p95")
    db_p99, _ = _metric(bundle, "db_latency", "p99")
    # 현 앱 경보 기준(API 1초·DB 0.5초)과 충돌하는 자료가 있으면 정상으로 확대하지 않는다.
    normal_metrics = (cache_error == 0 and cache_unavailable == 0 and api_p95 is not None and api_p95 < 1
                      and db_p99 is not None and db_p99 < 0.5 and _log_contains(bundle, "RedisError") is None)
    healthy_scope = (
        complete and normal_metrics and expected is not None and 0 < expected <= 12
        and expected == len(workloads) == len(pod_ids)
        and None not in pod_ids and len(deployment_ids) == 1 and None not in deployment_ids
        and {"workload_status", "log", "event", "deployment", "config_summary", "metric"}.issubset({item.kind for item in available})
        and {"cache_outcomes", "cache_hit_ratio", "http_error_ratio", "api_latency", "db_latency"}.issubset(metrics)
        and all(item.status == "available" and item.payload.get("ready") is True
                and item.payload.get("phase") == "Running" and entries[item.evidence_id].replicaset_uid
                and entries[item.evidence_id].replicaset_resource_version for item in workloads)
    )
    if report.current_state == "healthy":
        workload_fact = next(fact for fact in report.facts if fact.statement == "관측된 workload는 Running/Ready 상태다.")
        if healthy_scope:
            workload_fact.statement = f"등록 Deployment의 기대 Pod {expected}개 전체가 관측 시점에 Running/Ready 상태다."
            workload_fact.evidence_ids = [item.evidence_id for item in workloads]
        else:
            workload_fact.statement = "확보한 Pod 상태에서 Running/Ready 관측이 있지만 전체 서비스 정상은 확인하지 못했다."
            report.current_state = "unknown"
            report.limitations.append("전체 Pod 수·소유 관계·필수 출처·수집 완료 조건이 모두 확인되지 않아 전체 건강을 판정하지 않았다.")
    elif report.current_state == "recovered":
        report.current_state = "unknown"
        report.limitations.append("이전 복구 기록은 현재 전체 Pod 건강을 입증하지 않아 최신 상태를 확인 불가로 둔다.")
    reasons = {
        "permission_denied": "자료 조회 권한이 거부됐다.", "missing_history": "CI 실패·롤백 전후 또는 배포 이력이 확보되지 않았다.",
        "empty_response": "조회 결과가 없어 수치 0으로 해석하지 않았다.", "no_traffic": "평가 구간의 요청·표본이 0건이어서 비율·지연을 판단하지 않았다.",
        "scrape_unavailable": "지표 수집 상태·최근성·표본 수 조건을 충족하지 못했다.",
        "truncated": "수집 상한 또는 잘린 응답 때문에 자료 전체를 확보하지 못했다.",
        "scope_mismatch": "등록 대상이나 객체 소유 관계가 일치하지 않았다.",
        "revision_changed": "수집 도중 Deployment 또는 Pod가 변경됐다.",
        "deadline_exceeded": "수집 기한이 지났다.", "timeout": "조회 시간이 초과됐다.",
        "invalid_payload": "자료 형식·허용 필드·진단 표식 검사를 통과하지 못했다.",
    }
    for missing in report.missing_evidence:
        missing.reason = reasons.get(missing.reason, missing.reason)
    if audit is not None and not complete:
        report.analysis_status = "partial"
        report.limitations.append(f"수집 상태는 {audit.collection_status}이며 일부 또는 전체 출처가 부족한 규칙 부분 결과다.")
    if audit is None:
        report.limitations.append("수집 감사가 없는 captured 입력이므로 전체 Pod 범위와 수집 완전성을 검증하지 못했다.")
    return report


def analyze_snapshot_rules_v2(request: IncidentRequest, bundle: EvidenceBundle, run_id: str, audit=None) -> AnalysisReport:
    """v1의 판단 경계는 유지하고 일부 자료만 있어도 확인한 관측을 설명한다."""
    report = analyze_snapshot_rules_v1(request, bundle, run_id, audit)
    report.execution.engine = "rules-captured-v2"
    available = [item for item in bundle.evidence if item.status == "available"]
    cited = {key for fact in report.facts for key in fact.evidence_ids}

    def append(statement: str, items: list[EvidenceItem]) -> None:
        # 입력 전체를 정상으로 일반화하지 않고 인용 가능한 최대 20개만 요약한다.
        selected = items[:20]
        if selected and len(report.facts) < 100 and not all(item.evidence_id in cited for item in selected):
            report.facts.append(Fact(statement=statement, evidence_ids=[item.evidence_id for item in selected]))
            cited.update(item.evidence_id for item in selected)

    workloads = [item for item in available if item.kind == "workload_status"][:20]
    if workloads:
        ready = sum(item.payload.get("phase") == "Running" and item.payload.get("ready") is True for item in workloads)
        append(f"확보한 Pod 상태 {len(workloads)}개 중 {ready}개가 관측 시점에 Running/Ready였다. 전체 서비스 정상 여부와는 구분한다.", workloads)
    statuses = {
        "healthy": "정상 동기화 상태", "progressing": "배포 진행 상태", "failed": "실패 상태",
        "healthy_after_rollback": "롤백 이후 정상 기록", "unknown": "판단 불가 상태",
    }
    for status, label in statuses.items():
        deployments = [item for item in available if item.kind == "deployment" and item.payload.get("status") == status][:20]
        append(f"인용한 배포 자료 {len(deployments)}개는 {label}로 기록됐다. 사용자 요청 경로의 정상 여부를 직접 입증하지는 않는다.", deployments)
    for item in available:
        if item.kind != "metric" or item.payload.get("query_id") != "cache_outcomes":
            continue
        samples = item.payload["samples"]
        values = ", ".join(f"{sample['outcome']}={float(sample['value']):.3g}" for sample in samples)
        append(f"캐시 결과의 추정 증가량 집계값은 {values}였다. 표본이 없는 다른 지표를 0으로 채우거나 연결 정상으로 해석하지 않는다.", [item])
    reasons = {
        "no_diagnostic_fields": "로그 응답과 시각은 유효하지만 허용된 진단 표식이 없어 본문을 저장하지 않았다. 오류가 없다는 뜻은 아니다.",
        "invalid_log_encoding": "로그 응답이 UTF-8 검사를 통과하지 못해 본문을 저장하지 않았다.",
        "invalid_log_timestamp": "로그 행의 시각 형식이 유효하지 않아 본문을 저장하지 않았다.",
    }
    for missing in report.missing_evidence:
        missing.reason = reasons.get(missing.reason, missing.reason)
    return report


def analyze_snapshot_rules_v3(request: IncidentRequest, bundle: EvidenceBundle, run_id: str, audit=None) -> AnalysisReport:
    """Pod 소유·동일 이미지·근접 시각이 맞는 native 이미지 실패만 후보로 설명한다."""
    report = analyze_snapshot_rules_v2(request, bundle, run_id, audit)
    report.execution.engine = "rules-captured-v3"
    if audit is None:
        return report
    if (len(report.facts) >= 100 or len(report.hypotheses) >= 3
            or len(report.recommended_checks) >= 30 or len(report.limitations) >= 30):
        return report
    entries = {entry.evidence_id: entry for entry in audit.entries}
    available = [item for item in bundle.evidence if item.status == "available"
                 and item.provenance.kind == "captured" and item.evidence_id in entries
                 and entries[item.evidence_id].status == "available"]
    deployments = [item for item in available if item.kind == "deployment"
                   and entries[item.evidence_id].resource_uid and item.payload.get("image")]
    for pod in available:
        if (pod.kind != "workload_status" or pod.payload.get("phase") != "Pending"
                or pod.payload.get("ready") is not False):
            continue
        owner = entries[pod.evidence_id]
        if not all((owner.resource_uid, owner.resource_version, owner.deployment_uid,
                    owner.replicaset_uid, owner.replicaset_resource_version)):
            continue
        deployment = next((item for item in deployments
            if entries[item.evidence_id].resource_uid == owner.deployment_uid
            and item.payload["image"] == pod.payload.get("image")
            and abs((item.observed_at - pod.observed_at).total_seconds()) <= 120), None)
        if deployment is None:
            continue
        for event in available:
            if event.kind != "event" or event.payload.get("type") != "Warning":
                continue
            reason = event.payload.get("reason")
            if reason not in {"Failed", "BackOff", "ImagePullBackOff", "ErrImagePull"}:
                continue
            marker = re.search(r"(?<![\w])(?:ImagePullBackOff|ErrImagePull)(?![\w])", event.payload.get("message", ""))
            if reason not in {"ImagePullBackOff", "ErrImagePull"} and marker is None:
                continue
            if event.payload.get("involved_uid") != owner.resource_uid:
                continue
            # 과거의 다른 사건이나 Pod 관측 후 뒤늦게 발생한 이벤트를 섞지 않는다.
            lag = (pod.observed_at - event.observed_at).total_seconds()
            if not 0 <= lag <= 120:
                continue
            citations = [pod.evidence_id, event.evidence_id, deployment.evidence_id]
            report.facts.append(Fact(statement="등록 Deployment와 같은 이미지를 사용하는 Pending/비준비 Pod에 근접 시각의 이미지 취득 실패 이벤트가 연결됐다.",
                                     evidence_ids=citations))
            report.hypotheses.insert(0, Hypothesis(cause="관측 Pod의 이미지 취득 실패로 컨테이너가 시작되지 못했을 가능성이 있다.",
                                                  supporting_evidence_ids=citations))
            report.diagnosis_status = "suspected_cause"
            report.current_state = "unknown"
            report.recommended_checks.insert(0, "같은 시각의 이미지 태그·digest 존재 여부와 레지스트리 접근·인증을 구분해 확인한다.")
            report.limitations.append("이미지 오류 표식은 태그 부재·인증·네트워크 원인을 구분하지 못하며, 현재 전체 서비스 장애나 복구 완료를 확정하지 않는다.")
            return report
    return report


def analyze_snapshot_rules(request: IncidentRequest, bundle: EvidenceBundle, run_id: str, audit=None) -> AnalysisReport:
    """CI 출처의 배포 이력은 전용 규칙으로 분리하고 기존 v3 보고서는 보존한다."""
    from ai_ops.ci_rules import analyze_ci_snapshot, has_ci_history

    if has_ci_history(bundle, audit):
        return analyze_ci_snapshot(request, bundle, run_id, audit)
    return analyze_snapshot_rules_v3(request, bundle, run_id, audit)
