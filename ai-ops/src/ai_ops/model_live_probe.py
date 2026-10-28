"""모델 전송 직전에 현재 실습 대상·설정·gateway 이미지를 읽기 전용으로 확인한다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ai_ops.b_contracts import BRuntime, BindingRegistry, MetricCatalog, ReadRequest
from ai_ops.contracts import sha256_json
from ai_ops.live_collectors.transport import HTTPReadTransport
from ai_ops.model_projection import TARGET
from ai_ops.model_sequence_projection import SEQUENCE_POLICY
from ai_ops.model_service import IMAGE_PATTERN
from ai_ops.normal_ci_contract import NormalCIBinding, executor_hash


TOKEN = Path("/var/run/ai-model/kubernetes/token")
CA = Path("/var/run/ai-model/kubernetes/ca.crt")


class ModelLiveProbe:
    """고정 Kubernetes GET으로 승인 시점과 현재 자원을 대조한다."""

    def __init__(self, *, expected_cluster_fingerprint: str, expected_ca_sha256: str,
                 token: Path = TOKEN, ca: Path = CA, transport=None):
        self.expected_cluster_fingerprint = expected_cluster_fingerprint
        self.expected_ca_sha256 = expected_ca_sha256
        self.ca = ca
        runtime = BRuntime(schema_version=1, connections={"model-kubernetes": {
            "kind": "kubernetes", "base_url": "https://kubernetes.default.svc:443",
            "ca_file": str(ca), "token_file": str(token)}})
        self.transport = transport or HTTPReadTransport(runtime)

    def _read(self, path: str, limit: int = 65536) -> dict:
        response = self.transport.get(ReadRequest("model-kubernetes", path),
                                      timeout_seconds=5, max_bytes=limit)
        if response.status_code != 200 or len(response.body) > limit:
            raise ValueError("model_probe_read_rejected")
        value = json.loads(response.body)
        if not isinstance(value, dict):
            raise ValueError("model_probe_response_rejected")
        return value

    def verify(self, binding: dict):
        """다른 클러스터/Deployment/CI 설정/이미지로 바뀌면 보내기 전에 중단한다."""
        if binding["cluster_fingerprint"] != self.expected_cluster_fingerprint:
            raise ValueError("model_probe_cluster_changed")
        raw_ca = self.ca.read_bytes()
        if hashlib.sha256(raw_ca).hexdigest() != self.expected_ca_sha256:
            raise ValueError("model_probe_ca_changed")
        app = self._read("/apis/apps/v1/namespaces/ai-ops-lab/deployments/lab-api", 16384)
        if app.get("metadata", {}).get("uid") != binding["deployment_uid"]:
            raise ValueError("model_probe_deployment_changed")
        normal_policy = binding.get("policy") == SEQUENCE_POLICY
        config_path = ("/api/v1/namespaces/ai-ops-lab/configmaps/lab-collector"
                       if normal_policy else "/api/v1/namespaces/ai-ci/configmaps/ci-config")
        config = self._read(config_path)
        data = config.get("data", {})
        if set(data) != {"bindings.json", "runtime.json", "metric-queries.json"}:
            raise ValueError("model_probe_config_changed")
        try:
            parsed = [model.model_validate_json(data[name]) for name, model in (
                ("bindings.json", BindingRegistry), ("runtime.json", BRuntime),
                ("metric-queries.json", MetricCatalog))]
        except (ValueError, TypeError, KeyError):
            raise ValueError("model_probe_config_changed") from None
        registry, runtime, _ = parsed
        if (len(registry.bindings) != 1 or
                registry.bindings[0].target.model_dump(mode="json") != TARGET or
                runtime.llm_enabled is not False or
                sha256_json([item.model_dump(mode="json") for item in parsed]) != binding["config_hash"]):
            raise ValueError("model_probe_config_changed")
        if normal_policy:
            current = self._read("/api/v1/namespaces/ai-ci/configmaps/ci-normal-binding")
            if set(current.get("data", {})) != {"normal-ci-binding.json"}:
                raise ValueError("model_probe_normal_binding_changed")
            try:
                normal = NormalCIBinding.model_validate_json(
                    current["data"]["normal-ci-binding.json"])
            except (ValueError, TypeError, KeyError):
                raise ValueError("model_probe_normal_binding_changed") from None
            if (normal.cluster_fingerprint != binding["cluster_fingerprint"] or
                    normal.app.deployment_uid != binding["deployment_uid"] or
                    normal.config_hash != binding["config_hash"] or
                    normal.executor_sha256 != executor_hash()):
                raise ValueError("model_probe_normal_binding_changed")
        gateway = self._read("/apis/apps/v1/namespaces/ai-model/deployments/model-gateway", 16384)
        containers = gateway.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        image = containers[0].get("image", "") if len(containers) == 1 else ""
        if (gateway.get("spec", {}).get("replicas") != 1 or len(containers) != 1 or
                containers[0].get("name") != "model-gateway" or
                not IMAGE_PATTERN.fullmatch(image) or
                image.split("@")[-1] != binding["image_digest"]):
            raise ValueError("model_probe_image_changed")
