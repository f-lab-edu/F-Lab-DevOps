"""C-02 활성 GitOps 소스 실습의 읽기 전용 시작 조건을 검증한다."""

from __future__ import annotations

from ai_ops.b6_ci import validate_sources
from ai_ops.c_live_preflight import assess_live_preflight


def prepare_source_trial(application: dict, deployment: dict, lease: dict,
                         baseline: bytes, failure: bytes, *, baseline_revision: str,
                         failure_revision: str, good_present: bool,
                         bad_absent: bool) -> dict:
    """정상 기준선과 실패 소스만 확인하고 적용 권한이 없는 계획을 반환한다."""
    import json
    import re

    if (not all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{40}", value)
                for value in (baseline_revision, failure_revision))
            or baseline_revision == failure_revision):
        raise ValueError("실습 커밋 SHA가 유효하지 않습니다")
    good, bad = validate_sources(json.loads(baseline), json.loads(failure))
    preflight = assess_live_preflight(application, deployment, lease, baseline)
    if (preflight["base_sha"] != baseline_revision
            or preflight["blockers"] != ["already_recovered"]
            or deployment["spec"]["template"]["spec"]["containers"][0]["image"] != good
            or deployment["status"].get("readyReplicas") != 1
            or not good_present or not bad_absent):
        raise ValueError("C-02 실습의 정상 기준선 또는 이미지 출처가 유효하지 않습니다")
    return {"schema_version": 1, "target": preflight["target"],
            "application_uid": application["metadata"]["uid"],
            "deployment_uid": deployment["metadata"]["uid"],
            "baseline_revision": baseline_revision,
            "failure_revision": failure_revision,
            "normal_image": good, "failure_image": bad,
            "maximum_failure_seconds": 300,
            "independent_rollback_ready": False,
            "active_source_capture_ready": False,
            "ready_to_inject": False,
            "cluster_or_github_write_performed": False}
