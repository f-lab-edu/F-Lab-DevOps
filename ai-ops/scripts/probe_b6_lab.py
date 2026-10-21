"""실습 대상의 인가·연결·실제 수집을 확인하고 본문 없는 결과만 반출한다."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import uuid

from ai_ops.b6_lab import CONTEXT, NAMESPACE, PURPOSE
from ai_ops.path_policy import cli_paths, write_private_json


def command(*args, allow_denial=False, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode and not (allow_denial and result.returncode == 1):
        raise ValueError("실습 확인 명령이 실패했습니다")
    return result.stdout.strip()


def permissions():
    """실제 Secret 읽기·쓰기·다른 대상 조회를 실행하지 않고 인가만 확인한다."""
    cases = [("get", "deployments.apps/lab-api", NAMESPACE, True),
             ("list", "pods", NAMESPACE, True), ("get", "secrets", NAMESPACE, False),
             ("patch", "deployments.apps/lab-api", NAMESPACE, False),
             ("create", "pods/exec", NAMESPACE, False),
             ("list", "pods", "url-shortener", False),
             ("get", "applications.argoproj.io/url-shortener", "argocd", False)]
    results = []
    for verb, resource, namespace, expected in cases:
        answer = command("kubectl", "auth", "can-i", verb, resource, "-n", namespace,
                         "--as=system:serviceaccount:ai-ops-lab:lab-investigator", allow_denial=True)
        if answer not in {"yes", "no"} or (answer == "yes") != expected:
            raise ValueError("실습 수집기 권한 경계가 다릅니다")
        results.append({"verb": verb, "resource": resource, "namespace": namespace, "allowed": expected})
    return results


def probe():
    if command("kubectl", "config", "current-context") != CONTEXT:
        raise ValueError("실습 context가 다릅니다")
    namespace = json.loads(command("kubectl", "get", "namespace", NAMESPACE, "-o", "json"))
    if any(namespace["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items()):
        raise ValueError("실습 소유 label이 다릅니다")
    auth = permissions()
    # 운영자 exec는 실습 Pod에서만 사용하며 수집기에 exec 권한을 주지 않는다.
    api_program = '''
import json,os,urllib.request
import redis
assert os.environ['APP_ENV']=='practice'
assert os.environ['ENABLE_FAULT_INJECTION']=='true'
assert os.environ['DATABASE_URL']==os.environ['DATABASE_READ_URL']
assert redis.from_url(os.environ['REDIS_URL']).ping()
statuses={}
for path in ('/readyz','/items','/items/_db'):
    with urllib.request.urlopen('http://127.0.0.1:8000'+path,timeout=10) as response:
        statuses[path]=response.status
        if path=='/items/_db':
            roles=json.load(response)
            assert not roles['write']['in_recovery'] and not roles['read']['in_recovery']
assert all(status==200 for status in statuses.values())
print(json.dumps({'http':statuses,'redis_ping':True,'database_topology':'single_primary_no_replica'}))
'''
    api = json.loads(command("kubectl", "-n", NAMESPACE, "exec", "deployment/lab-api", "-c", "api",
                             "--", "python", "-c", api_program))
    collector_program = '''
import json,sys,time,urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode
from ai_ops.b_contracts import load_b_settings
from ai_ops.live_collectors import collect_snapshot
from ai_ops.incidents import IncidentStore
from ai_ops.data_policy import ensure_external_model_allowed,ModelDataDenied
settings=load_b_settings(*(Path('/etc/ai-ops')/name for name in ('bindings.json','runtime.json','metric-queries.json')))
binding=settings.bindings.bindings[0]
assert binding.target.namespace=='ai-ops-lab' and binding.target.environment=='practice'
assert settings.runtime.llm_enabled is False
url=settings.runtime.connections[binding.prometheus_connection].base_url+'/api/v1/query?'+urlencode({'query':'up{namespace="ai-ops-lab",service="lab-api"}'})
with urllib.request.urlopen(url,timeout=10) as response:
    metrics=json.load(response)
samples=metrics['data']['result']
assert metrics['status']=='success' and len(samples)==1 and float(samples[0]['value'][1])==1
quality={}
for name,expression in settings.metrics.quality_expressions.items():
    expression=expression.replace('@namespace@',binding.target.namespace).replace('@service@',binding.target.service)
    with urllib.request.urlopen(settings.runtime.connections[binding.prometheus_connection].base_url+'/api/v1/query?'+urlencode({'query':expression}),timeout=10) as response:
        result=json.load(response)
    assert result['status']=='success' and len(result['data']['result'])==1
    quality[name]=float(result['data']['result'][0]['value'][1])
quality['last_scrape_age_seconds']=time.time()-quality.pop('last_scrape')
metric_ready=(quality['up']==1 and quality['scrape_health']==1 and quality['scrape_samples']>=settings.runtime.scrape.minimum_samples
              and 0<=quality['last_scrape_age_seconds']<=settings.runtime.scrape.max_age_seconds)
snapshot=collect_snapshot(settings,binding.target,route='/items',operation='select_all')
store=IncidentStore(Path('/data/incidents'))
record=store.investigate_snapshot(snapshot=snapshot,settings=settings,question='실습 대상에서 관측 가능한 자료와 부족한 자료를 확인해 주세요.',request_id=sys.argv[1])
record=store.show(record.incident_id,record.run_id)
report=record.report
assert report.execution.engine in ('rules-captured-v2','rules-captured-v3') and report.execution.mode=='rules'
assert snapshot.bundle.target.namespace=='ai-ops-lab'
try:
    ensure_external_model_allowed(snapshot.bundle)
except ModelDataDenied:
    blocked=True
else:
    blocked=False
assert blocked
available={e.evidence_id for e in snapshot.bundle.evidence if e.status=='available'}
assert any(e.kind=='workload_status' and e.status=='available' for e in snapshot.bundle.evidence)
assert all(set(f.evidence_ids)<=available for f in report.facts)
assert all(a.execution_status=='not_executed' for a in report.suggested_actions)
print(json.dumps({'prometheus_up':1,'scrape_quality':quality,'ready_for_metric_scenarios':metric_ready,
    'incident_id':record.incident_id,'run_id':record.run_id,'bundle_hash':snapshot.bundle.bundle_hash,
    'engine':report.execution.engine,'analysis_status':report.analysis_status,'diagnosis_status':report.diagnosis_status,'current_state':report.current_state,
    'evidence_statuses':dict(Counter(e.status for e in snapshot.bundle.evidence)),
    'collection_entries':[{'id':e.evidence_id,'status':e.status,'reason':e.reason} for e in snapshot.audit.entries],
    'facts':len(report.facts),'hypotheses':len(report.hypotheses),'fact_citations_available':True,
    'external_model_blocked':blocked,'source':'captured','store_persistence':'emptyDir'}))
'''
    collected = json.loads(command("kubectl", "-n", NAMESPACE, "exec", "deployment/lab-collector", "-c", "collector",
                                   "--", "python", "-c", collector_program, "b6-lab-readiness-" + uuid.uuid4().hex))
    return {"status": "passed", "scope": "lab_readiness_not_fault_accuracy", "namespace": NAMESPACE,
            "verified_at": datetime.now(timezone.utc).isoformat(), "permissions": auth,
            "api": api, "collection": collected, "external_model_calls": 0,
            "scenarios_executed": []}


def main():
    parser = argparse.ArgumentParser(description="B-06 실습 연결·권한·규칙 수집 1건 검증")
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    try:
        result = probe()
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
        result = {"status": "failed", "namespace": NAMESPACE, "reason": "lab_readiness_check_failed"}
    write_private_json(args.output, result)
    print("실습 연결·권한·수집 확인: " + result["status"])
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
