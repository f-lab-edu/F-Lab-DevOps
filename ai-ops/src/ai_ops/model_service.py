"""실습 모델 gateway·고정 proxy의 기본 비활성 배포 정의다."""

from __future__ import annotations

import re
import ipaddress


NAMESPACE = "ai-model"
IMAGE_PATTERN = re.compile(r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener-practice-ai-ops@sha256:[a-f0-9]{64}\Z")


def build_service(image: str) -> dict:
    """Secret 값과 활성 스위치를 생성하지 않고 복제수 0만 정의한다."""
    if not IMAGE_PATTERN.fullmatch(image):
        raise ValueError("model_image_digest_required")
    items = []

    def add(kind, name, spec=None, *, api="v1", namespace=NAMESPACE, **fields):
        metadata = {"name": name, "labels": {"app.kubernetes.io/part-of": "ai-model"}}
        if namespace:
            metadata["namespace"] = namespace
        item = {"apiVersion": api, "kind": kind, "metadata": metadata}
        if spec is not None:
            item["spec"] = spec
        item.update(fields)
        items.append(item)
        return item

    namespace = add("Namespace", NAMESPACE, namespace=None)
    namespace["metadata"]["labels"].update({"pod-security.kubernetes.io/enforce": "restricted"})
    for name in ("model-gateway", "model-egress-proxy"):
        add("ServiceAccount", name, automountServiceAccountToken=False)
    add("PersistentVolumeClaim", "model-ledger", {
        "accessModes": ["ReadWriteOnce"], "storageClassName": "ai-ops-gp3-encrypted",
        "resources": {"requests": {"storage": "1Gi"}}})

    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]}}
    pod_security = {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                    "fsGroup": 10001, "fsGroupChangePolicy": "OnRootMismatch",
                    "seccompProfile": {"type": "RuntimeDefault"}}
    for name, module in (("model-gateway", "model_gateway"),
                         ("model-egress-proxy", "model_egress_proxy")):
        gateway = name == "model-gateway"
        volumes = [{"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}}]
        mounts = [{"name": "tmp", "mountPath": "/tmp"}]
        if gateway:
            volumes += [
                {"name": "ledger", "persistentVolumeClaim": {"claimName": "model-ledger"}},
                {"name": "api-key", "secret": {"secretName": "model-openai-key", "defaultMode": 288}},
                {"name": "client", "secret": {"secretName": "model-client-auth", "defaultMode": 288}},
            ]
            mounts += [{"name": "ledger", "mountPath": "/data"},
                       {"name": "api-key", "mountPath": "/etc/ai-model-key", "readOnly": True},
                       {"name": "client", "mountPath": "/etc/ai-model-client", "readOnly": True}]
        container = {"name": name, "image": image, "command": ["python", "-m", "ai_ops." + module],
                     "securityContext": security, "volumeMounts": mounts,
                     "env": [{"name": "HOME", "value": "/tmp"}],
                     "resources": {"requests": {"cpu": "25m", "memory": "64Mi"},
                                   "limits": {"cpu": "250m", "memory": "192Mi"}}}
        if gateway:
            container["env"].append({"name": "AI_MODEL_ENABLED", "value": "false"})
        template = {"metadata": {"labels": {"app": name}},
                    "spec": {"serviceAccountName": name, "automountServiceAccountToken": False,
                             "enableServiceLinks": False, "securityContext": pod_security,
                             "containers": [container], "volumes": volumes}}
        add("Deployment", name, {"replicas": 0, "selector": {"matchLabels": {"app": name}},
                                 "strategy": {"type": "Recreate"}, "template": template}, api="apps/v1")

    add("Service", "model-gateway", {"type": "ClusterIP", "selector": {"app": "model-gateway"},
                                      "ports": [{"name": "http", "port": 8081, "targetPort": 8081}]})
    add("Service", "model-egress-proxy", {"type": "ClusterIP",
                                           "selector": {"app": "model-egress-proxy"},
                                           "ports": [{"name": "connect", "port": 8080,
                                                      "targetPort": 8080}]})
    network = "networking.k8s.io/v1"
    add("NetworkPolicy", "model-deny-all", {"podSelector": {},
                                            "policyTypes": ["Ingress", "Egress"],
                                            "ingress": [], "egress": []}, api=network)
    add("NetworkPolicy", "model-gateway", {
        "podSelector": {"matchLabels": {"app": "model-gateway"}},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": [{"from": [{"namespaceSelector": {"matchLabels": {
            "kubernetes.io/metadata.name": "ai-ci"}},
            "podSelector": {"matchLabels": {"app": "ci-worker"}}}],
            "ports": [{"protocol": "TCP", "port": 8081}]}],
        "egress": [
            {"to": [{"podSelector": {"matchLabels": {
                "app": "model-egress-proxy"}}}],
             "ports": [{"protocol": "TCP", "port": 8080}]},
            {"to": [{"namespaceSelector": {"matchLabels": {
                "kubernetes.io/metadata.name": "kube-system"}},
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
             "ports": [{"protocol": protocol, "port": 53} for protocol in ("UDP", "TCP")]},
        ]}, api=network)
    add("NetworkPolicy", "model-egress-proxy", {
        "podSelector": {"matchLabels": {"app": "model-egress-proxy"}},
        "policyTypes": ["Ingress", "Egress"],
        "ingress": [{"from": [{"podSelector": {"matchLabels": {
            "app": "model-gateway"}}}],
            "ports": [{"protocol": "TCP", "port": 8080}]}],
        "egress": [
            {"to": [{"namespaceSelector": {"matchLabels": {
                "kubernetes.io/metadata.name": "kube-system"}},
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
             "ports": [{"protocol": protocol, "port": 53} for protocol in ("UDP", "TCP")]},
            {"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": [
                "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                "169.254.0.0/16", "127.0.0.0/8", "100.64.0.0/10",
                "192.0.0.0/24", "198.18.0.0/15", "224.0.0.0/4"]}}],
             "ports": [{"protocol": "TCP", "port": 443}]}
        ]}, api=network)
    return {"apiVersion": "v1", "kind": "List", "items": items}


def build_prepared_service(image: str, api_cidrs: list[str]) -> dict:
    """M7-2 읽기 권한과 내부 연결만 추가한다. 복제수·모델 스위치는 그대로 0/false다."""
    if not api_cidrs or len(set(api_cidrs)) != len(api_cidrs) or len(api_cidrs) > 8:
        raise ValueError("model_api_addresses_required")
    for cidr in api_cidrs:
        network = ipaddress.ip_network(cidr, strict=True)
        if (network.version != 4 or network.prefixlen != 32 or not network.is_private or
                network.is_loopback or network.is_link_local or network.is_multicast or
                network.is_unspecified or network.is_reserved):
            raise ValueError("model_api_private_host_required")
    result = build_service(image)
    items = result["items"]
    def named(kind, name, namespace=NAMESPACE):
        return next(x for x in items if x["kind"] == kind and
                    x["metadata"]["name"] == name and
                    x["metadata"].get("namespace") == namespace)
    gateway = named("Deployment", "model-gateway")["spec"]["template"]["spec"]
    gateway["terminationGracePeriodSeconds"] = 90
    gateway["volumes"].append({"name": "identity", "projected": {"sources": [
        {"serviceAccountToken": {"path": "token", "expirationSeconds": 600}},
        {"configMap": {"name": "kube-root-ca.crt", "items": [
            {"key": "ca.crt", "path": "ca.crt"}]}}]}})
    gateway["containers"][0]["volumeMounts"].append({"name": "identity",
        "mountPath": "/var/run/ai-model/kubernetes", "readOnly": True})
    named("NetworkPolicy", "model-gateway")["spec"]["egress"].append({
        "to": [{"ipBlock": {"cidr": cidr}} for cidr in api_cidrs],
        "ports": [{"protocol": "TCP", "port": 443}]})

    permissions = (
        ("ai-ops-lab", (("deployments", "apps", "lab-api"),
                        ("configmaps", "", "lab-collector"))),
        ("ai-ci", (("configmaps", "", "ci-config"),
                   ("configmaps", "", "ci-normal-binding"))),
        ("ai-model", (("deployments", "apps", "model-gateway"),)),
    )
    for namespace, entries in permissions:
        role_name = "model-gateway-current-read"
        metadata = {"name": role_name, "namespace": namespace,
                    "labels": {"app.kubernetes.io/part-of": "ai-model"}}
        items.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
            "metadata": metadata, "rules": [{"apiGroups": [api_group], "resources": [resource],
                "resourceNames": [name], "verbs": ["get"]}
                for resource, api_group, name in entries]})
        items.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
            "metadata": metadata, "roleRef": {"apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role", "name": role_name},
            "subjects": [{"kind": "ServiceAccount", "name": "model-gateway",
                          "namespace": NAMESPACE}]})
    items.append({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": "ci-worker-model-gateway", "namespace": "ai-ci",
                     "labels": {"app.kubernetes.io/part-of": "ai-model"}},
        "spec": {"podSelector": {"matchLabels": {"app": "ci-worker"}},
            "policyTypes": ["Egress"], "egress": [{"to": [{
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": NAMESPACE}},
                "podSelector": {"matchLabels": {"app": "model-gateway"}}}],
                "ports": [{"protocol": "TCP", "port": 8081}]}]}})
    return result
