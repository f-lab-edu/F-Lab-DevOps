"""GitOps 실습 앱의 같은 Pod에서 GET·counter·제한 로그를 함께 수집한다."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import time
import uuid
from typing import Annotated

from pydantic import Field, StrictInt, model_validator

from ai_ops.b6_coordination import LabLease
from ai_ops.b6_lab import NAMESPACE, PURPOSE
from ai_ops.b6_scenarios import APP_PROGRAM, LabClient, QUALITY_PROGRAM
from ai_ops.contracts import StrictModel, UTCDateTime
from ai_ops.evidence import normalize_source_time
from ai_ops.model_sequence_projection import (Count, ItemID, LOG, Outcome, ProbeRead,
                                              probe_log_hash)


class RequestObservation(StrictModel):
    """전체 측정 구간과 실제 GET 구간을 구분해 보존한다."""

    started_at: UTCDateTime
    request_started_at: UTCDateTime
    request_finished_at: UTCDateTime
    finished_at: UTCDateTime
    http_status: Annotated[StrictInt, Field(ge=200, le=200)]
    response_item_id: ItemID
    counters_before: dict[Outcome, Count]
    counters_after: dict[Outcome, Count]

    @model_validator(mode="after")
    def consistent(self):
        if not self.started_at <= self.request_started_at <= self.request_finished_at <= self.finished_at:
            raise ValueError("probe request timestamps rejected")
        if (self.finished_at-self.started_at).total_seconds() > 15:
            raise ValueError("probe observation too long")
        return self


def bind_reads(observations: list[RequestObservation], raw: bytes, item_id: int) -> list[ProbeRead]:
    """로그의 전체 제한 응답에서 소유 아이템의 각 요청에 한 줄씩만 연결한다."""
    if (type(raw) is not bytes or len(raw) >= 64*1024 or
            len(raw.splitlines()) >= 256 or len(observations) not in (2, 4)):
        raise ValueError("probe log limits reached")
    owned = re.compile(r"item_id=" + str(item_id) + r"(?:\D|$)")
    prefix = "INFO:app.api.routes.item:"
    selected = []
    for line in raw.decode("utf-8", errors="strict").splitlines():
        if "cache_" not in line or not owned.search(line):
            continue
        timestamp, separator, message = line.partition(" ")
        if not separator or not message.startswith(prefix):
            raise ValueError("probe app log format rejected")
        message = message[len(prefix):]
        if LOG.fullmatch(message) is None:
            raise ValueError("probe app log outcome rejected")
        at, _ = normalize_source_time(timestamp)
        selected.append((at, message))
    if len(selected) != len(observations):
        raise ValueError("probe app logs missing or duplicated")
    reads = []
    for observation, (at, message) in zip(observations, selected):
        if observation.response_item_id != item_id:
            raise ValueError("probe response item changed")
        data = observation.model_dump(mode="json", exclude={"request_started_at", "request_finished_at"})
        reads.append(ProbeRead(**data, log_at=at, log_message=message))
    if ([read.outcome for read in reads] not in (["miss", "hit"], ["miss", "hit", "miss", "hit"]) or
            any(a.finished_at >= b.started_at or a.counters_after != b.counters_before
                for a, b in zip(reads, reads[1:]))):
        raise ValueError("probe order or counter continuity rejected")
    return reads


class SequenceLabClient(LabClient):
    """Deployment 변경 없이 고정 Pod의 자기 데이터 작업과 로그 읽기만 연결한다."""

    def pin_app(self):
        deployment = self.get(NAMESPACE, "deployment", "lab-api")
        if (deployment["spec"]["replicas"] != 1 or deployment["status"].get("readyReplicas") != 1 or
                any(deployment["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items())):
            raise ValueError("probe deployment not ready")
        containers = deployment["spec"]["template"]["spec"]["containers"]
        if (len(containers) != 1 or containers[0]["name"] != "api" or not re.fullmatch(
                r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener@sha256:[a-f0-9]{64}",
                containers[0]["image"])):
            raise ValueError("probe app container rejected")
        replicasets = json.loads(self.command("-n", NAMESPACE, "get", "replicasets", "-l", "app=lab-api", "-o", "json"))["items"]
        owners = {rs["metadata"]["uid"] for rs in replicasets if any(
            owner.get("controller") is True and owner.get("kind") == "Deployment" and
            owner.get("uid") == deployment["metadata"]["uid"] for owner in rs["metadata"].get("ownerReferences", []))}
        pods = json.loads(self.command("-n", NAMESPACE, "get", "pods", "-l", "app=lab-api", "-o", "json"))["items"]
        if len(pods) != 1:
            raise ValueError("probe requires exactly one pod")
        pod = pods[0]
        status = pod["status"].get("containerStatuses", [])
        controllers = [owner for owner in pod["metadata"].get("ownerReferences", []) if owner.get("controller") is True]
        if (pod["metadata"].get("deletionTimestamp") or len(controllers) != 1 or
                controllers[0].get("kind") != "ReplicaSet" or controllers[0].get("uid") not in owners or
                len(status) != 1 or status[0].get("name") != "api" or status[0].get("ready") is not True or
                not status[0].get("containerID") or "running" not in status[0].get("state", {}) or
                len(pod["spec"].get("containers", [])) != 1 or
                pod["spec"]["containers"][0].get("name") != "api" or
                pod["spec"]["containers"][0].get("image") != containers[0]["image"] or
                not status[0].get("imageID", "").endswith(containers[0]["image"].split("@", 1)[1]) or
                not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", pod["metadata"]["name"])):
            raise ValueError("probe pod ownership or readiness rejected")
        return {"deployment_uid": deployment["metadata"]["uid"],
                "generation": deployment["metadata"]["generation"], "image": containers[0]["image"],
                "pod_name": pod["metadata"]["name"], "pod_uid": pod["metadata"]["uid"],
                "container_id": status[0]["containerID"], "restarts": status[0]["restartCount"]}

    def guard(self):
        namespace = self.get(NAMESPACE, "namespace", NAMESPACE)
        if any(namespace["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items()):
            raise ValueError("probe namespace ownership rejected")
        application = self.get("argocd", "application", "lab-target")
        if (application["spec"]["project"] != "b6-lab" or
                application["spec"]["destination"]["namespace"] != NAMESPACE or
                application["status"]["sync"]["status"] != "Synced" or
                application["status"]["health"]["status"] != "Healthy" or
                application["status"].get("operationState", {}).get("phase") == "Running"):
            raise ValueError("probe GitOps baseline rejected")
        lab = {}
        for name in ("lab-api", "lab-collector", "lab-postgres", "lab-redis"):
            obj = self.get(NAMESPACE, "deployment", name)
            if (obj["spec"]["replicas"] != 1 or obj["status"].get("readyReplicas") != 1 or
                    any(obj["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items())):
                raise ValueError("probe lab baseline rejected")
            lab[name] = {"uid": obj["metadata"]["uid"], "generation": obj["metadata"]["generation"],
                         "image": obj["spec"]["template"]["spec"]["containers"][0]["image"]}
        pinned = self.pin_app()
        if hasattr(self, "pinned") and self.pinned != pinned:
            raise ValueError("probe pod changed")
        self.pinned = pinned
        self.remote("lab-collector", "collector", QUALITY_PROGRAM)
        return {"lab": lab, "production": self.production(), "app": pinned,
                "argo": {"uid": application["metadata"]["uid"], "spec": application["spec"],
                         "revision": application["status"]["sync"].get("revision")}}

    def app(self, action, run_id):
        if action not in {"create", "cold", "hit", "cleanup"} or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise ValueError("probe action rejected")
        if not hasattr(self, "pinned") or self.pin_app() != self.pinned:
            raise ValueError("probe pod changed before exec")
        raw = self.command("-n", NAMESPACE, "exec", self.pinned["pod_name"], "-c", "api",
                           "--", "python", "-c", APP_PROGRAM, action, run_id, timeout=100)
        if len(raw.encode()) > 16*1024 or self.pin_app() != self.pinned:
            raise ValueError("probe pod changed during exec")
        return json.loads(raw)

    def logs(self, since):
        if self.pin_app() != self.pinned:
            raise ValueError("probe pod changed before logs")
        raw = self.command("-n", NAMESPACE, "logs", self.pinned["pod_name"], "-c", "api",
            "--timestamps=true", "--follow=false", "--previous=false", "--since-time=" + since,
            "--tail=257", "--limit-bytes=65537", "--request-timeout=30s").encode()
        if self.pin_app() != self.pinned or len(raw) >= 64*1024 or len(raw.splitlines()) >= 256:
            raise ValueError("probe logs changed or limits reached")
        return raw


def capture_sequence(client: SequenceLabClient, *, run_id=None, sleep=time.sleep,
                     checkpoint=None, lease_factory=LabLease):
    """조회·수집 실패에도 자기 데이터 원복을 확인한 후에만 공유 잠금을 해제한다."""
    run_id = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("probe run id rejected")
    now = lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    save = checkpoint or (lambda value: None)
    baseline = client.guard()
    lease = lease_factory(client.command, "manual-" + run_id)
    lock = lease.acquire()
    result = {"schema_version": 1, "run_id": run_id, "status": "running", "started_at": now(),
              "baseline": baseline, "lock": lock, "external_model_calls": 0, "observations": []}
    attempted = False
    try:
        save(result)
        attempted = True
        creation = client.app("create", run_id)
        if creation.get("created") is not True or creation.get("redis_ping") is not True or type(creation.get("item_id")) is not int:
            raise ValueError("probe creation unverified")
        result["item_id"] = creation["item_id"]
        observations = []
        for _ in range(2):
            for action in ("cold", "hit"):
                value = client.app(action, run_id)
                observation = RequestObservation.model_validate({
                    key: value[key] for key in RequestObservation.model_fields})
                observations.append(observation)
                result["observations"].append(observation.model_dump(mode="json"))
            sleep(35)
        logs = client.logs(observations[0].started_at.isoformat().replace("+00:00", "Z"))
        reads = bind_reads(observations, logs, result["item_id"])
        result.update(reads=[read.model_dump(mode="json") for read in reads],
                      raw_logs=logs.decode(), raw_log_sha256=hashlib.sha256(logs).hexdigest(),
                      log_source_hash=probe_log_hash(reads), recorded_at=now())
        save(result)
        investigation = client.collect(run_id)
        if (not re.fullmatch(r"inc-[a-f0-9]{32}", investigation.get("incident_id", "")) or
                not re.fullmatch(r"run-[a-f0-9]{32}", investigation.get("run_id", "")) or
                not re.fullmatch(r"[a-f0-9]{64}", investigation.get("bundle_hash", "")) or
                investigation.get("external_model_blocked") is not True or
                investigation.get("fact_citations_available") is not True):
            raise ValueError("probe investigation unverified")
        result["investigation"] = investigation
        result["status"] = "captured"
    except Exception as error:
        result.update(status="failed", failure_type=type(error).__name__)
    finally:
        recovery = {"owned_data_removed": not attempted, "baseline_unchanged": False}
        if attempted:
            try:
                cleanup = client.app("cleanup", run_id)
                recovery["owned_data_removed"] = cleanup.get("owned_data_removed") is True and cleanup.get("redis_ping") is True
            except Exception:
                pass
        try:
            recovery["baseline_unchanged"] = client.guard() == baseline
        except Exception:
            pass
        result["recovery"] = recovery
        if all(recovery.values()):
            try:
                result["lock_release"] = lease.release(recovery_verified=True, scenario_started=attempted)
            except Exception:
                result["status"] = "recovery_failed"
        else:
            result["status"] = "recovery_failed"
        result["finished_at"] = now()
    return result
