"""고정 실습 namespace에만 설치하며 운영 Deployment·AI PVC의 불변을 확인한다."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import uuid
from datetime import datetime, timezone

from ai_ops.b6_lab import CONTEXT, NAMESPACE, PURPOSE, validate_lab
from ai_ops.b6_coordination import LabLease, require_manual_deployment
from ai_ops.path_policy import cli_paths, write_private_json


def command(*args, payload=None, timeout=45):
    """자격 증명이 포함될 수 있는 서버 오류 본문은 출력하지 않는다."""
    result = subprocess.run(args, input=payload, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError("실습 설치 명령 실패: " + args[0])
    return result.stdout


def production_identity():
    identities = {}
    for namespace, resource, name in (("url-shortener", "deployment", "url-shortener-api"),
                                      ("ai-ops", "statefulset", "ai-ops"),
                                      ("ai-ops", "pvc", "data-ai-ops-0")):
        item = json.loads(command("kubectl", "--context", CONTEXT, "-n", namespace, "get", resource, name, "-o", "json"))
        identities[namespace + "/" + name] = {key: item["metadata"][key] for key in ("uid", "generation") if key in item["metadata"]}
    return identities


def deploy(bundle):
    summary = validate_lab(bundle)
    if command("kubectl", "config", "current-context").strip() != CONTEXT:
        raise ValueError("실습 대상 context가 다릅니다")
    before = production_identity()
    payload = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    existing = command("kubectl", "--context", CONTEXT, "get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json").strip()
    marker = "ai-ops/b6-bundle-sha256"
    if existing:
        metadata = json.loads(existing)["metadata"]
        if (any(metadata.get("labels", {}).get(k) != v for k, v in PURPOSE.items())
                or metadata.get("annotations", {}).get(marker) != digest):
            raise ValueError("다른 구성의 기존 namespace는 변경하지 않습니다")
    else:
        namespace = json.loads(json.dumps(bundle["items"][0]))
        namespace["metadata"]["annotations"] = {marker: digest}
        command("kubectl", "--context", CONTEXT, "create", "-f", "-", payload=json.dumps(namespace))
    def scoped(*args, **kwargs):
        return command("kubectl", "--context", CONTEXT, *args, **kwargs)
    if scoped("-n", "argocd", "get", "application", "lab-target", "--ignore-not-found", "-o", "json").strip():
        raise ValueError("GitOps 등록 후에는 기존 실습 전체 설치기를 적용하지 않습니다")
    current = scoped("-n", NAMESPACE, "get", "deployment", "lab-api", "--ignore-not-found", "-o", "json").strip()
    if current:
        require_manual_deployment(json.loads(current))
    lease = LabLease(scoped, "adoption-" + uuid.uuid4().hex)
    lock_identity = lease.acquire()
    # 설치 실패 때는 공유 잠금을 보존하고 기존 cleanup_required 경로로 운영자가 확인한다.
    result = _finish_deployment(scoped, payload, before, summary, digest)
    result["cluster_lock"] = {**lock_identity, **lease.release(recovery_verified=True)}
    return result


def _finish_deployment(scoped, payload, before, summary, digest):
    scoped("apply", "--dry-run=server", "-f", "-", payload=payload)
    scoped("apply", "-f", "-", payload=payload)
    for name in ("lab-postgres", "lab-redis", "lab-api", "lab-collector"):
        scoped("-n", NAMESPACE, "rollout", "status", "deployment/" + name, "--timeout=180s", timeout=195)
    if production_identity() != before:
        raise ValueError("실습 설치 중 운영 리소스 식별자가 변경됐습니다")
    pods = json.loads(scoped("-n", NAMESPACE, "get", "pods", "-o", "json"))["items"]
    ready = [p for p in pods if any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", []))]
    if len(pods) != 4 or len(ready) != 4:
        raise ValueError("실습 Pod 네 개의 준비 상태가 다릅니다")
    return {**summary, "status": "deployed", "context": CONTEXT, "bundle_sha256": digest,
            "ready_pods": 4, "production_identity_unchanged": True, "production_identity": before,
            "images": {p["metadata"]["labels"]["app"]: p["status"]["containerStatuses"][0]["imageID"] for p in pods},
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "scenarios_executed": [], "external_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description="B-06 고정 namespace 설치. 기본은 로컬 정의 검사만 수행")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = cli_paths(parser, parser.parse_args(), {"manifest": "input", "output": "output"})
    bundle = json.loads(args.manifest.read_bytes())
    summary = validate_lab(bundle)
    if not args.apply:
        write_private_json(args.output, {**summary, "status": "validated_only"})
        print("실습 정의 검사 완료. 설치는 실행하지 않았습니다.")
        return 0
    try:
        result = deploy(bundle)
    except (ValueError, OSError, KeyError, subprocess.TimeoutExpired):
        write_private_json(args.output, {"status": "failed", "namespace": NAMESPACE,
                                         "reason": "deployment_check_failed", "cleanup_required": True})
        print("실습 설치 확인 실패. 실습 namespace만 확인하고 운영 자원을 변경하지 마세요.")
        return 1
    write_private_json(args.output, result)
    print("분리된 실습 Pod 네 개 설치·운영 대상 불변 확인 완료. 장애 시나리오는 실행하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
