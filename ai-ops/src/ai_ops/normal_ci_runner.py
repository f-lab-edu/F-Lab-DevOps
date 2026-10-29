"""검토한 main의 정상 관측 CI에서 자기 데이터 네 조회와 원복을 수행한다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from ai_ops.b6_coordination import LabLease
from ai_ops.b6_lab import NAMESPACE, PURPOSE
from ai_ops.b6_scenarios import QUALITY_PROGRAM
from ai_ops.b6_sequence_capture import RequestObservation, SequenceLabClient
from ai_ops.contracts import sha256_json
from ai_ops.normal_ci_contract import (NormalCIBinding, NormalCIIdentity, NormalCIReceipt,
                                      WORKFLOW, executor_hash)
from ai_ops.normal_ci_source import SourceRequest
from ai_ops.path_policy import cli_paths, write_private_json


class NormalCIClient(SequenceLabClient):
    """현재 고정된 실습 Pod만 실행한다. 운영 namespace를 읽지 않는다."""

    def __init__(self, binding):
        self.binding = binding

    def pin_app(self):
        result = super().pin_app()
        expected = self.binding.app
        if any(result[key] != value for key, value in {
            "pod_name": expected.name, "pod_uid": expected.uid,
            "deployment_uid": expected.deployment_uid, "image": expected.image}.items()):
            raise ValueError("normal_ci_app_binding_changed")
        return result

    def pin_collector(self):
        expected = self.binding.collector
        deployment = self.get(NAMESPACE, "deployment", "lab-collector")
        pod = self.get(NAMESPACE, "pod", expected.name)
        statuses = pod["status"].get("containerStatuses", [])
        owners = [item for item in pod["metadata"].get("ownerReferences", []) if item.get("controller") is True]
        if len(owners) != 1 or owners[0].get("kind") != "ReplicaSet":
            raise ValueError("normal_ci_collector_owner_changed")
        rs = self.get(NAMESPACE, "replicaset", owners[0]["name"])
        controllers = [item for item in rs["metadata"].get("ownerReferences", []) if item.get("controller") is True]
        if (pod["metadata"]["uid"] != expected.uid or pod["metadata"].get("deletionTimestamp")
                or rs["metadata"]["uid"] != owners[0]["uid"] or len(controllers) != 1
                or controllers[0].get("kind") != "Deployment"
                or controllers[0].get("uid") != expected.deployment_uid
                or deployment["metadata"]["uid"] != expected.deployment_uid
                or deployment["spec"]["template"]["spec"]["containers"][0]["image"] != expected.image
                or len(statuses) != 1 or statuses[0].get("name") != "collector"
                or statuses[0].get("ready") is not True or "running" not in statuses[0].get("state", {})
                or not statuses[0].get("imageID", "").endswith(expected.image.split("@", 1)[1])):
            raise ValueError("normal_ci_collector_binding_changed")
        return {"uid": expected.uid, "generation": deployment["metadata"]["generation"],
                "container": statuses[0].get("containerID"), "restarts": statuses[0]["restartCount"]}

    def remote(self, deployment, container, program, *args):
        if (deployment, container) != ("lab-collector", "collector"):
            raise ValueError("normal_ci_remote_target_rejected")
        before = self.pin_collector()
        raw = self.command("-n", NAMESPACE, "exec", self.binding.collector.name,
                           "-c", "collector", "--", "python", "-c", program, *args, timeout=100)
        if len(raw.encode()) > 32768 or self.pin_collector() != before:
            raise ValueError("normal_ci_collector_changed")
        return json.loads(raw)

    def source(self, request):
        before = self.pin_collector()
        raw = self.command("-n", NAMESPACE, "exec", "-i", self.binding.collector.name,
            "-c", "collector", "--", "python", "-m", "ai_ops.normal_ci_source",
            payload=request.model_dump_json(), timeout=150)
        if len(raw.encode()) > 32768 or self.pin_collector() != before:
            raise ValueError("normal_ci_source_response_rejected")
        return json.loads(raw)

    def guard(self):
        app, collector = self.pin_app(), self.pin_collector()
        application = self.get("argocd", "application", "lab-target")
        if (application["spec"]["project"] != "b6-lab"
                or application["spec"]["destination"]["namespace"] != NAMESPACE
                or application["spec"]["source"]["repoURL"] != "https://github.com/f-lab-edu/F-Lab-DevOps.git"
                or application["spec"]["source"]["path"] != "ai-ops/lab"
                or set(application["spec"]["source"]) != {"repoURL", "path", "targetRevision"}
                or application["status"]["sync"]["status"] != "Synced"
                or application["status"]["health"]["status"] != "Healthy"
                or application["status"].get("operationState", {}).get("phase") == "Running"):
            raise ValueError("normal_ci_gitops_baseline_rejected")
        lab = {}
        for name in ("lab-api", "lab-collector", "lab-postgres", "lab-redis"):
            obj = self.get(NAMESPACE, "deployment", name)
            if (obj["spec"]["replicas"] != 1 or obj["status"].get("readyReplicas") != 1
                    or any(obj["metadata"].get("labels", {}).get(key) != value for key, value in PURPOSE.items())):
                raise ValueError("normal_ci_lab_baseline_rejected")
            lab[name] = {"uid": obj["metadata"]["uid"], "generation": obj["metadata"]["generation"],
                         "image": obj["spec"]["template"]["spec"]["containers"][0]["image"]}
        self.pinned = app
        self.remote("lab-collector", "collector", QUALITY_PROGRAM)
        return {"app": app, "collector": collector, "lab": lab,
                "argo": {"uid": application["metadata"]["uid"], "spec": application["spec"],
                         "revision": application["status"]["sync"].get("revision")}}


def run_normal_ci(client, identity, binding, *, sleep=time.sleep, lease_factory=LabLease, clock=None):
    """실패·응답 유실에도 자기 데이터 정리와 기준선 검증 후에만 잠금을 해제한다."""
    if binding.executor_sha256 != executor_hash():
        raise ValueError("normal_ci_runner_code_changed")
    clock = clock or (lambda: datetime.now(timezone.utc))
    started = clock()
    baseline = client.guard()
    lease = lease_factory(client.command, "ci-"+identity.session)
    lease.acquire()
    attempted = False
    began = False
    sealed = False
    observations = []
    item_id = None
    recovery = {"owned_data_removed": False, "baseline_unchanged": False, "lock_released": False}
    result = {"record_type": "b6_normal_ci_failure", "identity": identity.model_dump(mode="json"),
              "reason": "capture_failed", "external_model_calls": 0}
    def source(action):
        return client.source(SourceRequest(action=action, identity=identity, binding=binding,
            started_at=started, item_id=item_id, observations=observations, **recovery))
    try:
        if source("begin") != {"state": "started"}:
            raise ValueError("normal_ci_begin_rejected")
        began = True
        attempted = True
        created = client.app("create", identity.session)
        if created.get("created") is not True or created.get("redis_ping") is not True or type(created.get("item_id")) is not int:
            raise ValueError("normal_ci_creation_unverified")
        item_id = created["item_id"]
        if source("checkpoint") != {"state": "started"}:
            raise ValueError("normal_ci_checkpoint_rejected")
        for index, action in enumerate(("cold", "hit", "cold", "hit")):
            if index == 2:
                sleep(35)
            observation = RequestObservation.model_validate({key: value for key, value in
                client.app(action, identity.session).items() if key in RequestObservation.model_fields})
            if observation.response_item_id != item_id:
                raise ValueError("normal_ci_item_changed")
            observations.append(observation)
            if source("checkpoint") != {"state": "started"}:
                raise ValueError("normal_ci_checkpoint_rejected")
        if source("seal") != {"state": "sealed"}:
            raise ValueError("normal_ci_seal_rejected")
        sealed = True
    except Exception:
        # 고정 사유만 반환하며 명령 출력·HTTP 본문·예외 문자열을 남기지 않는다.
        pass
    finally:
        if attempted:
            try:
                cleaned = client.app("cleanup", identity.session)
                recovery["owned_data_removed"] = cleaned.get("owned_data_removed") is True and cleaned.get("redis_ping") is True
            except Exception:
                pass
        else:
            recovery["owned_data_removed"] = True
        try:
            recovery["baseline_unchanged"] = client.guard() == baseline
        except Exception:
            pass
        if recovery["owned_data_removed"] and recovery["baseline_unchanged"]:
            try:
                recovery["lock_released"] = lease.release(recovery_verified=True, scenario_started=attempted).get("released") is True
            except Exception:
                pass
        if began:
            try:
                finished = source("finish")
                if sealed and all(recovery.values()):
                    receipt = NormalCIReceipt.model_validate(finished)
                    if (receipt.identity != identity or receipt.binding_hash != sha256_json(binding.model_dump(mode="json"))
                            or receipt.executor_sha256 != binding.executor_sha256):
                        raise ValueError("normal_ci_receipt_source_changed")
                    result = receipt.model_dump(mode="json")
            except Exception:
                result["reason"] = "source_finalize_failed"
        if not all(recovery.values()):
            result["reason"] = "recovery_unverified"
        if result["record_type"] == "b6_normal_ci_failure":
            result["recovery"] = recovery
    return result


def workflow_identity():
    if (os.environ.get("GITHUB_REPOSITORY") != "f-lab-edu/F-Lab-DevOps"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_WORKFLOW_REF") != "f-lab-edu/F-Lab-DevOps/"+WORKFLOW+"@refs/heads/main"):
        raise ValueError("normal_ci_workflow_scope_rejected")
    return NormalCIIdentity(run_id=os.environ["GITHUB_RUN_ID"], run_attempt=os.environ["GITHUB_RUN_ATTEMPT"],
                            source_revision=os.environ["GITHUB_SHA"])


def verify_cluster(binding, *, execute=subprocess.run):
    """동명 재설치 클러스터와 다른 kubeconfig 연결을 기존 승인에 섞지 않는다."""
    def command(args):
        response = execute(args, capture_output=True, timeout=45)
        if response.returncode or len(response.stdout) > 65536:
            raise ValueError("normal_ci_cluster_read_rejected")
        return response.stdout
    cluster = json.loads(command(["aws", "eks", "describe-cluster", "--region", "ap-northeast-2",
                                  "--name", "urlshortener", "--output", "json"]))["cluster"]
    context = command(["kubectl", "config", "current-context"]).decode().strip()
    kube = json.loads(command(["kubectl", "--context", binding.cluster_arn,
                              "config", "view", "--minify", "--raw", "-o", "json"]))
    connection = kube["clusters"][0]["cluster"]
    fingerprint = sha256_json({"arn": cluster["arn"], "endpoint": cluster["endpoint"],
                               "ca": cluster["certificateAuthority"]["data"]})
    if (cluster["arn"] != binding.cluster_arn or cluster["status"] != "ACTIVE"
            or context != binding.cluster_arn or fingerprint != binding.cluster_fingerprint
            or connection.get("server") != cluster["endpoint"]
            or connection.get("certificate-authority-data") != cluster["certificateAuthority"]["data"]):
        raise ValueError("normal_ci_cluster_binding_changed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="격리 정상 관측 CI 네 조회·수집기 내부 정제·원복; 모델 호출 없음")
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(argv), {"binding": "input", "output": "output"})
    os.umask(0o077)
    try:
        if args.binding.stat().st_size > 8192:
            raise ValueError("normal_ci_binding_too_large")
        binding = NormalCIBinding.model_validate_json(args.binding.read_bytes())
        verify_cluster(binding)
        result = run_normal_ci(NormalCIClient(binding), workflow_identity(), binding)
        write_private_json(args.output, result)
        print(json.dumps({"record_type": result["record_type"], "external_model_calls": 0}))
        return 0 if result["record_type"] == "b6_normal_ci_observation" else 1
    except Exception:
        print('{"error":"normal_ci_runner_rejected"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
