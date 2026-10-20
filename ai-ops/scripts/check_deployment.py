"""Helm 결과의 권한·통신·저장 경계를 검사한다. 클러스터에는 접속하지 않는다."""

from __future__ import annotations

import ipaddress
import json
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from ai_ops.b_contracts import BindingRegistry, BRuntime, load_b_settings


ROOT = Path(__file__).resolve().parents[1]


def parse_yaml(raw: str) -> list[dict]:
    # Ruby 표준 YAML 파서는 개발 검사에서만 사용한다. 이미지 의존성은 추가하지 않는다.
    parsed = subprocess.run(["ruby", "-rjson", "-ryaml", "-e", "puts JSON.generate(YAML.load_stream(STDIN.read))"],
                            input=raw, capture_output=True, text=True, check=True)
    return [doc for doc in json.loads(parsed.stdout) if doc]


def render(*args: str, success: bool = True) -> list[dict]:
    result = subprocess.run(["helm", "template", "ai-ops", str(ROOT / "chart"), "--namespace", "ai-ops", *args], capture_output=True, text=True)
    if not success:
        if result.returncode == 0:
            raise AssertionError("허용하지 않은 설정이 렌더링됐다")
        return []
    if result.returncode:
        raise AssertionError("Helm 렌더링 실패: " + result.stderr)
    return parse_yaml(result.stdout)


def verify(documents: list[dict], token_expiration_seconds: int = 3600, observability: bool = False, mode: str = "diagnostics", retention: bool = False) -> None:
    by_kind: dict[str, list[dict]] = {}
    for doc in documents:
        by_kind.setdefault(doc["kind"], []).append(doc)
    expected = {
        "ConfigMap": 1, "ServiceAccount": 1, "Role": 2, "RoleBinding": 2,
        "NetworkPolicy": 2, "Service": 1, "StatefulSet": 1,
    }
    if observability:
        expected.update(NetworkPolicy=3, Service=2, ServiceMonitor=1)
    assert {key: len(value) for key, value in by_kind.items()} == expected, "예상하지 않은 리소스가 포함됐다"
    config = by_kind["ConfigMap"][0]["data"]
    with tempfile.TemporaryDirectory() as directory:
        paths = []
        for name in ("bindings.json", "runtime.json", "metric-queries.json"):
            path = Path(directory) / name
            path.write_text(config[name]); paths.append(path)
        settings = load_b_settings(*paths)
    assert len(settings.bindings.bindings) == 1
    binding = settings.bindings.bindings[0]
    assert config["metric-queries.json"].strip() == (ROOT / "config/metric-queries.json").read_text().strip(), "지표 목록 복사본이 다르다"
    assert settings.runtime.llm_enabled is False
    assert len(settings.runtime.connections) == 2
    expected_rules = {
        binding.target.namespace: [
            {"apiGroups": ["apps"], "resources": ["deployments"], "resourceNames": [binding.target.workload], "verbs": ["get"]},
            {"apiGroups": [""], "resources": ["services"], "resourceNames": [binding.target.service], "verbs": ["get"]},
            {"apiGroups": [""], "resources": ["pods", "events"], "verbs": ["get", "list"]},
            {"apiGroups": ["apps"], "resources": ["replicasets"], "verbs": ["get", "list"]},
            {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
        ],
        binding.argo.namespace: [{"apiGroups": ["argoproj.io"], "resources": ["applications"], "resourceNames": [binding.argo.application], "verbs": ["get"]}],
    }
    for role in by_kind["Role"]:
        assert role["rules"] == expected_rules[role["metadata"]["namespace"]], "읽기 권한 범위가 다르다"
    roles = {(r["metadata"]["namespace"], r["metadata"]["name"]) for r in by_kind["Role"]}
    for role_binding in by_kind["RoleBinding"]:
        assert role_binding["subjects"] == [{"kind": "ServiceAccount", "name": "ai-ops-investigator", "namespace": "ai-ops"}]
        assert (role_binding["metadata"]["namespace"], role_binding["roleRef"]["name"]) in roles
        assert role_binding["roleRef"]["kind"] == "Role"
    assert by_kind["ServiceAccount"][0]["automountServiceAccountToken"] is False
    stateful = by_kind["StatefulSet"][0]["spec"]
    assert stateful["replicas"] == 1
    assert stateful["persistentVolumeClaimRetentionPolicy"] == {"whenDeleted": "Retain", "whenScaled": "Retain"}
    pod = stateful["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "ai-ops-investigator"
    assert pod["securityContext"] == {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
        "fsGroup": 10001, "fsGroupChangePolicy": "OnRootMismatch", "seccompProfile": {"type": "RuntimeDefault"}}
    assert len(pod["containers"]) == 1 and not pod.get("initContainers")
    container = pod["containers"][0]
    module = "ai_ops.worker_runtime" if mode == "worker" else "ai_ops.deployment_check"
    assert container["command"] == ["python", "-m", module]
    assert container.get("args", []) == (([] if mode == "worker" else ["--serve"]) + (["--metrics"] if observability else []) + (["--retention"] if retention else []))
    probe = ["python", "-m", module] + (["--probe"] if mode == "worker" else [])
    for name in ("startupProbe", "readinessProbe"):
        assert container[name]["exec"]["command"] == probe
    assert pod["terminationGracePeriodSeconds"] == (90 if mode == "worker" else 15)
    if mode == "worker":
        assert container["livenessProbe"]["exec"]["command"] == probe
        assert container["livenessProbe"]["failureThreshold"] == 3
    else:
        assert "livenessProbe" not in container
    assert container["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
    assert "@sha256:" in container["image"] and "envFrom" not in container
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert set(volumes) == {"config", "tmp", "kubernetes-identity"}
    identity = volumes["kubernetes-identity"]["projected"]
    assert identity["defaultMode"] == 0o440
    assert identity["sources"] == [{"serviceAccountToken": {"path": "token", "expirationSeconds": token_expiration_seconds}},
        {"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}}]
    claim = stateful["volumeClaimTemplates"][0]["spec"]
    assert claim["accessModes"] == ["ReadWriteOnce"] and claim["storageClassName"]
    selectors = stateful["selector"]["matchLabels"]
    assert next(s for s in by_kind["Service"] if s["metadata"]["name"] == "ai-ops-headless")["spec"] == {"clusterIP": "None", "selector": selectors}
    for policy in by_kind["NetworkPolicy"]:
        assert policy["metadata"]["namespace"] == "ai-ops"
        assert policy["spec"]["podSelector"] == {"matchLabels": selectors}
    deny = next(p["spec"] for p in by_kind["NetworkPolicy"] if p["spec"]["policyTypes"] == ["Ingress", "Egress"])
    assert deny["ingress"] == [] and deny["egress"] == []
    allow = next(p["spec"] for p in by_kind["NetworkPolicy"] if p["spec"]["policyTypes"] == ["Egress"])
    assert len(allow["egress"]) == 3
    dns, api, prom = allow["egress"]
    assert dns["ports"] == [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]
    assert api["ports"] == [{"protocol": "TCP", "port": 443}]
    for peer in api["to"]:
        network = ipaddress.ip_network(peer["ipBlock"]["cidr"])
        assert network.prefixlen == 32 and not network.network_address.is_unspecified
    for peer in dns["to"] + prom["to"]:
        assert peer["namespaceSelector"]["matchLabels"] and peer["podSelector"]["matchLabels"]
    prom_port = urlsplit(settings.runtime.connections[binding.prometheus_connection].base_url).port or 80
    assert prom["ports"] == [{"protocol": "TCP", "port": prom_port}]
    assert all("0.0.0.0/0" not in json.dumps(p) for p in by_kind["NetworkPolicy"])

    if observability:
        assert container["ports"] == [{"name": "metrics", "containerPort": 9091, "protocol": "TCP"}]
        service = next(s for s in by_kind["Service"] if s["metadata"]["name"] == "ai-ops-metrics")
        assert service["spec"] == {"selector": selectors, "ports": [{"name": "metrics", "port": 9091, "targetPort": "metrics", "protocol": "TCP"}]}
        metric_labels = {**selectors, "ai-ops-component": "metrics"}
        assert service["metadata"]["labels"] == metric_labels
        assert by_kind["ServiceMonitor"][0]["spec"] == {
            "namespaceSelector": {"matchNames": ["ai-ops"]}, "selector": {"matchLabels": metric_labels},
            "sampleLimit": 50, "targetLimit": 1,
            "endpoints": [{"port": "metrics", "path": "/metrics", "interval": "30s", "scrapeTimeout": "5s", "honorLabels": False}]}
        ingress = next(p["spec"] for p in by_kind["NetworkPolicy"] if p["spec"]["policyTypes"] == ["Ingress"])
        assert ingress["ingress"] == [{"from": prom["to"], "ports": [{"protocol": "TCP", "port": 9091}]}]
    else:
        assert not container.get("ports")


def main() -> None:
    schema = json.loads((ROOT / "chart/values.schema.json").read_text())
    definitions = {}
    for name, model in (("bindings", BindingRegistry), ("runtime", BRuntime)):
        current = model.model_json_schema()
        definitions.update(current.pop("$defs"))
        assert schema["allOf"][0]["then"]["properties"]["config"]["properties"][name] == current, "배포 schema가 Python 계약과 다르다"
    assert schema["$defs"] == definitions
    addon = ROOT.parent / "cluster-addons/ai-ops"
    application = parse_yaml((addon / "application.yaml").read_text())[0]["spec"]
    project = parse_yaml((addon / "project.yaml").read_text())[0]["spec"]
    assert application["project"] == "ai-ops"
    assert application["source"]["helm"]["valuesObject"] == {"enabled": False}
    assert application["source"]["targetRevision"] == "REPLACE_WITH_REVIEWED_COMMIT_SHA"
    assert "automated" not in application["syncPolicy"]
    assert project["clusterResourceWhitelist"] == []
    assert {d["namespace"] for d in project["destinations"]} == {"ai-ops", "url-shortener", "argocd"}
    assert {r["kind"] for r in project["namespaceResourceWhitelist"]} == {"ConfigMap", "Service", "ServiceAccount", "PersistentVolumeClaim", "StatefulSet", "Role", "RoleBinding", "NetworkPolicy", "ServiceMonitor"}
    for extra in ([], ["-f", str(ROOT / "chart/values.validation.yaml")]):
        subprocess.run(["helm", "lint", str(ROOT / "chart"), "--namespace", "ai-ops", "--strict", *extra], check=True)
    assert render() == [], "기본값에서 리소스를 생성했다"
    args = ["-f", str(ROOT / "chart/values.validation.yaml")]
    documents = render(*args)
    verify(documents)
    # 운영 대상을 등록해도 실습과 동일한 읽기 권한·통신·모델 비활성 경계를 적용한다.
    production = render(*args, "--set", "config.bindings.bindings[0].target.environment=production")
    verify(production)
    config = next(doc for doc in production if doc["kind"] == "ConfigMap")["data"]
    assert json.loads(config["bindings.json"])["bindings"][0]["target"]["environment"] == "production"
    # 단기 토큰도 동일한 권한·통신 경계를 유지하며 수명 범위 밖은 거부한다.
    verify(render(*args, "--set", "identity.tokenExpirationSeconds=600"), token_expiration_seconds=600)
    verify(render(*args, "--set", "mode=worker"), mode="worker")
    verify(render(*args, "--set", "mode=worker", "--set", "observability.enabled=true"), observability=True, mode="worker")
    verify(render(*args, "--set", "mode=worker", "--set", "observability.enabled=true", "--set", "retention.enabled=true"), observability=True, mode="worker", retention=True)
    monitored = render(*args, "--set", "observability.enabled=true")
    verify(monitored, observability=True)
    # 관측 기능이 열려도 발신자 제한·포트·대상 범위가 넓어지면 거부한다.
    for mutate in (
        lambda docs: next(d for d in docs if d["metadata"]["name"] == "ai-ops-metrics-ingress")["spec"]["ingress"][0]["from"][0].pop("podSelector"),
        lambda docs: next(d for d in docs if d["kind"] == "ServiceMonitor")["spec"].update(namespaceSelector={"any": True}),
    ):
        changed = json.loads(json.dumps(monitored)); mutate(changed)
        try:
            verify(changed, observability=True)
        except AssertionError:
            pass
        else:
            raise AssertionError("관측 권한 확대를 놓쳤다")
    rejected = [
        ("identity.tokenExpirationSeconds=599",), ("identity.tokenExpirationSeconds=3601",),
        ("replicaCount=2",), ("mode=unknown",), ("retention.enabled=true",), ("image.digest=latest",),
        ("config.runtime.llm_enabled=true",), ("persistence.storageClassName=",),
        ("network.apiServerCIDRs[0]=0.0.0.0/0",), ("network.dns.podLabels=null",),
        ("network.prometheus.podLabels=null",), ("config.runtime.connections.practice-kubernetes.token_file=/tmp/key",),
        ("config.bindings.bindings[0].target.environment=staging",),
    ]
    for (setting,) in rejected:
        render(*args, "--set", setting, success=False)
    render("--set", "enabled=true", success=False)
    # 검사가 권한·통신의 의도하지 않은 확대도 검출하는지 확인한다.
    for mutate in (
        lambda docs: next(d for d in docs if d["kind"] == "Role")["rules"][0]["verbs"].append("patch"),
        lambda docs: next(d for d in docs if d["kind"] == "StatefulSet")["spec"].update(replicas=2),
        lambda docs: next(d for d in docs if d["kind"] == "NetworkPolicy")["spec"].update(podSelector={}),
    ):
        changed = json.loads(json.dumps(documents)); mutate(changed)
        try:
            verify(changed)
        except AssertionError:
            pass
        else:
            raise AssertionError("위험한 manifest 변경을 놓쳤다")
    print("B-03/B-04 진단·worker 정적 검사 통과: 기본 비활성·실습/운영 읽기 전용 리소스 10개·관측 활성 13개·잘못된 설정 거부·위험 변경 5종 검출. 실환경은 미검증.")


if __name__ == "__main__":
    main()
