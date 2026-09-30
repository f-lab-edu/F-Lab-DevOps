"""저장 증거만 읽는 규칙 기반 비교군. 모델·클러스터 호출은 없다."""

from __future__ import annotations

from ai_ops.contracts import AnalysisReport, EvidenceBundle, EvidenceItem, IncidentRequest


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
    ]
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
