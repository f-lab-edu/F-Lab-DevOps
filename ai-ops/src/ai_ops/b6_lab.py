"""운영 대상과 분리된 일회성 B-06 실습 리소스를 생성하고 경계를 검사한다."""

from __future__ import annotations

from collections import Counter
import ipaddress
from pathlib import Path
import re
import secrets

from pydantic import Field, model_validator

from ai_ops.b_contracts import BindingRegistry, BRuntime
from ai_ops.contracts import StrictModel


NAMESPACE = "ai-ops-lab"
CLUSTER = "urlshortener"
CONTEXT = "arn:aws:eks:ap-northeast-2:716174522908:cluster/urlshortener"
PURPOSE = {"ai-ops-purpose": "b6-isolated-lab"}
ROOT = Path(__file__).resolve().parents[2]
ECR = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/"


class LabInputs(StrictModel):
    app_image: str
    collector_image: str
    postgres_image: str
    redis_image: str
    api_server_cidrs: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def check_images_and_network(self):
        """실제 검증한 이미지 digest와 API 주소만 받으며 연결 URL은 고정한다."""
        for field, repository in (
            ("app_image", ECR + "urlshortener"),
            ("collector_image", ECR + "urlshortener-practice-ai-ops"),
            ("postgres_image", "docker.io/library/postgres"),
            ("redis_image", "docker.io/library/redis"),
        ):
            if not re.fullmatch(re.escape(repository) + r"@sha256:[a-f0-9]{64}", getattr(self, field)):
                raise ValueError("승인한 저장소의 digest 고정 이미지가 필요합니다")
        for cidr in self.api_server_cidrs:
            network = ipaddress.ip_network(cidr, strict=True)
            if network.version != 4 or network.prefixlen != 32:
                raise ValueError("API 통신은 확인한 IPv4 /32만 허용합니다")
        if len(set(self.api_server_cidrs)) != len(self.api_server_cidrs):
            raise ValueError("중복 API 주소는 허용하지 않습니다")
        return self


def _object(kind, name, spec=None, *, api="v1", **fields):
    value = {"apiVersion": api, "kind": kind,
             "metadata": {"name": name, "namespace": NAMESPACE, "labels": dict(PURPOSE)}, **fields}
    if spec is not None:
        value["spec"] = spec
    return value


def _selector(component):
    return {"matchLabels": {"app": "lab-" + component}}


def _security():
    return {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]}}


def build_lab(inputs: LabInputs, *, password: str | None = None) -> dict:
    """실습 전용 Secret이 포함되므로 호출자는 결과를 비공개 파일에만 저장한다."""
    password = password or secrets.token_hex(24)
    if not re.fullmatch(r"[a-f0-9]{48}", password):
        raise ValueError("실습 암호 형식이 유효하지 않습니다")
    documents = [{"apiVersion": "v1", "kind": "Namespace", "metadata": {
        "name": NAMESPACE, "labels": {**PURPOSE, "pod-security.kubernetes.io/enforce": "restricted",
                                      "pod-security.kubernetes.io/enforce-version": "v1.35"}}}]
    documents += [_object("ResourceQuota", "lab-budget", {"hard": {
        "requests.cpu": "500m", "requests.memory": "768Mi", "limits.cpu": "2",
        "limits.memory": "2Gi", "pods": "6", "persistentvolumeclaims": "0",
        "services.loadbalancers": "0", "services.nodeports": "0"}})]
    for name in ("lab-workload", "lab-investigator"):
        documents.append(_object("ServiceAccount", name, automountServiceAccountToken=False))
    database_url = f"postgresql://lab:{password}@lab-postgres.{NAMESPACE}.svc:5432/lab"
    documents.append(_object("Secret", "lab-database", type="Opaque", stringData={
        "POSTGRES_PASSWORD": password, "DATABASE_URL": database_url,
        "DATABASE_READ_URL": database_url}))
    binding = BindingRegistry.model_validate({"schema_version": 1, "bindings": [{
        "alias": "url-shortener-b6-lab",
        "target": {"cluster_id": CLUSTER, "namespace": NAMESPACE, "workload": "lab-api",
                   "service": "lab-api", "environment": "practice"},
        "pod_selector": {"app": "lab-api"}, "container": "api",
        "kubernetes_connection": "lab-kubernetes", "prometheus_connection": "lab-prometheus",
        # 없는 Argo Application은 missing으로 남기며 가짜 배포 이력을 만들지 않는다.
        "argo": {"namespace": NAMESPACE, "application": "lab-target",
                 "repo_url": "https://github.com/f-lab-edu/F-Lab-DevOps.git", "path": "ai-ops/lab"},
        "routes": ["/items", "/items/{item_id}", "/items/_db"],
        "operations": ["select_one", "select_all", "insert", "delete"]}]})
    runtime = BRuntime.model_validate({"schema_version": 1, "connections": {
        "lab-kubernetes": {"kind": "kubernetes", "base_url": "https://kubernetes.default.svc:443",
                           "ca_file": "/var/run/ai-ops/kubernetes/ca.crt",
                           "token_file": "/var/run/ai-ops/kubernetes/token"},
        "lab-prometheus": {"kind": "prometheus", "base_url": "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"}}})
    documents.append(_object("ConfigMap", "lab-collector", data={
        "bindings.json": binding.model_dump_json(), "runtime.json": runtime.model_dump_json(),
        "metric-queries.json": (ROOT / "config/metric-queries.json").read_text()}))
    rules = [
        {"apiGroups": ["apps"], "resources": ["deployments"], "resourceNames": ["lab-api"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["services"], "resourceNames": ["lab-api"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["pods", "events"], "verbs": ["get", "list"]},
        {"apiGroups": ["apps"], "resources": ["replicasets"], "verbs": ["get", "list"]},
        {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
        {"apiGroups": ["argoproj.io"], "resources": ["applications"], "resourceNames": ["lab-target"], "verbs": ["get"]},
    ]
    documents += [_object("Role", "lab-read-only", api="rbac.authorization.k8s.io/v1", rules=rules),
                  _object("RoleBinding", "lab-read-only", api="rbac.authorization.k8s.io/v1",
                          subjects=[{"kind": "ServiceAccount", "name": "lab-investigator", "namespace": NAMESPACE}],
                          roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "lab-read-only"})]
    for component, image, uid, memory, cpu in (
        ("postgres", inputs.postgres_image, 999, "256Mi", "100m"),
        ("redis", inputs.redis_image, 999, "64Mi", "50m"),
        ("api", inputs.app_image, 1000, "128Mi", "100m"),
        ("collector", inputs.collector_image, 10001, "128Mi", "100m"),
    ):
        container = {"name": "api" if component == "api" else component, "image": image,
                     "imagePullPolicy": "IfNotPresent", "securityContext": _security(),
                     "resources": {"requests": {"cpu": cpu, "memory": memory},
                                   "limits": {"cpu": "250m" if component == "redis" else "500m",
                                              "memory": {"api": "384Mi", "redis": "128Mi"}.get(component, "512Mi")}},
                     "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}]}
        pod = {"serviceAccountName": "lab-investigator" if component == "collector" else "lab-workload",
               "automountServiceAccountToken": False, "enableServiceLinks": False,
               "securityContext": {"runAsNonRoot": True, "runAsUser": uid, "runAsGroup": uid,
                                   "fsGroup": uid, "seccompProfile": {"type": "RuntimeDefault"}},
               "containers": [container], "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}]}
        if component == "postgres":
            container.update(env=[{"name": "POSTGRES_USER", "value": "lab"}, {"name": "POSTGRES_DB", "value": "lab"},
                                  {"name": "POSTGRES_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "lab-database", "key": "POSTGRES_PASSWORD"}}},
                                  {"name": "PGDATA", "value": "/var/lib/postgresql/data/pgdata"}],
                             ports=[{"name": "postgres", "containerPort": 5432}],
                             readinessProbe={"exec": {"command": ["pg_isready", "-U", "lab", "-d", "lab"]}, "periodSeconds": 5})
            container["volumeMounts"] += [{"name": "data", "mountPath": "/var/lib/postgresql/data"},
                                          {"name": "socket", "mountPath": "/var/run/postgresql"}]
            pod["volumes"] += [{"name": "data", "emptyDir": {"sizeLimit": "512Mi"}},
                               {"name": "socket", "emptyDir": {"sizeLimit": "16Mi"}}]
        elif component == "redis":
            container.update(command=["redis-server", "--save", "", "--appendonly", "no", "--maxmemory", "64mb", "--maxmemory-policy", "noeviction"],
                             ports=[{"name": "redis", "containerPort": 6379}],
                             readinessProbe={"exec": {"command": ["redis-cli", "ping"]}, "periodSeconds": 5})
            container["volumeMounts"].append({"name": "data", "mountPath": "/data"})
            pod["volumes"].append({"name": "data", "emptyDir": {"sizeLimit": "128Mi"}})
        elif component == "api":
            container.update(env=[*({"name": name, "valueFrom": {"secretKeyRef": {"name": "lab-database", "key": name}}}
                                     for name in ("DATABASE_URL", "DATABASE_READ_URL")),
                                  {"name": "REDIS_URL", "value": f"redis://lab-redis.{NAMESPACE}.svc:6379/0"},
                                  {"name": "APP_ENV", "value": "practice"},
                                  {"name": "ENABLE_FAULT_INJECTION", "value": "true"},
                                  {"name": "MAX_FAULT_DELAY_MS", "value": "1000"},
                                  {"name": "REPLICA_CONSISTENCY_WINDOW_SECONDS", "value": "5"},
                                  {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}],
                             ports=[{"name": "http", "containerPort": 8000}],
                             readinessProbe={"httpGet": {"path": "/readyz", "port": "http"}, "periodSeconds": 5},
                             startupProbe={"httpGet": {"path": "/readyz", "port": "http"}, "periodSeconds": 5, "failureThreshold": 36})
        else:
            container.update(command=["python", "-c", "import signal; signal.pause()"],
                             env=[{"name": "PYTHONDONTWRITEBYTECODE", "value": "1"}, {"name": "HOME", "value": "/tmp"}],
                             readinessProbe={"exec": {"command": ["python", "-c", "from pathlib import Path; assert Path('/etc/ai-ops/runtime.json').is_file()"]}, "periodSeconds": 10})
            container["volumeMounts"] += [{"name": "config", "mountPath": "/etc/ai-ops", "readOnly": True},
                                          {"name": "identity", "mountPath": "/var/run/ai-ops/kubernetes", "readOnly": True},
                                          {"name": "data", "mountPath": "/data"}]
            pod["volumes"] += [{"name": "config", "configMap": {"name": "lab-collector"}},
                               {"name": "identity", "projected": {"defaultMode": 288, "sources": [
                                   {"serviceAccountToken": {"path": "token", "expirationSeconds": 600}},
                                   {"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}}]}},
                               {"name": "data", "emptyDir": {"sizeLimit": "512Mi"}}]
        documents.append(_object("Deployment", "lab-" + component, {"replicas": 1, "strategy": {"type": "Recreate"},
            "selector": _selector(component), "template": {"metadata": {"labels": {**PURPOSE, "app": "lab-" + component}}, "spec": pod}}, api="apps/v1"))
    for component, port in (("api", 8000), ("postgres", 5432), ("redis", 6379)):
        service = _object("Service", "lab-" + component, {"type": "ClusterIP", "selector": {"app": "lab-" + component},
                           "ports": [{"name": "http" if component == "api" else component, "port": port, "targetPort": port}]})
        service["metadata"]["labels"]["app"] = "lab-" + component
        documents.append(service)
    documents.append(_object("ServiceMonitor", "lab-api", {"namespaceSelector": {"matchNames": [NAMESPACE]},
        "selector": _selector("api"), "sampleLimit": 2000, "targetLimit": 1,
        "endpoints": [{"port": "http", "path": "/metrics/", "interval": "30s", "scrapeTimeout": "5s", "honorLabels": False}]}, api="monitoring.coreos.com/v1"))
    documents[-1]["metadata"]["labels"]["release"] = "kube-prometheus-stack"
    policies = [
        ("lab-deny", {"podSelector": {}, "policyTypes": ["Ingress", "Egress"], "ingress": [], "egress": []}),
        ("lab-api", {"podSelector": _selector("api"), "policyTypes": ["Ingress", "Egress"],
                     "ingress": [{"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                                             "podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}}}], "ports": [{"port": 8000}]}],
                     "egress": [{"to": [{"podSelector": _selector(component)}], "ports": [{"port": port}]} for component, port in (("postgres", 5432), ("redis", 6379))]}),
        ("lab-database", {"podSelector": {"matchExpressions": [{"key": "app", "operator": "In", "values": ["lab-postgres", "lab-redis"]}]},
                          "policyTypes": ["Ingress"], "ingress": [{"from": [{"podSelector": _selector("api")}], "ports": [{"port": 5432}, {"port": 6379}]}]}),
        ("lab-dns", {"podSelector": {"matchExpressions": [{"key": "app", "operator": "In", "values": ["lab-api", "lab-collector"]}]},
                     "policyTypes": ["Egress"], "egress": [{"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                                                                 "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}], "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]}]}),
        ("lab-collector", {"podSelector": _selector("collector"), "policyTypes": ["Egress"], "egress": [
            {"to": [{"ipBlock": {"cidr": cidr}} for cidr in inputs.api_server_cidrs], "ports": [{"port": 443}]},
            {"to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                     "podSelector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}}}], "ports": [{"port": 9090}]}]}),
    ]
    documents += [_object("NetworkPolicy", name, spec, api="networking.k8s.io/v1") for name, spec in policies]
    return {"apiVersion": "v1", "kind": "List", "items": documents}


def validate_lab(bundle: dict) -> dict:
    """설치 전 모든 리소스를 고정 정의와 대조해 운영 자원·권한 확대를 거부한다."""
    if bundle.get("apiVersion") != "v1" or bundle.get("kind") != "List" or set(bundle) != {"apiVersion", "kind", "items"}:
        raise ValueError("실습 리소스 목록 형식이 다릅니다")
    try:
        indexed = {(item["kind"], item["metadata"]["name"]): item for item in bundle["items"]}
        if len(indexed) != len(bundle["items"]):
            raise ValueError("중복 실습 리소스")
        images = {name: indexed[("Deployment", "lab-" + name)]["spec"]["template"]["spec"]["containers"][0]["image"]
                  for name in ("api", "collector", "postgres", "redis")}
        cidrs = [item["ipBlock"]["cidr"] for item in indexed[("NetworkPolicy", "lab-collector")]["spec"]["egress"][0]["to"]]
        inputs = LabInputs(app_image=images["api"], collector_image=images["collector"],
                           postgres_image=images["postgres"], redis_image=images["redis"], api_server_cidrs=cidrs)
        password = indexed[("Secret", "lab-database")]["stringData"]["POSTGRES_PASSWORD"]
        expected = build_lab(inputs, password=password)
        if sorted(bundle["items"], key=lambda d: (d["kind"], d["metadata"]["name"])) != sorted(expected["items"], key=lambda d: (d["kind"], d["metadata"]["name"])):
            raise ValueError("실습 정의에 허용하지 않은 변경이 있습니다")
    except (KeyError, TypeError) as exc:
        raise ValueError("실습 필수 리소스가 누락됐습니다") from exc
    return {"namespace": NAMESPACE, "resources": dict(Counter(item["kind"] for item in bundle["items"])),
            "requests": {"cpu": "350m", "memory": "576Mi"}, "persistent_storage": False,
            "external_model_enabled": False, "database_topology": "single_primary_no_replica"}
