"""C-03 결과에 결합한 실습 앱의 런타임·HTTP·지표·경보를 읽는다.

관찰 시각을 새로 만든 과거 증거와 섞지 않는다. 수집 실패는 자료 부족으로 남기며
입력 영수증의 해시는 승인이나 실제 C-03 실행을 인증하는 수단이 아니다.
"""

from datetime import datetime, timezone
import math
import time
from typing import Annotated, Literal
from urllib.parse import urlencode

from pydantic import Field, StrictInt, StrictStr, model_validator

from ai_ops.contracts import StrictModel, sha256_json
from ai_ops.c_verification import (
    SyncReceipt, VerificationInput, VerificationPolicy, RuntimeReading,
    HTTPReading, MetricReading, AlertReading,
)
from ai_ops.c_verification_transport import CollectionError


SCOPE = {"namespace": "ai-ops-lab", "deployment": "lab-api"}
SELECTORS = {
    "target_up": 'up{namespace="ai-ops-lab",service="lab-api"}',
    "available_replicas": 'kube_deployment_status_replicas_available{namespace="ai-ops-lab",deployment="lab-api"}',
}
REPOSITORIES = Literal["https://github.com/f-lab-edu/F-Lab-DevOps.git",
                       "https://github.com/joungGo/c03-execution-lab.git"]


class AlertDefinition(StrictModel):
    """운영자가 실제 규칙을 확인하고 고정할 필드다. 예시 규칙을 자동 생성하지 않는다."""

    name: Literal["KubeDeploymentReplicasMismatch"]
    query: Annotated[StrictStr, Field(min_length=1, max_length=4096)]
    labels: dict[StrictStr, StrictStr] = Field(max_length=32)
    duration: Annotated[float, Field(ge=0, le=86400, allow_inf_nan=False)]
    keepFiringFor: Annotated[float, Field(ge=0, le=86400, allow_inf_nan=False)] = 0
    group: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    file: Annotated[StrictStr, Field(min_length=1, max_length=1024)]


class CollectionPlan(StrictModel):
    """고정된 GET 범위와 시간 예산만 제공하며 새 실행 권한을 부여하지 않는다."""

    execution: SyncReceipt
    repository: REPOSITORIES
    alert: AlertDefinition | None = None
    duration_seconds: Annotated[StrictInt, Field(ge=120, le=600)] = 120
    interval_seconds: Annotated[StrictInt, Field(ge=10, le=45)] = 30
    source_max_age_seconds: Annotated[StrictInt, Field(ge=15, le=90)] = 60

    @model_validator(mode="after")
    def bounded_samples(self):
        if self.duration_seconds // self.interval_seconds + 3 > 100:
            raise ValueError("최대 관찰 표본 수 초과")
        return self


def definition_hash(value):
    return sha256_json({"definition": value, "target": SCOPE})


def policy_for(plan, start=None, end=None):
    """실제 관찰 시각만 창에 넣는다. 규칙이 없으면 미설정 해시를 유지한다."""
    return VerificationPolicy(window_start=start, window_end=end,
        minimum_span_seconds=plan.duration_seconds, maximum_gap_seconds=90,
        minimum_samples=4, maximum_report_delay_seconds=120, http_paths=["/readyz", "/items"],
        metrics=[{"name": name, "definition_hash": definition_hash(query), "comparison": "gte", "threshold": 1.0}
                 for name, query in SELECTORS.items()],
        alerts=[{"name": "KubeDeploymentReplicasMismatch", "definition_hash": definition_hash(
            plan.alert.model_dump(mode="json") if plan.alert else {"not_configured": True})}])


def check_anchor(plan, application, deployment):
    """이름이 같아도 재생성된 UID·저장소·실습 경로는 이전 영수증에 연결하지 않는다."""
    if (application["metadata"]["uid"] != plan.execution.binding.application_uid
            or deployment["metadata"]["uid"] != plan.execution.binding.target_uid):
        raise CollectionError("target_identity_mismatch")
    check_location(plan.repository, application, deployment)


def check_location(repository, application, deployment):
    """실행 영수증 없는 준비 조회에서도 같은 저장소·실습 범위 제한을 사용한다."""
    source = application.get("spec", {}).get("source", {})
    destination = application.get("spec", {}).get("destination", {})
    if (application["metadata"].get("name") != "lab-target"
            or application["metadata"].get("namespace") != "argocd"
            or deployment["metadata"].get("name") != "lab-api"
            or deployment["metadata"].get("namespace") != "ai-ops-lab"
            or application.get("spec", {}).get("sources")
            or source.get("repoURL") != repository or source.get("path") != "ai-ops/lab"
            or destination != {"namespace": "ai-ops-lab", "server": "https://kubernetes.default.svc"}):
        raise CollectionError("source_or_destination_mismatch")


def _controller(obj, kind, uid):
    return any(x.get("kind") == kind and x.get("uid") == uid and x.get("controller") is True
               for x in obj["metadata"].get("ownerReferences", []))


def snapshot(reader, plan):
    application, deployment = reader.get("application"), reader.get("deployment")
    check_anchor(plan, application, deployment)
    rs_list, pod_list = reader.get("replicasets"), reader.get("pods")
    if any(x.get("metadata", {}).get("continue") for x in (rs_list, pod_list)):
        raise CollectionError("kubernetes_list_incomplete")
    replicasets = rs_list["items"]
    rs_uids = {x["metadata"]["uid"] for x in replicasets
               if _controller(x, "Deployment", deployment["metadata"]["uid"])}
    pods = pod_list["items"]
    if any(not any(_controller(p, "ReplicaSet", uid) for uid in rs_uids)
           or p["metadata"].get("deletionTimestamp") for p in pods):
        raise CollectionError("pod_ownership_or_lifecycle_changed")
    images, ready = [], 0
    for pod in pods:
        containers = pod.get("status", {}).get("containerStatuses", [])
        if len(containers) != 1 or containers[0].get("name") != "api":
            raise CollectionError("pod_container_unavailable")
        container = containers[0]
        is_ready = any(x.get("type") == "Ready" and x.get("status") == "True"
                       for x in pod.get("status", {}).get("conditions", []))
        ready += int(is_ready and container.get("ready") is True)
        image = container.get("imageID", "")
        if "sha256:" in image:
            images.append("sha256:"+image.rsplit("sha256:", 1)[1])
        elif is_ready:
            raise CollectionError("pod_image_unavailable")
    status = application.get("status", {})
    operation = status.get("operationState", {})
    reading = RuntimeReading(revision=status.get("sync", {}).get("revision"),
        operation_revision=operation.get("syncResult", {}).get("revision"),
        argo_sync=status.get("sync", {}).get("status", "Unknown"),
        argo_health=status.get("health", {}).get("status", "Unknown"),
        operation_phase=operation.get("phase", "Unknown"),
        generation=deployment["metadata"]["generation"],
        observed_generation=deployment.get("status", {}).get("observedGeneration", 0),
        replicas=deployment["spec"].get("replicas", 1),
        ready_replicas=deployment.get("status", {}).get("readyReplicas", 0),
        pod_count=len(pods), ready_pods=ready, pod_images=images)
    identity = {"application_uid": application["metadata"]["uid"], "source": application["spec"]["source"],
        "deployment_uid": deployment["metadata"]["uid"], "generation": deployment["metadata"]["generation"],
        "template_hash": sha256_json(deployment["spec"]["template"]),
        "pods": sorted((p["metadata"]["name"], p["metadata"]["uid"],
            sha256_json([{k: c.get(k) for k in ("name", "imageID", "containerID", "restartCount")}
                        for c in p.get("status", {}).get("containerStatuses", [])])) for p in pods)}
    return reading, pods, sha256_json(identity)


def valid_body(path, value):
    """사용자 자료를 보존하지 않고 앱의 두 읽기 응답 계약만 검사한다."""
    if path == "/readyz":
        return value == {"status": "ready"}
    if not isinstance(value, list):
        return False
    for row in value:
        if (not isinstance(row, dict) or set(row) != {"id", "name", "description", "created_at"}
                or type(row["id"]) is not int or not isinstance(row["name"], str)
                or not (row["description"] is None or isinstance(row["description"], str))
                or not isinstance(row["created_at"], str) or "T" not in row["created_at"]):
            return False
        try:
            datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        except ValueError:
            return False
    return True


def _api(reader, port, path):
    status, value, _ = reader.http(port, path)
    if (status != 200 or not isinstance(value, dict) or value.get("status") != "success"
            or value.get("warnings") or value.get("infos")):
        raise CollectionError("prometheus_response_unavailable")
    return value["data"]


def metric_value(reader, port, query, labels, at, max_age, not_before=None):
    """평가 시각만 새로워진 오래된 시계열을 timestamp 조회로 거부한다."""
    values = []
    for expression in (query, "timestamp("+query+")"):
        # 응답 시각의 밀리초 정밀도에 맞춰 내림한다. 반올림된 시각을 미래 자료로 오인하지 않는다.
        data = _api(reader, port, "/api/v1/query?"+urlencode({"query": expression,
            "time": at.isoformat(timespec="milliseconds")}))
        if data.get("resultType") != "vector" or len(data.get("result", [])) != 1:
            raise CollectionError("metric_missing_or_ambiguous")
        point = data["result"][0]
        if any(point.get("metric", {}).get(k) != v for k, v in labels.items()):
            raise CollectionError("metric_target_mismatch")
        stamp, raw = point["value"]
        value = float(raw)
        if not math.isfinite(value) or not 0 <= at.timestamp()-float(stamp) <= max_age:
            raise CollectionError("metric_stale_or_nonfinite")
        values.append(value)
    if (not 0 <= at.timestamp()-values[1] <= max_age
            or not_before is not None and values[1] < not_before.timestamp()):
        raise CollectionError("metric_source_stale")
    return values[0]


def read_alert_rule(reader, port):
    """계획 준비와 반복 수집에서 동일한 규칙 원본·완전성 검사를 사용한다."""
    name = "KubeDeploymentReplicasMismatch"
    data = _api(reader, port, "/api/v1/rules?"+urlencode({"type": "alert", "rule_name[]": name}))
    if data.get("groupNextToken"):
        raise CollectionError("rules_incomplete")
    matches = [(g, r) for g in data.get("groups", []) for r in g.get("rules", [])
               if r.get("name") == name and r.get("type") == "alerting"]
    if len(matches) != 1:
        raise CollectionError("alert_rule_missing_or_ambiguous")
    group, rule = matches[0]
    actual = AlertDefinition(name=rule["name"], query=rule["query"], labels=rule.get("labels", {}),
        duration=rule["duration"], keepFiringFor=rule.get("keepFiringFor", 0),
        group=group["name"], file=group["file"])
    return actual, rule


def alert_reading(reader, port, plan, at, runtime):
    """규칙 원본·평가 시각·대상 지표가 모두 있어야 활성 경보 수를 사용한다."""
    actual, rule = read_alert_rule(reader, port)
    if actual != plan.alert:
        raise CollectionError("alert_definition_changed")
    evaluated = datetime.fromisoformat(rule["lastEvaluation"].replace("Z", "+00:00"))
    if (evaluated.tzinfo is None or rule.get("health") != "ok" or rule.get("lastError")
            or not 0 <= (at-evaluated).total_seconds() <= plan.source_max_age_seconds
            or evaluated < plan.execution.verified_at):
        raise CollectionError("alert_evaluation_unavailable")
    # 빈 경보 목록은 대상 지표가 없을 때도 생긴다. 필요한 KSM 값의 최신성을 함께 확인한다.
    for metric, expected in (("kube_deployment_spec_replicas", runtime.replicas),
            ("kube_deployment_status_replicas_available", runtime.ready_replicas),
            ("kube_deployment_status_replicas_updated", runtime.replicas),
            ("kube_deployment_metadata_generation", runtime.generation),
            ("kube_deployment_status_observed_generation", runtime.observed_generation)):
        query = metric+'{namespace="ai-ops-lab",deployment="lab-api"}'
        if metric_value(reader, port, query, SCOPE, at, plan.source_max_age_seconds,
                        plan.execution.verified_at) != expected:
            raise CollectionError("alert_target_data_unavailable")
    history = 'count_over_time(kube_deployment_status_replicas_updated{namespace="ai-ops-lab",deployment="lab-api"}[10m])'
    # changes()가 쓰는 실제 입력의 최소 두 표본을 확인한다. 파생 값에 timestamp()를 적용하지 않는다.
    historical = _api(reader, port, "/api/v1/query?"+urlencode({"query": history,
        "time": at.isoformat(timespec="milliseconds")}))
    points = historical.get("result", [])
    if (historical.get("resultType") != "vector" or len(points) != 1
            or any(points[0].get("metric", {}).get(k) != v for k, v in SCOPE.items())):
        raise CollectionError("alert_history_unavailable")
    stamp, count = points[0]["value"]
    if not (0 <= at.timestamp()-float(stamp) <= plan.source_max_age_seconds
            and math.isfinite(float(count)) and float(count) >= 2):
        raise CollectionError("alert_history_unavailable")
    active = 0
    for alert in rule.get("alerts", []):
        labels = alert.get("labels", {})
        if not all(k in labels for k in SCOPE):
            raise CollectionError("alert_scope_unavailable")
        if all(labels[k] == v for k, v in SCOPE.items()):
            if alert.get("state") not in ("pending", "firing"):
                raise CollectionError("alert_state_unavailable")
            active += 1
    return AlertReading(rule_present=True, evaluation="ok", evaluated_at=evaluated, active_count=active)


def _prom_identity(pod):
    return sha256_json({"uid": pod["metadata"]["uid"],
        "containers": [{k: c.get(k) for k in ("name", "containerID", "restartCount")}
            for c in pod.get("status", {}).get("containerStatuses", [])]})


def collect(plan, reader, *, enabled=False, clock=lambda: datetime.now(timezone.utc),
            monotonic=time.monotonic, sleep=time.sleep, cancel_check=None):
    """먼저 대상 대조만 한다. 명시적으로 켠 경우에만 한정된 관찰 창을 수집한다."""
    audit = {"mode": "collect" if enabled else "preflight", "reasons": [], "rounds": 0,
             "workload_changes": 0, "external_model_calls": 0}
    series = {}
    policy = policy_for(plan)
    for kind, name, definition in [("runtime", "lab-api", None), ("http", "/readyz", None),
            ("http", "/items", None), *[("metric", x.name, x.definition_hash) for x in policy.metrics],
            *[("alert", x.name, x.definition_hash) for x in policy.alerts]]:
        series[kind, name] = {"kind": kind, "name": name, "binding": plan.execution.binding,
            "definition_hash": definition, "source_hash": "0"*64, "status": "missing", "samples": []}

    def fail(reason, key=None):
        if reason not in audit["reasons"]:
            audit["reasons"].append(reason)
        keys = [key] if key else list(series)
        for item in keys:
            series[item].update(status="collection_failed", samples=[])

    def add(key, reading, observed=None):
        item = series[key]
        if item["status"] != "collection_failed":
            item["status"] = "available"
            item["samples"].append({"observed_at": observed or clock(), "reading": reading})

    previous_deadline = getattr(reader, "deadline", None)
    try:
        if cancel_check is not None:
            cancel_check()
        app, deployment = reader.get("application"), reader.get("deployment")
        audit["observed_binding"] = type(plan.execution.binding)(
            application_uid=app["metadata"]["uid"], target_uid=deployment["metadata"]["uid"]).model_dump(mode="json")
        check_anchor(plan, app, deployment)
        if plan.execution.verified_at is None or plan.execution.verified_at > clock():
            raise CollectionError("execution_time_unavailable_or_future")
        if not enabled:
            audit["reasons"].append("preflight_only")
        elif plan.alert is None:
            raise CollectionError("alert_definition_required")
        else:
            started = monotonic()
            if hasattr(reader, "deadline"):
                deadline = time.monotonic()+plan.duration_seconds+120
                reader.deadline = min(deadline, previous_deadline) if previous_deadline is not None else deadline
            anchor = None
            prom_anchor = None
            while monotonic()-started < plan.duration_seconds+120:
                if cancel_check is not None:
                    cancel_check()
                runtime, pods, identity = snapshot(reader, plan)
                if anchor is not None and anchor != identity:
                    raise CollectionError("observation_identity_changed")
                anchor = identity
                add(("runtime", "lab-api"), runtime)
                if len(pods) == 1 and runtime.ready_pods == 1:
                    pod = pods[0]["metadata"]["name"]
                    try:
                        with reader.forward("ai-ops-lab", pod, 8000) as port:
                            for path in policy.http_paths:
                                try:
                                    code, body, _ = reader.http(port, path)
                                    add(("http", path), HTTPReading(status_code=code,
                                        response_contract_valid=valid_body(path, body)))
                                except (ValueError, KeyError, TypeError, OSError):
                                    fail("http_collection_failed", ("http", path))
                    except (ValueError, KeyError, TypeError, OSError):
                        for path in policy.http_paths:
                            fail("http_collection_failed", ("http", path))
                else:
                    for path in policy.http_paths:
                        fail("http_target_not_ready", ("http", path))
                try:
                    prom = reader.get("prometheus")
                    prom_identity = _prom_identity(prom)
                    if prom_anchor is not None and prom_anchor != prom_identity:
                        raise CollectionError("prometheus_identity_changed")
                    prom_anchor = prom_identity
                    if (prom["metadata"]["name"] != "prometheus-kube-prometheus-stack-prometheus-0"
                            or prom["metadata"]["namespace"] != "monitoring"
                            or not any(c.get("type") == "Ready" and c.get("status") == "True"
                                       for c in prom.get("status", {}).get("conditions", []))):
                        raise CollectionError("prometheus_not_ready")
                    with reader.forward("monitoring", prom["metadata"]["name"], 9090) as port:
                        for name, query in SELECTORS.items():
                            try:
                                at = clock()
                                labels = {"namespace": "ai-ops-lab", "service": "lab-api", "pod": pods[0]["metadata"]["name"]} if name == "target_up" and len(pods) == 1 else SCOPE
                                value = metric_value(reader, port, query, labels, at, plan.source_max_age_seconds,
                                                     plan.execution.verified_at)
                                add(("metric", name), MetricReading(value=value), at)
                            except (ValueError, KeyError, TypeError, OSError, IndexError):
                                fail("metric_collection_failed", ("metric", name))
                        try:
                            at = clock()
                            add(("alert", plan.alert.name), alert_reading(reader, port, plan, at, runtime), at)
                        except (ValueError, KeyError, TypeError, OSError):
                            fail("alert_collection_failed", ("alert", plan.alert.name))
                    if _prom_identity(reader.get("prometheus")) != prom_identity:
                        raise CollectionError("prometheus_identity_changed")
                except (ValueError, KeyError, TypeError, OSError):
                    for key in series:
                        if key[0] in ("metric", "alert"):
                            fail("prometheus_collection_failed", key)
                if snapshot(reader, plan)[2] != anchor:
                    raise CollectionError("observation_identity_changed")
                audit["rounds"] += 1
                now = clock()
                # 각 종류의 실제 첫·끝 시각을 확인한다. API 호출 지연으로 짧아진 창은 연장한다.
                available = [x["samples"] for x in series.values() if x["status"] == "available"]
                if available and all((x[-1]["observed_at"]-x[0]["observed_at"]).total_seconds()
                        >= plan.duration_seconds for x in available) and audit["rounds"] >= 4:
                    earliest = min(s["observed_at"] for x in series.values() for s in x["samples"])
                    policy = policy_for(plan, earliest, now)
                    break
                if cancel_check is None:
                    sleep(plan.interval_seconds)
                else:
                    remaining = plan.interval_seconds
                    while remaining > 0:
                        cancel_check()
                        step = min(1, remaining)
                        sleep(step)
                        remaining -= step
            else:
                raise CollectionError("collection_deadline_exceeded")
    except (ValueError, KeyError, TypeError, OSError, IndexError) as error:
        fail(str(error) if isinstance(error, CollectionError) else "source_contract_unavailable")
    finally:
        if hasattr(reader, "deadline"):
            reader.deadline = previous_deadline
    values = []
    for item in series.values():
        item["source_hash"] = sha256_json({"kind": item["kind"], "name": item["name"],
            "definition_hash": item["definition_hash"], "status": item["status"],
            "samples": [{"observed_at": x["observed_at"].isoformat(), "reading": x["reading"].model_dump(mode="json")}
                        for x in item["samples"]]})
        values.append(item)
    audit.update(http_requests=getattr(reader, "http_requests", 0),
                 forwards_started=getattr(reader, "forwards_started", 0))
    return VerificationInput(provenance="operator_supplied", execution=plan.execution,
        policy=policy, evaluated_at=clock(), series=values), audit
