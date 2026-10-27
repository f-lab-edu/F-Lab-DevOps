"""원본 로그를 실습 수집기 안에서 검증·보관하고 정제 영수증만 출력한다."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Literal

from pydantic import Field, StrictBool, StrictInt

from ai_ops.b_contracts import ReadRequest, load_b_settings
from ai_ops.b6_sequence_capture import RequestObservation, bind_reads
from ai_ops.contracts import StrictModel, UTCDateTime, canonical_json_bytes, sha256_json
from ai_ops.incidents import IncidentStore, _atomic_write, _private_directory, _read_json
from ai_ops.live_collectors import collect_snapshot
from ai_ops.live_collectors.transport import HTTPReadTransport
from ai_ops.model_projection import SourceIdentity, TARGET, project_snapshot
from ai_ops.model_sequence_projection import ProbeRecord, parse_sequence_projection, probe_log_hash, project_probe
from ai_ops.normal_ci_contract import NormalCIBinding, NormalCIIdentity, NormalCIReceipt, executor_hash


class SourceRequest(StrictModel):
    action: Literal["begin", "checkpoint", "seal", "finish"]
    identity: NormalCIIdentity
    binding: NormalCIBinding
    started_at: UTCDateTime
    item_id: StrictInt | None = Field(default=None, ge=1)
    observations: list[RequestObservation] = Field(default_factory=list, max_length=4)
    owned_data_removed: StrictBool = False
    baseline_unchanged: StrictBool = False
    lock_released: StrictBool = False


class NormalSourceStore:
    """CI의 자기 세션만 갱신한다. 이 폴더는 현재 수집기 볼륨 수명을 따른다."""

    def __init__(self, settings, root=Path("/data/incidents"), *, transport=None,
                 collector=collect_snapshot, clock=None):
        if (settings.runtime.llm_enabled or len(settings.bindings.bindings) != 1
                or settings.bindings.bindings[0].target.model_dump(mode="json") != TARGET):
            raise ValueError("normal_ci_source_target_rejected")
        self.settings = settings
        self.root = root
        self.directory = root/"normal-ci"
        _private_directory(self.directory)
        self.transport = transport or HTTPReadTransport(settings.runtime)
        self.collector = collector
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _read(self, path, params=(), *, limit=65536):
        connection = self.settings.bindings.bindings[0].kubernetes_connection
        response = self.transport.get(ReadRequest(connection, path, params),
                                      timeout_seconds=30, max_bytes=limit)
        if response.status_code != 200 or len(response.body) >= limit:
            raise ValueError("normal_ci_source_read_rejected")
        return response.body

    def _pin(self, binding):
        """로그 응답 전후의 같은 Pod·Deployment·이미지를 확인한다."""
        app = binding.app
        pod = json.loads(self._read("/api/v1/namespaces/ai-ops-lab/pods/"+app.name))
        deployment = json.loads(self._read("/apis/apps/v1/namespaces/ai-ops-lab/deployments/lab-api"))
        statuses = pod["status"].get("containerStatuses", [])
        if (pod["metadata"]["uid"] != app.uid or pod["metadata"].get("deletionTimestamp")
                or deployment["metadata"]["uid"] != app.deployment_uid
                or deployment["spec"]["template"]["spec"]["containers"][0]["image"] != app.image
                or len(statuses) != 1 or statuses[0].get("ready") is not True
                or not statuses[0].get("imageID", "").endswith(app.image.split("@", 1)[1])):
            raise ValueError("normal_ci_source_pod_changed")
        return {"pod": pod["metadata"]["uid"], "generation": deployment["metadata"]["generation"],
                "container": statuses[0].get("containerID"), "restarts": statuses[0]["restartCount"]}

    def handle(self, request: SourceRequest):
        if request.binding.executor_sha256 != executor_hash():
            raise ValueError("normal_ci_source_code_changed")
        if request.binding.config_hash != self.settings.config_hash:
            raise ValueError("normal_ci_source_config_changed")
        directory = self.directory/request.identity.session
        path = directory/"source.json"
        if request.action == "begin":
            # mkdir의 배타성으로 동일 실행·재실행 번호의 덮어쓰기를 막는다.
            directory.mkdir(mode=0o700)
            value = {"state": "started", "identity": request.identity.model_dump(mode="json"),
                "binding": request.binding.model_dump(mode="json"),
                "started_at": request.started_at.isoformat(), "observations": []}
        else:
            _private_directory(directory)
            value = _read_json(path, 512*1024)
            if (value["identity"] != request.identity.model_dump(mode="json")
                    or value["binding"] != request.binding.model_dump(mode="json")
                    or value["started_at"] != request.started_at.isoformat()
                    or value["state"] in ("completed", "failed")):
                raise ValueError("normal_ci_source_session_changed")
            if request.action == "checkpoint":
                previous = value["observations"]
                current = [item.model_dump(mode="json") for item in request.observations]
                if (value["state"] != "started" or current[:len(previous)] != previous
                        or len(current) < len(previous)
                        or value.get("item_id", request.item_id) != request.item_id):
                    raise ValueError("normal_ci_source_checkpoint_changed")
                value.update(item_id=request.item_id, observations=current)
            elif request.action == "seal":
                if (value["state"] != "started" or len(value["observations"]) != 4
                        or value.get("item_id") is None):
                    raise ValueError("normal_ci_source_incomplete")
                value = self._seal(value, request)
            elif request.action == "finish":
                recovered = (request.owned_data_removed and request.baseline_unchanged and request.lock_released)
                value["recovery"] = {key: getattr(request, key) for key in
                    ("owned_data_removed", "baseline_unchanged", "lock_released")}
                if value["state"] == "sealed" and recovered:
                    projection = parse_sequence_projection(canonical_json_bytes(value["projection"]))
                    receipt = NormalCIReceipt.from_projection(projection, identity=request.identity,
                        binding=request.binding, started_at=request.started_at, finished_at=self.clock())
                    value.update(state="completed", receipt=receipt.model_dump(mode="json"))
                else:
                    value["state"] = "failed"
            else:
                raise ValueError("normal_ci_source_action_rejected")
        _atomic_write(path, canonical_json_bytes(value))
        if value["state"] == "completed":
            return value["receipt"]
        return {"state": value["state"]}

    def _seal(self, value, request):
        observations = [RequestObservation.model_validate(item) for item in value["observations"]]
        pinned = self._pin(request.binding)
        since = observations[0].started_at.isoformat().replace("+00:00", "Z")
        raw = self._read("/api/v1/namespaces/ai-ops-lab/pods/"+request.binding.app.name+"/log",
            (("container", "api"), ("timestamps", "true"), ("follow", "false"),
             ("previous", "false"), ("sinceTime", since), ("tailLines", "257"), ("limitBytes", "65537")))
        if self._pin(request.binding) != pinned:
            raise ValueError("normal_ci_source_pod_changed")
        reads = bind_reads(observations, raw, value["item_id"])
        recorded = self.clock()
        # 분석·정제에 실패해도 원본 대조 자료가 수집기 내부에 남도록 먼저 기록한다.
        value.update(raw_logs=raw.decode(), reads=[item.model_dump(mode="json") for item in reads],
                     recorded_at=recorded.isoformat())
        _atomic_write(self.directory/request.identity.session/"source.json", canonical_json_bytes(value))
        settings = self.settings
        snapshot = self.collector(settings, settings.bindings.bindings[0].target,
                                  route="/items/{item_id}", operation="select_one")
        record = IncidentStore(self.root).investigate_snapshot(snapshot=snapshot, settings=settings,
            question="같은 실습 아이템의 캐시 관측과 부족한 자료를 설명해 주세요.",
            request_id="normal-ci-"+request.identity.session)
        if record.report.execution.mode != "rules":
            raise ValueError("normal_ci_source_rules_required")
        identity = SourceIdentity(cluster_fingerprint=request.binding.cluster_fingerprint,
            deployment_uid=request.binding.app.deployment_uid, config_hash=settings.config_hash,
            incident_id=record.incident_id, run_id=record.run_id,
            exporter_sha256=request.binding.executor_sha256)
        base = project_snapshot(snapshot, settings, identity)
        probe = ProbeRecord(source_bundle_hash=base.source_bundle_hash,
            source_audit_hash=base.source_audit_hash, config_hash=settings.config_hash,
            deployment_uid=request.binding.app.deployment_uid, pod_uid=request.binding.app.uid,
            image_reference_hash=sha256_json(request.binding.app.image), window=base.view.window,
            recorded_at=recorded, item_id=value["item_id"], reads=reads,
            log_source_hash=probe_log_hash(reads), raw_log_sha256=hashlib.sha256(raw).hexdigest())
        projection = project_probe(base, probe, snapshot, settings, raw_logs=raw,
                                   log_pod_uid=request.binding.app.uid, logs_truncated=False)
        if self._pin(request.binding) != pinned:
            raise ValueError("normal_ci_source_pod_changed")
        value.update(state="sealed", raw_logs=raw.decode(), projection=projection.model_dump(mode="json"))
        return value


def main():
    os.umask(0o077)
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ValueError("normal_ci_source_request_too_large")
        request = SourceRequest.model_validate_json(raw)
        settings = load_b_settings(*(Path("/etc/ai-ops")/name for name in
                                    ("bindings.json", "runtime.json", "metric-queries.json")))
        print(json.dumps(NormalSourceStore(settings).handle(request)))
        return 0
    except Exception:
        # 원본·예외 본문·자격을 CI 로그에 반환하지 않는다.
        print('{"error":"normal_ci_source_rejected"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
