"""고정 실습 대상의 캐시·DB 지연 사례를 제한된 요청으로 재현하고 복구한다."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import subprocess
import time
import uuid

from ai_ops.b6_lab import CONTEXT, NAMESPACE, PURPOSE
from ai_ops.b6_coordination import require_manual_deployment


def utc_now():
    return datetime.now(timezone.utc).isoformat()


# HTTP·캐시·DB 접근은 실습 앱 Pod 안에서만 실행한다. 원문·암호를 출력하지 않는다.
APP_PROGRAM = r'''
import json,os,re,sys,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
import redis
from prometheus_client.parser import text_string_to_metric_families
assert os.environ['APP_ENV']=='practice' and os.environ['ENABLE_FAULT_INJECTION']=='true'
assert urlsplit(os.environ['DATABASE_URL']).hostname=='lab-postgres.ai-ops-lab.svc'
assert os.environ['DATABASE_URL']==os.environ['DATABASE_READ_URL']
assert urlsplit(os.environ['REDIS_URL']).hostname=='lab-redis.ai-ops-lab.svc'
action,run_id=sys.argv[1:]
assert re.fullmatch(r'[a-f0-9]{32}',run_id)
path=Path('/tmp/b6-owned-'+run_id+'.json')
name='b6-scenario-'+run_id
base='http://127.0.0.1:8000'
cache=redis.from_url(os.environ['REDIS_URL'],decode_responses=True,socket_timeout=3,socket_connect_timeout=3)
calls=0
def utc_stamp():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def request(method,url,data=None,headers=None):
    global calls
    calls+=1
    payload=json.dumps(data).encode() if data is not None else None
    req=Request(base+url,data=payload,method=method,headers={'Content-Type':'application/json',**(headers or {})})
    try:
        with urlopen(req,timeout=12) as response:
            body=response.read(262144)
            return response.status,json.loads(body) if body else None
    except HTTPError as exc:
        return exc.code,None
def owned():
    value=json.loads(path.read_text())
    assert value['name']==name
    return value
def metric_snapshot():
    global calls
    calls+=1
    with urlopen(base+'/metrics/',timeout=8) as response:
        raw=response.read(262144).decode()
    values={}
    db={}
    for family in text_string_to_metric_families(raw):
        for sample in family.samples:
            if sample.name=='cache_operation_total' and sample.labels.get('endpoint')=='get_item':
                assert sample.value>=0 and sample.value.is_integer()
                values[sample.labels['result']]=int(sample.value)
            if sample.labels.get('operation')=='select_one' and sample.name in ('db_query_latency_seconds_count','db_query_latency_seconds_sum'):
                db['count' if sample.name.endswith('_count') else 'seconds']=sample.value
    return {'cache':values,'db':db}
def counters():
    return metric_snapshot()['cache']
result={}
if action=='create':
    assert not path.exists()
    # 응답을 잃어도 고유 이름으로 자기 데이터만 찾아 복구할 수 있게 먼저 기록한다.
    path.write_text(json.dumps({'name':name}))
    path.chmod(0o600)
    status,body=request('POST','/items',{'name':name,'description':'B6 isolated disposable test'})
    assert status==201 and body['name']==name and type(body['id']) is int
    path.write_text(json.dumps({'name':name,'id':body['id']}))
    result={'created':True,'item_id':body['id'],'redis_ping':bool(cache.ping())}
elif action in ('cold','hit','fault-read'):
    item=owned()['id']
    if action=='cold':
        assert cache.ping()
        marker='item:'+str(item)+':primary-read'
        deadline=time.monotonic()+10
        while cache.exists(marker):
            if time.monotonic()>=deadline: raise ValueError('marker did not expire')
            time.sleep(0.2)
        generation=cache.get('item:'+str(item)+':version') or '0'
        cache.delete('item:'+str(item)+':v'+generation)
        result['marker_absent']=True
    started_at=utc_stamp()
    before=counters()
    request_started_at=utc_stamp()
    status,body=request('GET','/items/'+str(item))
    request_finished_at=utc_stamp()
    assert status==200 and body['id']==item and body['name']==name
    after=counters()
    result.update(http_status=status,response_item_id=body['id'],
        started_at=started_at,finished_at=utc_stamp(),
        request_started_at=request_started_at,request_finished_at=request_finished_at,
        counters_before=before,counters_after=after)
elif action=='ping':
    result={'redis_ping':bool(cache.ping())}
elif action=='health':
    statuses={}
    for route in ('/readyz','/items'):
        status,_=request('GET',route)
        assert status==200
        statuses[route]=status
    assert cache.ping()
    result={'http':statuses,'redis_ping':True}
elif action in ('delay-baseline','delay-read','delay-recovered'):
    item=owned()['id']
    assert os.environ['MAX_FAULT_DELAY_MS']=='1000'
    assert cache.ping()
    before=metric_snapshot()
    delay=750 if action=='delay-read' else 0
    # 캐시 hit로 DB 지연 측정이 생략되지 않게 실습 요청에서만 우회한다.
    headers={'X-Week12-Bypass-Cache':'true'}
    if delay: headers['X-Week12-Read-Delay-Ms']=str(delay)
    started=time.perf_counter()
    status,body=request('GET','/items/'+str(item),headers=headers)
    elapsed=time.perf_counter()-started
    assert status==200 and body['id']==item and body['name']==name
    after=metric_snapshot()
    result={'http_status':status,'elapsed_seconds':elapsed,'delay_requested_ms':delay,
            'delay_header_sent':bool(delay),'bypass_header_sent':True,
            'counters_before':before['cache'],'counters_after':after['cache'],
            'db_before':before['db'],'db_after':after['db']}
elif action=='cleanup':
    if path.exists():
        value=owned()
        # 생성 응답이 끊긴 경우에도 자기 고유 이름에 해당하는 행만 찾는다.
        if 'id' not in value:
            from app.core.database import WriteSession
            from app.models.item import Item
            with WriteSession() as db:
                rows=db.query(Item).filter(Item.name==name).limit(2).all()
                assert len(rows)<=1
                if rows: value['id']=rows[0].id
        if 'id' in value:
            status,body=request('GET','/items/'+str(value['id']))
            assert status==404 or (status==200 and body['name']==name)
            if status==200:
                status,_=request('DELETE','/items/'+str(value['id']))
                assert status==204
            status,_=request('GET','/items/'+str(value['id']))
            assert status==404
        path.unlink()
    status,_=request('GET','/readyz')
    assert status==200 and cache.ping()
    result={'owned_data_removed':True,'http_status':status,'redis_ping':True}
else:
    raise ValueError('unknown fixed lab action')
result['http_calls']=calls
print(json.dumps(result))
'''


COLLECT_PROGRAM = r'''
import json,sys
from collections import Counter
from pathlib import Path
from ai_ops.b_contracts import load_b_settings
from ai_ops.live_collectors import collect_snapshot
from ai_ops.incidents import IncidentStore
from ai_ops.data_policy import ensure_external_model_allowed,ModelDataDenied
settings=load_b_settings(*(Path('/etc/ai-ops')/name for name in ('bindings.json','runtime.json','metric-queries.json')))
binding=settings.bindings.bindings[0]
assert binding.target.namespace=='ai-ops-lab' and binding.target.environment=='practice'
assert settings.runtime.llm_enabled is False
snapshot=collect_snapshot(settings,binding.target,route='/items/{item_id}',operation='select_one')
store=IncidentStore(Path('/data/incidents'))
record=store.investigate_snapshot(snapshot=snapshot,settings=settings,question='현재 요청 처리와 캐시 관측을 확인하고 부족한 자료를 설명해 주세요.',request_id=sys.argv[1])
record=store.show(record.incident_id,record.run_id)
report=record.report
assert report.execution.mode=='rules' and report.execution.engine in ('rules-captured-v2','rules-captured-v3')
available={item.evidence_id for item in snapshot.bundle.evidence if item.status=='available'}
assert all(set(f.evidence_ids)<=available for f in report.facts)
assert all(a.execution_status=='not_executed' for a in report.suggested_actions)
try:
    ensure_external_model_allowed(snapshot.bundle)
except ModelDataDenied:
    blocked=True
else:
    blocked=False
assert blocked
metrics={item.payload['query_id']:item.payload['samples'] for item in snapshot.bundle.evidence if item.status=='available' and item.kind=='metric'}
print(json.dumps({'incident_id':record.incident_id,'run_id':record.run_id,'bundle_hash':snapshot.bundle.bundle_hash,
    'engine':report.execution.engine,'analysis_status':report.analysis_status,'diagnosis_status':report.diagnosis_status,
    'current_state':report.current_state,'facts':len(report.facts),'hypotheses':len(report.hypotheses),
    'hypothesis_evidence_ids':[h.supporting_evidence_ids for h in report.hypotheses],
    'fact_evidence_ids':[f.evidence_ids for f in report.facts],
    'workload_states':[{'id':item.evidence_id,'phase':item.payload.get('phase'),'ready':item.payload.get('ready')}
        for item in snapshot.bundle.evidence if item.status=='available' and item.kind=='workload_status'],
    'event_reasons':[{'id':item.evidence_id,'reason':item.payload.get('reason'),
        'image_failure_marker':any(token in item.payload.get('message','') for token in ('ImagePullBackOff','ErrImagePull'))}
        for item in snapshot.bundle.evidence if item.status=='available' and item.kind=='event'],
    'evidence_statuses':dict(Counter(e.status for e in snapshot.bundle.evidence)),
    'collection_entries':[{'id':e.evidence_id,'status':e.status,'reason':e.reason} for e in snapshot.audit.entries],
    'metric_samples':metrics,'external_model_blocked':True,'fact_citations_available':True}))
'''


QUALITY_PROGRAM = r'''
import json,time,urllib.request
from pathlib import Path
from urllib.parse import urlencode
from ai_ops.b_contracts import load_b_settings
settings=load_b_settings(*(Path('/etc/ai-ops')/name for name in ('bindings.json','runtime.json','metric-queries.json')))
binding=settings.bindings.bindings[0]
assert binding.target.namespace=='ai-ops-lab' and settings.runtime.llm_enabled is False
values={}
for key,expression in settings.metrics.quality_expressions.items():
    expression=expression.replace('@namespace@',binding.target.namespace).replace('@service@',binding.target.service)
    url=settings.runtime.connections[binding.prometheus_connection].base_url+'/api/v1/query?'+urlencode({'query':expression})
    with urllib.request.urlopen(url,timeout=8) as response:
        data=json.load(response)
    assert data['status']=='success' and len(data['data']['result'])==1
    values[key]=float(data['data']['result'][0]['value'][1])
assert values['up']==1 and values['scrape_health']==1 and values['scrape_samples']>=8
assert 0<=time.time()-values['last_scrape']<=60
print(json.dumps(values))
'''


class LabClient:
    """고정 실습 Redis replicas와 실습 앱 이미지의 제한된 변경만 지원한다."""

    def command(self, *args, timeout=45, payload=None):
        result = subprocess.run(["kubectl", "--context", CONTEXT, *args], input=payload, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise ValueError("고정 실습 명령이 실패했습니다")
        return result.stdout

    def get(self, namespace, kind, name):
        return json.loads(self.command("-n", namespace, "get", kind, name, "-o", "json"))

    def production(self):
        result = {}
        for namespace, kind, name in (("url-shortener", "deployment", "url-shortener-api"),
                                       ("ai-ops", "statefulset", "ai-ops"), ("ai-ops", "pvc", "data-ai-ops-0")):
            obj = self.get(namespace, kind, name)
            result[namespace + "/" + name] = {k: obj["metadata"][k] for k in ("uid", "generation") if k in obj["metadata"]}
        return result

    def guard(self, *, require_quality=True):
        namespace = self.get(NAMESPACE, "namespace", NAMESPACE)
        require_manual_deployment(namespace)
        if any(namespace["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items()):
            raise ValueError("실습 소유 label이 다릅니다")
        identity = {}
        for name in ("lab-api", "lab-redis", "lab-postgres", "lab-collector"):
            obj = self.get(NAMESPACE, "deployment", name)
            if name == "lab-api":
                require_manual_deployment(obj)
            if (any(obj["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items())
                    or obj["spec"]["replicas"] != 1 or obj["status"].get("readyReplicas") != 1):
                raise ValueError("실습 대상이 정상 기준선이 아닙니다")
            identity[name] = {"uid": obj["metadata"]["uid"], "generation": obj["metadata"]["generation"],
                              "image": obj["spec"]["template"]["spec"]["containers"][0]["image"]}
        if not hasattr(self, "redis_uid"):
            self.redis_uid = identity["lab-redis"]["uid"]
        if self.command("-n", "argocd", "get", "application", "lab-target", "--ignore-not-found", "-o", "json").strip():
            raise ValueError("실습 Application 등록 후에는 수동 실습 실행기를 사용하지 않습니다")
        # 기존 인가 검사와 동일하게 실제 Secret 조회나 쓰기는 실행하지 않는다.
        for verb, resource, namespace, expected in (
            ("get", "deployments.apps/lab-api", NAMESPACE, "yes"),
            ("get", "secrets", NAMESPACE, "no"), ("patch", "deployments.apps/lab-api", NAMESPACE, "no"),
            ("list", "pods", "url-shortener", "no")):
            result = subprocess.run(["kubectl", "--context", CONTEXT, "auth", "can-i", verb, resource, "-n", namespace,
                                     "--as=system:serviceaccount:ai-ops-lab:lab-investigator"], capture_output=True, text=True, timeout=30)
            if result.returncode not in (0, 1) or result.stdout.strip() != expected:
                raise ValueError("실습 읽기 인가 경계가 다릅니다")
        if require_quality:
            self.remote("lab-collector", "collector", QUALITY_PROGRAM)
        return identity

    def missing_image(self, run_id):
        """동일 앱 저장소의 임의 태그 부재를 ECR의 명시적 실패 코드로 확인한다."""
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("이미지 실습 ID가 유효하지 않습니다")
        result = subprocess.run(["aws", "ecr", "batch-get-image", "--region", "ap-northeast-2",
            "--registry-id", "716174522908", "--repository-name", "urlshortener",
            "--image-ids", "imageTag=b6-missing-" + run_id, "--output", "json"],
            capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise ValueError("이미지 부재를 확인할 수 없습니다")
        data = json.loads(result.stdout)
        failures = data.get("failures", [])
        if (data.get("images") != [] or len(failures) != 1
                or failures[0].get("failureCode") != "ImageNotFound"
                or failures[0].get("imageId", {}).get("imageTag") != "b6-missing-" + run_id):
            raise ValueError("명시적으로 부재가 확인된 실습 태그가 아닙니다")
        return "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener:b6-missing-" + run_id

    def replace_image(self, baseline, old_image, new_image):
        """UID·resourceVersion·기존 이미지가 일치할 때만 api 컨테이너 이미지를 바꾼다."""
        repository = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener"
        for image in (old_image, new_image):
            if not re.fullmatch(re.escape(repository) + r"(?:@sha256:[a-f0-9]{64}|:b6-missing-[a-f0-9]{32})", image):
                raise ValueError("실습용 앱 저장소 밖의 이미지는 사용할 수 없습니다")
        obj = self.get(NAMESPACE, "deployment", "lab-api")
        require_manual_deployment(obj)
        containers = obj["spec"]["template"]["spec"]["containers"]
        if (obj["metadata"]["uid"] != baseline["uid"] or len(containers) != 1 or containers[0]["name"] != "api"
                or obj["spec"]["replicas"] != 1 or obj["spec"]["strategy"]["type"] != "Recreate"
                or any(obj["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items())):
            raise ValueError("기준선과 다른 실습 앱은 변경하지 않습니다")
        current = containers[0]["image"]
        if current == new_image:
            return
        if current != old_image:
            raise ValueError("다른 변경이 적용된 실습 이미지는 덮어쓰지 않습니다")
        patch = [{"op": "test", "path": "/metadata/uid", "value": baseline["uid"]},
                 {"op": "test", "path": "/metadata/resourceVersion", "value": obj["metadata"]["resourceVersion"]},
                 {"op": "test", "path": "/spec/template/spec/containers/0/image", "value": old_image},
                 {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": new_image}]
        self.command("-n", NAMESPACE, "patch", "deployment", "lab-api", "--type=json", "--patch", json.dumps(patch))

    def wait_image_failure(self, baseline, bad_image, *, sleep=time.sleep):
        """새 ReplicaSet 소유 Pod의 실제 대기 상태와 image 실패 이벤트를 기다린다."""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            obj = self.get(NAMESPACE, "deployment", "lab-api")
            if (obj["metadata"]["uid"] != baseline["uid"]
                    or obj["spec"]["template"]["spec"]["containers"][0]["image"] != bad_image):
                raise ValueError("실습 배포가 실행 중 바뀌었습니다")
            replicasets = json.loads(self.command("-n", NAMESPACE, "get", "replicasets", "-l", "app=lab-api", "-o", "json"))["items"]
            owners = {r["metadata"]["uid"] for r in replicasets if any(o.get("uid") == baseline["uid"] and o.get("controller") is True
                       for o in r["metadata"].get("ownerReferences", []))}
            pods = json.loads(self.command("-n", NAMESPACE, "get", "pods", "-l", "app=lab-api", "-o", "json"))["items"]
            for pod in pods:
                if (pod["metadata"].get("deletionTimestamp") or not any(o.get("uid") in owners and o.get("controller") is True
                        for o in pod["metadata"].get("ownerReferences", []))):
                    continue
                containers = pod["spec"]["containers"]
                if len(containers) != 1 or containers[0]["name"] != "api" or containers[0]["image"] != bad_image:
                    continue
                states = [s for s in pod["status"].get("containerStatuses", []) if s["name"] == "api"]
                if not states:
                    continue
                reason = states[0].get("state", {}).get("waiting", {}).get("reason")
                if reason not in {"ImagePullBackOff", "ErrImagePull"} or states[0]["ready"]:
                    continue
                events = json.loads(self.command("-n", NAMESPACE, "get", "events", "--field-selector",
                    "involvedObject.uid=" + pod["metadata"]["uid"], "-o", "json"))["items"]
                reasons = sorted({e.get("reason") for e in events if e.get("reason") in {"Failed", "BackOff", "Pulling", "ErrImagePull", "ImagePullBackOff"}})
                if not {"Failed", "ErrImagePull", "ImagePullBackOff"}.intersection(reasons):
                    continue
                if obj["status"].get("readyReplicas", 0) != 0:
                    continue
                return {"deployment_uid": baseline["uid"], "deployment_generation": obj["metadata"]["generation"],
                        "replicaset_uids": sorted(owners), "pod_uid": pod["metadata"]["uid"],
                        "waiting_reason": reason, "ready_replicas": 0, "event_reasons": reasons, "at": utc_now()}
            sleep(5)
        raise ValueError("제한 시간 안에 실제 이미지 취득 실패를 확인하지 못했습니다")

    def restore_image(self, baseline, bad_image):
        self.replace_image(baseline, bad_image, baseline["image"])
        self.command("-n", NAMESPACE, "rollout", "status", "deployment/lab-api", "--timeout=180s", timeout=195)
        health = self.app("health", uuid.uuid4().hex)
        return health

    def remote(self, deployment, container, program, *args):
        if (deployment, container) not in {("lab-api", "api"), ("lab-collector", "collector")}:
            raise ValueError("실습 실행 대상이 아닙니다")
        return json.loads(self.command("-n", NAMESPACE, "exec", "deployment/" + deployment, "-c", container,
                                       "--", "python", "-c", program, *args, timeout=100))

    def app(self, action, run_id):
        return self.remote("lab-api", "api", APP_PROGRAM, action, run_id)

    def collect(self, run_id):
        return self.remote("lab-collector", "collector", COLLECT_PROGRAM, "lab-investigation-" + run_id)

    def scale_redis(self, replicas):
        if type(replicas) is not int or replicas not in (0, 1):
            raise ValueError("Redis replicas는 0 또는 1만 허용합니다")
        obj = self.get(NAMESPACE, "deployment", "lab-redis")
        if (obj["metadata"]["uid"] != self.redis_uid
                or any(obj["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items())):
            raise ValueError("기준선과 다른 Redis는 변경하지 않습니다")
        if obj["spec"]["replicas"] == replicas:
            return
        self.command("-n", NAMESPACE, "scale", "deployment/lab-redis", "--replicas=" + str(replicas), "--current-replicas=" + str(1-replicas))

    def wait_redis(self, replicas):
        if replicas == 1:
            self.command("-n", NAMESPACE, "rollout", "status", "deployment/lab-redis", "--timeout=120s", timeout=135)
        else:
            self.command("-n", NAMESPACE, "wait", "--for=delete", "pod", "-l", "app=lab-redis", "--timeout=120s", timeout=135)
            obj = self.get(NAMESPACE, "deployment", "lab-redis")
            if obj["spec"]["replicas"] != 0 or obj["status"].get("readyReplicas", 0) != 0:
                raise ValueError("실습 Redis 중단을 확인하지 못했습니다")


def advanced(before, after, key):
    """시계열 첫 출현을 숫자 0과 혼동하지 않고 실제 증가를 검사한다."""
    value = after.get(key)
    if value is None:
        return False
    previous = before.get(key)
    return value == 1 if previous is None else value == previous + 1


def check_read(read, outcome):
    if read["http_status"] != 200 or not advanced(read["counters_before"], read["counters_after"], outcome):
        raise ValueError("실제 캐시 결과가 기대 관측과 다릅니다")
    if outcome in {"miss", "hit"}:
        for failure in ("error", "unavailable"):
            if read["counters_before"].get(failure) != read["counters_after"].get(failure):
                raise ValueError("정상 캐시 기준선에 오류 증가가 섞였습니다")


def check_delay(read, *, delayed, baseline=None):
    """HTTP·우회 counter·실제 DB histogram 증가를 함께 확인한다."""
    check_read(read, "bypass")
    if (read.get("delay_requested_ms") != (750 if delayed else 0)
            or read.get("delay_header_sent") is not delayed or read.get("bypass_header_sent") is not True):
        raise ValueError("검토한 실습 지연 헤더가 아닙니다")
    for failure in ("error", "unavailable"):
        if read["counters_before"].get(failure) != read["counters_after"].get(failure):
            raise ValueError("DB 지연 실험에 캐시 연결 오류가 섞였습니다")
    before, after = read["db_before"], read["db_after"]
    if not advanced(before, after, "count") or "seconds" not in after:
        raise ValueError("DB histogram 표본 증가를 확인하지 못했습니다")
    # 처음 생긴 sum은 그대로 보존하고 비교 가능한 실제 표본으로만 차이를 구한다.
    seconds = after["seconds"] - before["seconds"] if "seconds" in before else after["seconds"]
    if delayed:
        if baseline is None or read["elapsed_seconds"] - baseline["elapsed_seconds"] < 0.5 or seconds < 0.65:
            raise ValueError("주입한 DB 세션 지연을 실제 관측하지 못했습니다")
    elif read["elapsed_seconds"] >= 0.5 or seconds >= 0.5:
        raise ValueError("지연 없는 DB 기준선이 느립니다")
    return seconds


def execute(scenario, client, *, sleep=time.sleep, run_id=None, event=None, checkpoint=None, recovery_plan=None):
    """관측·주입·복구를 구분하며 어떤 실패도 성공 결과로 바꾸지 않는다."""
    if scenario not in {"S1", "S2", "S3", "S4"}:
        raise ValueError("이번 실행기는 S1·S2·S3·S4만 지원합니다")
    run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("실습 실행 ID 형식이 다릅니다")
    if scenario == "S3":
        return execute_image(client, run_id, event=event, checkpoint=checkpoint, recovery_plan=recovery_plan)
    notify = event or (lambda message: None)
    result = {"scenario": scenario, "run_id": run_id, "namespace": NAMESPACE, "status": "failed",
              "started_at": utc_now(), "observations": [], "http_calls": 0, "external_model_calls": 0,
              "scope": "single_isolated_rules_scenario_not_live_llm_accuracy"}
    try:
        before = client.production()
        identity = client.guard()
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
        result.update(status="blocked", failure_reason="preflight_failed", cleanup={"no_mutation_started": True}, finished_at=utc_now())
        return result
    result["baseline"] = identity
    create_attempted = False
    redis_stop_attempted = False
    delay_attempted = False
    delay_baseline = None
    try:
        create_attempted = True
        created = client.app("create", run_id)
        result["http_calls"] += created["http_calls"]
        result["owned_item_id"] = created["item_id"]
        result["intervention_started_at"] = utc_now()
        for action in (("delay-baseline",) if scenario == "S4" else ("cold", "hit")):
            observation = client.app(action, run_id)
            result["http_calls"] += observation["http_calls"]
            if scenario == "S4":
                check_delay(observation, delayed=False)
                delay_baseline = observation
            else:
                check_read(observation, "miss" if action == "cold" else "hit")
            result["observations"].append({"action": action, "at": utc_now(), **observation})
        # 첫 출현 counter가 Prometheus 기준 표본으로 수집될 시간을 둔다.
        notify("실습 기준선 확인·첫 지표 수집 대기")
        sleep(35)
        if scenario == "S4":
            for batch in range(3):
                delay_attempted = True
                observation = client.app("delay-read", run_id)
                result["http_calls"] += observation["http_calls"]
                check_delay(observation, delayed=True, baseline=delay_baseline)
                result["observations"].append({"action": "delay-read", "batch": batch, "at": utc_now(), **observation})
                notify("실습 DB 세션 750ms 지연 관측·지표 수집 대기")
                sleep(35)
        elif scenario == "S1":
            redis_stop_attempted = True
            client.scale_redis(0)
            client.wait_redis(0)
            result["redis_stopped_at"] = utc_now()
            for batch in range(3):
                observation = client.app("fault-read", run_id)
                before_values, after_values = observation["counters_before"], observation["counters_after"]
                if observation["http_status"] != 200 or not any(advanced(before_values, after_values, k) for k in ("error", "unavailable")):
                    raise ValueError("캐시 접근 실패와 DB fallback을 확인하지 못했습니다")
                result["observations"].append({"action": "fault-read", "batch": batch, "at": utc_now(), **observation})
                result["http_calls"] += observation["http_calls"]
                notify("실습 Redis 중단 중 DB fallback 확인·지표 수집 대기")
                sleep(35)
        else:
            for action in ("cold", "hit"):
                observation = client.app(action, run_id)
                result["http_calls"] += observation["http_calls"]
                check_read(observation, "miss" if action == "cold" else "hit")
                result["observations"].append({"action": action, "at": utc_now(), **observation})
            notify("두 번째 cold miss→hit 확인·지표 수집 대기")
            sleep(35)
        if result["http_calls"] > 16:
            raise ValueError("복구용 요청을 제외한 실습 요청 한도를 넘었습니다")
        result["ground_truth_recorded_at"] = utc_now()
        if checkpoint:
            checkpoint({**result, "status": "observations_recorded", "record_type": "pre_investigation_ground_truth",
                        "execution_status_final": False})
        # 조사 질문에는 scenario·주입 명령·정답표를 넣지 않는다.
        result["report"] = client.collect(run_id)
        result["intervention_verified"] = True
        if scenario == "S2":
            result["report_check"] = "no_false_cause" if result["report"]["hypotheses"] == 0 else "needs_review"
        elif scenario == "S1":
            samples = result["report"]["metric_samples"].get("cache_outcomes", [])
            error_visible = any(s["outcome"] == "error" and s["value"] > 0 for s in samples)
            result["report_check"] = "cache_error_supported" if (error_visible and result["report"]["diagnosis_status"] == "suspected_cause"
                and any("ev-metric-cache_outcomes" in ids for ids in result["report"]["hypothesis_evidence_ids"])) else "needs_review"
        else:
            samples = result["report"]["metric_samples"].get("db_latency", [])
            latency_visible = any(s["outcome"] == "p99" and s["value"] >= 0.5 for s in samples)
            latency_cited = any("ev-metric-db_latency" in ids for ids in result["report"].get("fact_evidence_ids", []))
            result["report_check"] = "app_db_latency_observed_cause_deferred" if (latency_visible and latency_cited
                and result["report"]["hypotheses"] == 0 and result["report"]["current_state"] == "unknown") else "needs_review"
        result["status"] = "passed" if result["report_check"] != "needs_review" else "needs_review"
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
        result["failure_reason"] = "scenario_observation_failed"
    finally:
        cleanup = {"redis_restored": not redis_stop_attempted, "owned_data_removed": not create_attempted}
        if delay_attempted:
            try:
                observation = client.app("delay-recovered", run_id)
                result["http_calls"] += observation["http_calls"]
                check_delay(observation, delayed=False)
                result["recovery_observation"] = {"at": utc_now(), **observation}
                cleanup["delay_disabled_and_latency_recovered"] = True
            except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
                cleanup["delay_disabled_and_latency_recovered"] = False
        if redis_stop_attempted:
            try:
                client.scale_redis(1)
                client.wait_redis(1)
                client.app("ping", run_id)
                cleanup["redis_restored"] = True
            except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
                cleanup["redis_restored"] = False
        if create_attempted:
            try:
                observation = client.app("cleanup", run_id)
                result["http_calls"] += observation["http_calls"]
                cleanup["owned_data_removed"] = observation["owned_data_removed"] and observation["redis_ping"]
            except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
                cleanup["owned_data_removed"] = False
        try:
            cleanup["production_unchanged"] = client.production() == before
            current = client.guard()
            cleanup["lab_uids_and_images_unchanged"] = all(current[k]["uid"] == value["uid"] and current[k]["image"] == value["image"]
                and (k == "lab-redis" or current[k]["generation"] == value["generation"]) for k, value in identity.items())
        except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
            cleanup["production_unchanged"] = False
        result["cleanup"] = cleanup
        result["finished_at"] = utc_now()
        if not all(cleanup.values()):
            result["status"] = "recovery_failed"
        if result["http_calls"] > 20:
            result["status"] = "failed"
            result["failure_reason"] = "request_budget_exceeded"
    return result


def execute_image(client, run_id, *, event=None, checkpoint=None, recovery_plan=None):
    """실습 앱의 실패 이미지를 관측하고 어떤 결과에서도 고정 digest로 복구한다."""
    result = {"scenario": "S3", "run_id": run_id, "namespace": NAMESPACE, "status": "failed",
              "started_at": utc_now(), "external_model_calls": 0, "http_calls": 0,
              "scope": "isolated_image_failure_manual_restore_not_ci_rollback"}
    notify = event or (lambda message: None)
    try:
        before = client.production()
        identity = client.guard()
        baseline = identity["lab-api"]
        if not re.fullmatch(r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener@sha256:[a-f0-9]{64}", baseline["image"]):
            raise ValueError("복구용 이미지가 고정 digest가 아닙니다")
        bad_image = client.missing_image(run_id)
        result.update(baseline=identity, bad_image=bad_image, image_absence_confirmed=True)
        health = client.app("health", run_id)
        result["http_calls"] += health["http_calls"]
        if recovery_plan is None:
            raise ValueError("변경 전 복구 계획을 별도 저장해야 합니다")
        recovery_plan({"record_type": "pre_mutation_recovery_plan", "scenario": "S3", "run_id": run_id,
                       "namespace": NAMESPACE, "baseline": baseline, "bad_image": bad_image, "created_at": utc_now()})
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
        result.update(status="blocked", failure_reason="image_preflight_failed", cleanup={"no_mutation_started": True}, finished_at=utc_now())
        return result
    attempted = False
    try:
        attempted = True
        result["intervention_started_at"] = utc_now()
        client.replace_image(baseline, baseline["image"], bad_image)
        notify("실습 앱 이미지 변경·실제 취득 실패 대기")
        result["failure_observation"] = client.wait_image_failure(baseline, bad_image)
        result["intervention_verified"] = True
        result["ground_truth_recorded_at"] = utc_now()
        if checkpoint:
            checkpoint({**result, "status": "observations_recorded", "execution_status_final": False})
        result["report"] = client.collect(run_id)
        report = result["report"]
        event_ids = {e["id"] for e in report.get("event_reasons", []) if e["reason"] in {"ImagePullBackOff", "ErrImagePull"}
                     or (e["reason"] in {"Failed", "BackOff"} and e.get("image_failure_marker") is True)}
        supported = any(event_ids.intersection(ids) for ids in report.get("hypothesis_evidence_ids", []))
        result["report_check"] = "image_failure_supported" if (report["diagnosis_status"] == "suspected_cause" and supported) else "needs_review"
        result["status"] = "passed" if supported and report["diagnosis_status"] == "suspected_cause" else "needs_review"
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
        result["failure_reason"] = "image_observation_failed"
    finally:
        cleanup = {}
        if attempted:
            try:
                result["recovery_observation"] = client.restore_image(baseline, bad_image)
                result["http_calls"] += result["recovery_observation"]["http_calls"]
                current = client.guard(require_quality=False)
                cleanup["original_digest_ready_http_and_redis_restored"] = all(
                    current[k]["uid"] == v["uid"] and current[k]["image"] == v["image"]
                    and (k == "lab-api" or current[k]["generation"] == v["generation"]) for k, v in identity.items())
            except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
                cleanup["original_digest_ready_http_and_redis_restored"] = False
        try:
            cleanup["production_unchanged"] = client.production() == before
        except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
            cleanup["production_unchanged"] = False
        result.update(cleanup=cleanup, finished_at=utc_now())
        if not all(cleanup.values()):
            result["status"] = "recovery_failed"
    return result
