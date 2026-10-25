"""별도 승인 후에만 사용할 실습 모델 배포 패치의 순수 생성기다."""

from __future__ import annotations

import re
import time

from ai_ops.continuous_model import implementation_hash
from ai_ops.contracts import sha256_json
from ai_ops.model_service import IMAGE_PATTERN
from ai_ops.model_policy import policy_spec
from ai_ops.projected_model_engine import CallPlan


def build_activation_patches(campaign: dict, *, image: str,
                             cluster_ca_sha256: str, exporter_sha256: str,
                             clock=time.time) -> dict:
    """자격 원문 없이 gateway·proxy·worker strategic merge patch를 생성한다."""
    if not IMAGE_PATTERN.fullmatch(image):
        raise ValueError("model_activation_image_rejected")
    if not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in
               (cluster_ca_sha256, exporter_sha256)):
        raise ValueError("model_activation_hash_rejected")
    binding = campaign["binding"]
    now = clock()
    if (campaign["state"] != "approved" or
            not campaign["created"] <= now < campaign["expires"] or
            not campaign["actor"] or
            campaign["binding_hash"] != sha256_json(binding) or
            binding["implementation_hash"] != implementation_hash() or
            binding["image_digest"] != image.split("@", 1)[1]):
        raise ValueError("model_activation_scope_changed")
    plan = CallPlan.model_validate(binding["plan"])
    selected = policy_spec(binding["policy"])
    if plan.execution_mode != "live" or binding["prompt"] != selected.prompt:
        raise ValueError("model_activation_live_approval_required")

    def deployment(namespace, name, container):
        return {"apiVersion": "apps/v1", "kind": "Deployment",
                "metadata": {"namespace": namespace, "name": name},
                "spec": {"replicas": 1, "template": {"spec": {
                    "containers": [container]}}}}

    gateway = deployment("ai-model", "model-gateway", {
        "name": "model-gateway", "image": image,
        "env": [{"name": "AI_MODEL_ENABLED", "value": "true"},
                {"name": "AI_MODEL_CAMPAIGN_ID", "value": campaign["id"]},
                {"name": "AI_MODEL_IMAGE_DIGEST", "value": binding["image_digest"]},
                {"name": "AI_MODEL_CLUSTER_FINGERPRINT", "value": binding["cluster_fingerprint"]},
                {"name": "AI_MODEL_CA_SHA256", "value": cluster_ca_sha256}],
        "readinessProbe": {"httpGet": {"path": "/health", "port": 8081},
                           "periodSeconds": 10, "timeoutSeconds": 2},
        "livenessProbe": {"httpGet": {"path": "/health", "port": 8081},
                          "periodSeconds": 20, "timeoutSeconds": 2}})
    proxy = deployment("ai-model", "model-egress-proxy",
                       {"name": "model-egress-proxy", "image": image})
    worker = deployment("ai-ci", "ci-worker", {
        "name": "ci-worker", "image": image,
        "env": [{"name": "AI_MODEL_BRIDGE_ENABLED", "value": "true"},
                {"name": "AI_MODEL_CAMPAIGN_ID", "value": campaign["id"]},
                {"name": "AI_MODEL_POLICY", "value": selected.policy},
                {"name": "AI_MODEL_CLUSTER_FINGERPRINT", "value": binding["cluster_fingerprint"]},
                {"name": "AI_MODEL_EXPORTER_SHA256", "value": exporter_sha256}],
        "volumeMounts": [{"name": "model-client-auth",
                          "mountPath": "/etc/ai-model-client", "readOnly": True}]})
    worker["spec"]["template"]["spec"]["volumes"] = [{"name": "model-client-auth",
        "secret": {"secretName": "model-client-auth", "defaultMode": 288}}]
    return {"gateway": gateway, "proxy": proxy, "worker": worker}
