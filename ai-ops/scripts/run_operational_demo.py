"""실제 worker에 읽기 조사 한 건을 접수하고 본문 없는 운영 검증 결과를 저장한다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import uuid

from ai_ops.path_policy import cli_paths, write_private_json


CONTEXT = "arn:aws:eks:ap-northeast-2:716174522908:cluster/urlshortener"
IDENTITY = "system:serviceaccount:ai-ops:ai-ops-investigator"
TERMINAL = {"completed", "partial", "failed", "cancelled", "needs_review"}


def command(*args: str, allow_failure: bool = False) -> subprocess.CompletedProcess:
    """원문 오류나 연결 자격 증명을 결과 파일에 복사하지 않는다."""
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if result.returncode and not allow_failure:
        raise ValueError("운영 조회 명령 실패: " + args[0])
    return result


def kube_json(*args: str) -> dict:
    return json.loads(command("kubectl", *args, "-o", "json").stdout)


def cli(*args: str) -> dict:
    result = command("kubectl", "-n", "ai-ops", "exec", "ai-ops-0", "-c", "diagnostics",
                     "--", "python", "-m", "ai_ops.cli", *args, "--store", "/data/incidents")
    return json.loads(result.stdout)


def permissions() -> list[dict]:
    """쓰기나 Secret 조회는 실행하지 않고 인가 판정만 검사한다."""
    cases = [
        ("get", "deployments.apps/url-shortener-api", "url-shortener", True),
        ("list", "pods", "url-shortener", True),
        ("get", "applications.argoproj.io/url-shortener", "argocd", True),
        ("get", "secrets", "url-shortener", False),
        ("patch", "deployments.apps/url-shortener-api", "url-shortener", False),
        ("create", "pods/exec", "url-shortener", False),
        ("list", "pods", "kube-system", False),
    ]
    results = []
    for verb, resource, namespace, expected in cases:
        result = command("kubectl", "auth", "can-i", verb, resource, "-n", namespace,
                         "--as=" + IDENTITY, allow_failure=True)
        answer = result.stdout.strip()
        if answer not in {"yes", "no"} or result.returncode not in {0, 1}:
            raise ValueError("인가 조회 결과를 확인할 수 없습니다")
        allowed = answer == "yes"
        if allowed != expected:
            raise ValueError("검토한 조사 identity의 권한 범위가 다릅니다")
        results.append({"verb": verb, "resource": resource, "namespace": namespace,
                        "allowed": allowed})
    return results


def report_summary(job: dict) -> dict:
    """검증 보고서와 자료 본문은 PVC 안에서만 읽고 고정 메타데이터만 가져온다."""
    program = '''
import hashlib,json,sys
from collections import Counter
from pathlib import Path
from ai_ops.incidents import IncidentStore
from ai_ops.data_policy import ensure_external_model_allowed,ModelDataDenied
from ai_ops.contracts import canonical_json_bytes
from ai_ops.reports import format_kst
record=IncidentStore(Path('/data/incidents')).show(sys.argv[1],sys.argv[2])
report=record.report
assert report is not None and record.bundle is not None
try:
    ensure_external_model_allowed(record.bundle)
except ModelDataDenied:
    blocked=True
else:
    blocked=False
assert blocked and report.execution.mode=='rules'
assert all(a.execution_status=='not_executed' for a in report.suggested_actions)
audit=record.collection_audit
print(json.dumps({
    'record_status':record.metadata['status'],
    'analysis_status':report.analysis_status,
    'diagnosis_status':report.diagnosis_status,
    'current_state':report.current_state,
    'execution_mode':report.execution.mode,
    'report_sha256':hashlib.sha256(canonical_json_bytes(report.model_dump(mode='json'))).hexdigest(),
    'bundle_hash':record.bundle.bundle_hash,
    'provenance':dict(Counter(e.provenance.kind for e in record.bundle.evidence)),
    'evidence_statuses':dict(Counter(e.status for e in record.bundle.evidence)),
    'collection_status':audit.collection_status if audit else None,
    'collection_entries':[{'id':e.evidence_id,'status':e.status,'reason':e.reason} for e in audit.entries] if audit else [],
    'facts':len(report.facts),'hypotheses':len(report.hypotheses),
    'missing_evidence':len(report.missing_evidence),
    'all_actions_not_executed':True,'captured_external_model_blocked':blocked,
    'window_utc':record.bundle.window.model_dump(mode='json'),
    'window_end_kst':format_kst(record.bundle.window.end)
},ensure_ascii=False))
'''
    return json.loads(command("kubectl", "-n", "ai-ops", "exec", "ai-ops-0", "-c", "diagnostics",
                              "--", "python", "-c", program, job["incident_id"], job["run_id"]).stdout)


def evaluate(result: dict) -> None:
    """운영 리소스를 바꾸지 않고 영속 큐에 검증용 조사 한 건만 추가한다."""
    if command("kubectl", "config", "current-context").stdout.strip() != CONTEXT:
        raise ValueError("평가 대상 Kubernetes context가 다릅니다")
    pod = kube_json("-n", "ai-ops", "get", "pod", "ai-ops-0")
    statuses = pod["status"]["containerStatuses"]
    if len(statuses) != 2 or not all(item["ready"] for item in statuses):
        raise ValueError("AI worker와 수신기가 준비되지 않았습니다")
    config = kube_json("-n", "ai-ops", "get", "configmap", "ai-ops-config")["data"]
    runtime = json.loads(config["runtime.json"])
    if runtime["llm_enabled"] is not False:
        raise ValueError("현재 운영 평가는 외부 모델 비활성 상태만 지원합니다")
    app = kube_json("-n", "url-shortener", "get", "deployment", "url-shortener-api")
    result.update({"environment": "production", "identity": IDENTITY,
                   "images": [item["imageID"] for item in statuses],
                   "app_ready_replicas": app["status"].get("readyReplicas", 0),
                   "app_uid": app["metadata"]["uid"],
                   "app_generation": app["metadata"]["generation"],
                   "limits": runtime["limits"], "external_model_enabled": False,
                   "configured_budget_usd": {"per_incident": runtime["per_incident_budget_usd"],
                                             "daily": runtime["daily_budget_usd"]}})
    result["permissions"] = permissions()
    print("권한·모델 비활성 확인 완료", flush=True)
    request_id = "b6-demo-" + uuid.uuid4().hex
    arguments = ("queue-submit", "--bindings", "/etc/ai-ops/bindings.json", "--runtime",
                 "/etc/ai-ops/runtime.json", "--metric-queries", "/etc/ai-ops/metric-queries.json",
                 "--target", "url-shortener-production", "--question",
                 "현재 서비스 상태와 판단에 부족한 운영 자료를 확인해 주세요.",
                 "--request-id", request_id, "--route", "/items", "--operation", "select_all")
    # 질문은 고정 문장만 사용하며 비밀값과 시나리오 정답을 넣지 않는다.
    first = cli(*arguments)
    result["job_id"] = first["job_id"]
    print("PVC 큐에 조사 접수 완료", flush=True)
    again = cli(*arguments)
    if not again["duplicate"] or again["job_id"] != first["job_id"]:
        raise ValueError("접수 직후 동일 요청이 중복으로 처리되지 않았습니다")
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        job = cli("queue-show", "--job", first["job_id"])
        if job["state"] in TERMINAL:
            break
        time.sleep(3)
    else:
        raise ValueError("조사 완료 대기 시간이 초과됐습니다. 큐 상태를 확인하세요")
    if job["state"] not in {"completed", "partial"}:
        raise ValueError("worker가 보고서를 저장하지 못했습니다")
    result["job"] = job
    result["report"] = report_summary(job)
    print("보고서 저장과 본문 없는 요약 확인 완료", flush=True)
    duplicate = cli(*arguments)
    if not duplicate["duplicate"] or duplicate["job_id"] != job["job_id"] or duplicate["attempts"] != job["attempts"]:
        raise ValueError("완료 후 재접수가 추가 조사를 만들었습니다")
    result["duplicate_before_and_after"] = True
    command("kubectl", "-n", "ai-ops", "exec", "ai-ops-0", "-c", "diagnostics", "--",
            "python", "-m", "ai_ops.worker_runtime", "--probe")
    current = kube_json("-n", "url-shortener", "get", "deployment", "url-shortener-api")
    if current["metadata"]["uid"] != result["app_uid"] or current["metadata"]["generation"] != result["app_generation"]:
        raise ValueError("평가 도중 앱 Deployment가 변경됐습니다")
    result.update(status="passed", app_deployment_unchanged=True,
                  external_model_calls=0, external_model_cost_usd=0,
                  accuracy_measurement=False, baseline="단일 실제 읽기 조사이며 진단 정확도 평가가 아님")
    result["elapsed_seconds"] = time.monotonic() - result.pop("_started")


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 읽기 조사 1건·중복·권한·모델 차단의 본문 없는 운영 데모")
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    result = {"phase": "B-06", "schema_version": 1, "status": "failed", "store": "/data/incidents", "_started": time.monotonic(),
              "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        evaluate(result)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        result.pop("_started", None)
        result["failure_type"] = type(exc).__name__
        print("운영 데모 검증 실패. 결과 파일과 큐 상태를 확인하세요.")
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_private_json(args.output, result)
    print("운영 데모 결과: " + result["status"])
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
