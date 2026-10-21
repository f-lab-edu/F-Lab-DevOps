"""Deployment 소유 관계를 검증한 Pod의 상태·로그·이벤트·구성을 읽는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from ai_ops.b_contracts import ReadRequest, ReadTransport
from ai_ops.collection import ReadFailure, SnapshotBuilder
from ai_ops.evidence import normalize_source_time
from ai_ops.live_collectors.common import add, labels_match, list_items, metadata, owned_by, read_json, utc_now


@dataclass
class WorkloadContext:
    deployment: dict | None = field(default=None, repr=False)
    observed_at: str | None = None
    trusted: bool = False
    image: str | None = None
    revision_changed: bool = False


def api_container(obj: dict, name: str) -> dict:
    containers = obj["spec"]["template"]["spec"]["containers"] if obj["kind"] in {"Deployment", "ReplicaSet"} else obj["spec"]["containers"]
    matches = [container for container in containers if container.get("name") == name]
    if len(matches) != 1:
        raise ValueError("registered container missing or duplicated")
    return matches[0]


def _context(meta: dict) -> dict:
    return {"resource_uid": meta["uid"], "resource_version": meta["resourceVersion"]}


def _read(builder, transport, path, params=()):
    return read_json(builder, transport, ReadRequest(builder.binding.kubernetes_connection, path, tuple(params)))


def _mark_scope_failure(builder, failure):
    for key, kind in (("workload-scope", "workload_status"), ("workload-logs", "log"),
                      ("workload-events", "event"), ("workload-config", "config_summary")):
        add(builder, key, kind, failure=failure)


def collect_kubernetes(builder: SnapshotBuilder, transport: ReadTransport) -> WorkloadContext:
    binding = builder.binding
    ns = binding.target.namespace
    apps = f"/apis/apps/v1/namespaces/{ns}"
    core = f"/api/v1/namespaces/{ns}"
    dep_path = f"{apps}/deployments/{binding.target.workload}"
    deployment = _read(builder, transport, dep_path)
    dep_at = utc_now(builder)
    context = WorkloadContext()
    if isinstance(deployment, ReadFailure):
        _mark_scope_failure(builder, deployment)
        return context
    try:
        dm = metadata(deployment, kind="Deployment", namespace=ns, name=binding.target.workload)
        if type(dm.get("generation")) is not int or dm["generation"] < 1:
            raise ValueError("deployment generation missing")
        selector = deployment["spec"]["selector"]
        if selector.get("matchExpressions") or selector.get("matchLabels") != binding.pod_selector:
            raise ValueError("deployment selector mismatch")
        image = api_container(deployment, binding.container)["image"]
        if not isinstance(image, str):
            raise ValueError("invalid deployment image")
        replicas = deployment["spec"].get("replicas")
        if type(replicas) is int and replicas >= 0:
            builder.workload_expected_pods = replicas
        context = WorkloadContext(deployment, dep_at, False, image)
    except (KeyError, TypeError, ValueError, AttributeError):
        _mark_scope_failure(builder, ReadFailure("invalid", "scope_mismatch"))
        return context
    service = _read(builder, transport, f"{core}/services/{binding.target.service}")
    if isinstance(service, ReadFailure):
        _mark_scope_failure(builder, service)
        return context
    try:
        metadata(service, kind="Service", namespace=ns, name=binding.target.service)
        if service["spec"]["selector"] != binding.pod_selector:
            raise ValueError("service selector mismatch")
        selector_text = ",".join(f"{key}={value}" for key, value in sorted(binding.pod_selector.items()))
        params = (("labelSelector", selector_text), ("limit", str(builder.settings.runtime.limits.max_pods + 1)))
        rs_response = _read(builder, transport, apps + "/replicasets", params)
        if isinstance(rs_response, ReadFailure):
            _mark_scope_failure(builder, rs_response)
            return context
        replicasets = list_items(rs_response, kind="ReplicaSetList", limit=builder.settings.runtime.limits.max_pods)
        owners = {}
        for rs in replicasets:
            rm = metadata(rs, kind="ReplicaSet", namespace=ns)
            if not labels_match(rs, binding.pod_selector) or not owned_by(rs, kind="Deployment", name=dm["name"], uid=dm["uid"]):
                raise ValueError("replicaset owner mismatch")
            if rm["uid"] in owners:
                raise ValueError("duplicate replicaset UID")
            owners[rm["uid"]] = rm["name"]
        pod_response = _read(builder, transport, core + "/pods", params)
        pod_at = utc_now(builder)
        if isinstance(pod_response, ReadFailure):
            _mark_scope_failure(builder, pod_response)
            return context
        pods = list_items(pod_response, kind="PodList", limit=builder.settings.runtime.limits.max_pods)
        if not pods:
            _mark_scope_failure(builder, ReadFailure("missing", "empty_response"))
            return context
        seen = set()
        for pod in pods:
            pm = metadata(pod, kind="Pod", namespace=ns)
            if pm["uid"] in seen or not labels_match(pod, binding.pod_selector):
                raise ValueError("pod identity mismatch")
            seen.add(pm["uid"])
            if not any(owned_by(pod, kind="ReplicaSet", name=name, uid=uid) for uid, name in owners.items()):
                raise ValueError("pod owner mismatch")
            api_container(pod, binding.container)
        # 수집 도중 롤아웃이 바뀌면 과거 Pod 집합을 현재 건강의 근거로 쓰지 않는다.
        latest = _read(builder, transport, dep_path)
        if isinstance(latest, ReadFailure):
            _mark_scope_failure(builder, latest)
            return context
        lm = metadata(latest, kind="Deployment", namespace=ns, name=dm["name"])
        if (lm["uid"] != dm["uid"] or lm.get("generation") != dm.get("generation")
                or latest["spec"]["template"] != deployment["spec"]["template"]):
            context.revision_changed = True
            _mark_scope_failure(builder, ReadFailure("invalid", "revision_changed"))
            return context
    except OverflowError:
        _mark_scope_failure(builder, ReadFailure("missing", "truncated"))
        return context
    except (KeyError, TypeError, ValueError, AttributeError):
        _mark_scope_failure(builder, ReadFailure("invalid", "scope_mismatch"))
        return context
    context.trusted = True
    for index, pod in enumerate(sorted(pods, key=lambda p: p["metadata"]["uid"])):
        pm = pod["metadata"]
        audit = _context(pm)
        rs = next(rs for rs in replicasets if owned_by(pod, kind="ReplicaSet", name=rs["metadata"]["name"], uid=rs["metadata"]["uid"]))
        audit.update(deployment_uid=dm["uid"], replicaset_uid=rs["metadata"]["uid"],
                     replicaset_resource_version=rs["metadata"]["resourceVersion"])
        prefix = f"pod-{index}"
        try:
            statuses = [value for value in pod["status"]["containerStatuses"] if value["name"] == binding.container]
            if len(statuses) != 1:
                raise ValueError("container status missing")
            status = statuses[0]
            if type(status["restartCount"]) is not int or status["restartCount"] < 0 or type(status["ready"]) is not bool:
                raise ValueError("invalid container status")
            ready = (status["ready"] and not pm.get("deletionTimestamp") and
                     any(condition.get("type") == "Ready" and condition.get("status") == "True"
                         for condition in pod["status"].get("conditions", [])))
            add(builder, prefix + "-status", "workload_status", at=pod_at,
                payload={"image": status["image"], "phase": pod["status"]["phase"],
                         "ready": bool(ready), "restarts": status["restartCount"]}, context=audit)
        except (KeyError, TypeError, ValueError, AttributeError):
            add(builder, prefix + "-status", "workload_status", failure=ReadFailure("invalid", "invalid_payload"), context=audit)
            add(builder, prefix + "-logs", "log", failure=ReadFailure("missing", "invalid_payload"), context=audit)
        else:
            _logs(builder, transport, pod, prefix, status["restartCount"])
        _configuration(builder, pod, prefix, pod_at)
    related = [(dm["uid"], "Deployment", dm["name"])] + [(uid, "ReplicaSet", name) for uid, name in owners.items()]
    related += [(pod["metadata"]["uid"], "Pod", pod["metadata"]["name"]) for pod in pods]
    _events(builder, transport, related)
    return context


def _configuration(builder, pod, prefix, at):
    payload = {"read_engine_role": "unknown"}
    try:
        container = api_container(pod, builder.binding.container)
        entries = [value for value in container.get("env", []) if value.get("name") == "REDIS_URL"]
        if len(entries) > 1:
            raise ValueError("duplicate Redis setting")
        secret_ref = bool(entries and entries[0].get("valueFrom", {}).get("secretKeyRef"))
        if entries and "value" in entries[0]:
            payload["redis_url_configured"] = bool(entries[0]["value"])
        elif entries and not entries[0].get("valueFrom"):
            # API 응답은 명시적인 빈 value를 생략할 수 있다. 출처 참조가 없으면 빈 문자열이다.
            payload["redis_url_configured"] = False
        elif not entries and not container.get("envFrom"):
            payload["redis_url_configured"] = False
        add(builder, prefix + "-config", "config_summary", at=at, payload=payload,
            context={**_context(pod["metadata"]), "redis_secret_reference": secret_ref})
    except (ValueError, TypeError, KeyError, AttributeError):
        add(builder, prefix + "-config", "config_summary", failure=ReadFailure("invalid", "invalid_payload"))


def _logs(builder, transport, pod, prefix, restarts):
    limits = builder.settings.runtime.limits
    lines_left = limits.log_lines_per_pod
    for previous in ([False, True] if restarts > 0 else [False]):
        key = prefix + ("-previous-log" if previous else "-log")
        # 원시 로그 응답 누적량은 정규화 후보 크기와 별도로 제한한다.
        remaining = limits.raw_log_bytes - builder.raw_log_bytes_read
        if remaining <= 0 or lines_left <= 0:
            add(builder, key, "log", failure=ReadFailure("missing", "truncated"), context={"truncated": True})
            continue
        path = f"/api/v1/namespaces/{builder.binding.target.namespace}/pods/{pod['metadata']['name']}/log"
        since = builder.started_at - timedelta(seconds=limits.lookback_seconds)
        params = (("container", builder.binding.container), ("timestamps", "true"), ("follow", "false"),
                  ("sinceTime", since.isoformat().replace("+00:00", "Z")), ("tailLines", str(lines_left)),
                  ("limitBytes", str(remaining)), ("previous", str(previous).lower()))
        response = builder.read(ReadRequest(builder.binding.kubernetes_connection, path, params), transport)
        if isinstance(response, ReadFailure):
            add(builder, key, "log", failure=response, context=_context(pod["metadata"]))
            if response.reason in {"response_too_large", "log_budget_exceeded"}:
                lines_left = 0
            continue
        # UID 재확인 실패나 잘림으로 버리는 로그도 Pod의 줄 수 예산에 포함한다.
        try:
            lines = response.body.decode("utf-8").splitlines()
        except UnicodeError:
            lines_left = 0
            add(builder, key, "log", failure=ReadFailure("invalid", "invalid_log_encoding"))
            continue
        available_lines = lines_left
        lines_left = max(0, lines_left - len(lines))
        # 같은 이름의 Pod가 교체됐으면 이전 UID의 로그로 귀속시키지 않는다.
        current = _read(builder, transport, path.removesuffix("/log"))
        if isinstance(current, ReadFailure):
            add(builder, key, "log", failure=current)
            continue
        try:
            cm = metadata(current, kind="Pod", namespace=builder.binding.target.namespace, name=pod["metadata"]["name"])
            if cm["uid"] != pod["metadata"]["uid"] or api_container(current, builder.binding.container) != api_container(pod, builder.binding.container):
                raise ValueError("pod changed during log collection")
        except (KeyError, ValueError, TypeError, AttributeError):
            add(builder, key, "log", failure=ReadFailure("invalid", "revision_changed"))
            continue
        try:
            if not lines:
                add(builder, key, "log", failure=ReadFailure("missing", "empty_response"))
                continue
            if len(response.body) >= remaining or len(lines) >= available_lines:
                add(builder, key, "log", failure=ReadFailure("missing", "truncated"), context={"truncated": True})
                continue
            try:
                at = max((normalize_source_time(line.partition(" ")[0])[0], line.partition(" ")[0]) for line in lines)[1]
            except (ValueError, TypeError):
                add(builder, key, "log", failure=ReadFailure("invalid", "invalid_log_timestamp"))
                continue
            add(builder, key, "log", at=at, payload={"container": builder.binding.container, "lines": lines}, context=_context(pod["metadata"]))
        except (ValueError, TypeError, UnicodeError):
            add(builder, key, "log", failure=ReadFailure("invalid", "invalid_payload"))


def _events(builder, transport, related):
    limit = builder.settings.runtime.limits.max_events
    gathered = []
    seen = set()
    for index, (uid, kind, name) in enumerate(related):
        response = _read(builder, transport, f"/api/v1/namespaces/{builder.binding.target.namespace}/events",
                         (("fieldSelector", "involvedObject.uid=" + uid), ("limit", str(limit + 1))))
        if isinstance(response, ReadFailure):
            add(builder, f"events-source-{index}", "event", failure=response)
            continue
        try:
            items = list_items(response, kind="EventList", limit=limit)
            for item in items:
                meta = metadata(item, kind="Event", namespace=builder.binding.target.namespace)
                involved = item["involvedObject"]
                if (involved.get("uid") != uid or involved.get("kind") != kind or involved.get("name") != name
                        or involved.get("namespace") != builder.binding.target.namespace):
                    raise ValueError("event owner mismatch")
                if meta["uid"] not in seen:
                    seen.add(meta["uid"])
                    gathered.append(item)
            if len(gathered) > limit:
                raise OverflowError("event total limit exceeded")
        except OverflowError:
            add(builder, "events-truncated", "event", failure=ReadFailure("missing", "truncated"), context={"truncated": True})
            gathered = gathered[:limit]
            break
        except (ValueError, KeyError, TypeError, AttributeError):
            add(builder, f"events-invalid-{index}", "event", failure=ReadFailure("invalid", "scope_mismatch"))
    if not gathered:
        add(builder, "events-empty", "event", failure=ReadFailure("missing", "empty_response"))
    for index, item in enumerate(sorted(gathered, key=lambda v: (v.get("type") != "Warning", v["metadata"]["uid"]))):
        field = "eventTime" if item.get("eventTime") else "lastTimestamp"
        add(builder, f"event-{index}", "event", at=item.get(field), time_field=field,
            payload={"reason": item.get("reason"), "message": item.get("message", ""),
                     "type": item.get("type"), "involved_uid": item["involvedObject"]["uid"]}, context=_context(item["metadata"]))
