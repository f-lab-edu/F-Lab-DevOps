"""재설치할 클러스터의 실제 주소와 등록 이미지를 읽어 비공개 Helm 입력만 만든다."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess

from ai_ops.b_contracts import BindingRegistry, BRuntime
from ai_ops.path_policy import permitted_path, write_private_json

ROOT = Path(__file__).resolve().parents[2]
ACCOUNT = "716174522908"
REGION = "ap-northeast-2"
CLUSTER = "urlshortener"
REPOSITORY = "urlshortener-practice-ai-ops"


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise ValueError("재설치 사전 조회 실패: " + args[0])
    return result.stdout


def read_json(*args: str):
    return json.loads(command(*args))


def build_values(base: dict, registration: dict, service: dict, endpoints: dict) -> dict:
    """과거 주소를 상속하지 않고 현재 API의 Service·Endpoint /32로 교체한다."""
    expected_repo = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{REPOSITORY}"
    if (registration.get("published") is not True or registration.get("repository") != expected_repo
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", registration.get("registry_digest", ""))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", registration.get("image_id", ""))
            or not re.fullmatch(r"[0-9a-f]{40}", registration.get("source_revision", ""))):
        raise ValueError("등록 이미지 identity가 유효하지 않습니다")
    addresses = [service["spec"]["clusterIP"]]
    if not any(port["port"] == 443 and port.get("protocol", "TCP") == "TCP" for port in service["spec"]["ports"]):
        raise ValueError("API Service 443을 확인하지 못했습니다")
    ready = []
    for item in endpoints["items"]:
        if item.get("addressType") != "IPv4":
            continue
        if not any(p.get("port") == 443 and p.get("protocol", "TCP") == "TCP" for p in item["ports"]):
            raise ValueError("API Endpoint 포트가 예상과 다릅니다")
        for endpoint in item["endpoints"]:
            if endpoint.get("conditions", {}).get("ready") is not False:
                ready.extend(endpoint["addresses"])
    if not ready:
        raise ValueError("Ready API Endpoint가 없습니다")
    addresses += ready
    cidrs = []
    for value in sorted(set(addresses)):
        ip = ipaddress.ip_address(value)
        if ip.version != 4 or not ip.is_private or ip.is_unspecified or ip.is_loopback:
            raise ValueError("API는 확인한 내부 IPv4만 허용합니다")
        cidrs.append(str(ip) + "/32")
    values = json.loads(json.dumps(base))
    values.update(enabled=False)
    values["image"].update(repository=expected_repo, digest=registration["registry_digest"])
    values["persistence"]["storageClassName"] = "ai-ops-gp3-encrypted"
    values["network"]["apiServerCIDRs"] = cidrs
    values["config"]["bindings"] = json.loads((ROOT / "ai-ops/config/targets.production.json").read_text())
    BindingRegistry.model_validate(values["config"]["bindings"])
    BRuntime.model_validate(values["config"]["runtime"])
    return values


def main():
    parser = argparse.ArgumentParser(description="조회만 수행하고 재설치용 Helm 값을 생성한다")
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        registration_path = permitted_path(args.registration, "input")
        output = permitted_path(args.output, "output")
        registration = json.loads(registration_path.read_text())
        if command("aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text").strip() != ACCOUNT:
            raise ValueError("AWS 계정이 다릅니다")
        context = command("kubectl", "config", "current-context").strip()
        if context != f"arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{CLUSTER}":
            raise ValueError("Kubernetes 컨텍스트가 다릅니다")
        revision = registration.get("source_revision", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("등록 소스 SHA가 유효하지 않습니다")
        command("git", "-C", str(ROOT), "merge-base", "--is-ancestor", revision, "origin/main")
        for name in ("ai-ops/Dockerfile", "ai-ops/requirements.lock", "ai-ops/runbooks/index.yaml"):
            raw = subprocess.run(["git", "-C", str(ROOT), "show", revision + ":" + name], capture_output=True, check=True).stdout
            if hashlib.sha256(raw).hexdigest() != registration.get("source_hashes", {}).get(name):
                raise ValueError("등록 artifact와 소스 해시가 다릅니다")
        command("git", "-C", str(ROOT), "cat-file", "-e", revision + ":ai-ops/src/ai_ops/observability.py")
        digest = registration.get("registry_digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("등록 digest가 유효하지 않습니다")
        scan = read_json("aws", "ecr", "describe-image-scan-findings", "--region", REGION,
                         "--repository-name", REPOSITORY, "--image-id", "imageDigest=" + digest, "--output", "json")
        if scan["imageScanStatus"]["status"] != "COMPLETE" or sum(scan["imageScanFindings"]["findingSeverityCounts"].values()):
            raise ValueError("이미지 검사가 완료되지 않았거나 지적 사항이 있습니다")
        manifest = read_json("aws", "ecr", "batch-get-image", "--region", REGION, "--repository-name", REPOSITORY,
                             "--image-ids", "imageDigest=" + digest, "--output", "json")
        if json.loads(manifest["images"][0]["imageManifest"])["config"]["digest"] != registration["image_id"]:
            raise ValueError("ECR manifest와 등록 image ID가 다릅니다")
        base_text = (ROOT / "ai-ops/config/b3-values.preflight.yaml").read_text()
        base = json.loads("\n".join(line for line in base_text.splitlines() if not line.lstrip().startswith("#")))
        service = read_json("kubectl", "-n", "default", "get", "service", "kubernetes", "-o", "json")
        endpoints = read_json("kubectl", "-n", "default", "get", "endpointslice", "-l", "kubernetes.io/service-name=kubernetes", "-o", "json")
        read_json("kubectl", "-n", "url-shortener", "get", "deployment", "url-shortener-api", "-o", "json")
        read_json("kubectl", "-n", "url-shortener", "get", "service", "url-shortener-svc", "-o", "json")
        read_json("kubectl", "-n", "monitoring", "get", "prometheus", "kube-prometheus-stack-prometheus", "-o", "json")
        write_private_json(output, build_values(base, registration, service, endpoints))
        print("재설치 입력 생성 완료. 설치·삭제는 실행하지 않았습니다. 관측 overlay를 함께 적용해야 활성화됩니다.")
    except Exception:
        raise SystemExit("재설치 준비 실패: 계정·컨텍스트·등록 artifact·ECR 검사·소스·클러스터 준비 상태를 확인하세요.") from None


if __name__ == "__main__":
    main()
