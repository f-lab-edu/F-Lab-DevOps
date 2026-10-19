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


def verify(documents: list[dict]) -> None:
    by_kind: dict[str, list[dict]] = {}
    for doc in documents:
        by_kind.setdefault(doc["kind"], []).append(doc)
    assert {key: len(value) for key, value in by_kind.items()} == {
        "ConfigMap": 1, "ServiceAccount": 1, "Role": 2, "RoleBinding": 2,
        "NetworkPolicy": 2, "Service": 1, "StatefulSet": 1,
    }, "예상하지 않은 리소스가 포함됐다"
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
    assert container["command"] == ["python", "-m", "ai_ops.deployment_check"] and container["args"] == ["--serve"]
    assert container["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
    assert "@sha256:" in container["image"] and "envFrom" not in container
    volumes = {v["name"]: v for v in pod["volumes"]}
    assert set(volumes) == {"config", "tmp", "kubernetes-identity"}
    identity = volumes["kubernetes-identity"]["projected"]
    assert identity["defaultMode"] == 0o440
    assert identity["sources"] == [{"serviceAccountToken": {"path": "token", "expirationSeconds": 3600}},
        {"configMap": {"name": "kube-root-ca.crt", "items": [{"key": "ca.crt", "path": "ca.crt"}]}}]
    claim = stateful["volumeClaimTemplates"][0]["spec"]
    assert claim["accessModes"] == ["ReadWriteOnce"] and claim["storageClassName"]
    selectors = stateful["selector"]["matchLabels"]
    assert by_kind["Service"][0]["spec"] == {"clusterIP": "None", "selector": selectors}
    for policy in by_kind["NetworkPolicy"]:
        assert policy["metadata"]["namespace"] == "ai-ops"
        assert policy["spec"]["podSelector"] == {"matchLabels": selectors}
    deny = next(p["spec"] for p in by_kind["NetworkPolicy"] if "Ingress" in p["spec"]["policyTypes"])
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
    assert {r["kind"] for r in project["namespaceResourceWhitelist"]} == {"ConfigMap", "Service", "ServiceAccount", "PersistentVolumeClaim", "StatefulSet", "Role", "RoleBinding", "NetworkPolicy"}
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
    rejected = [
        ("replicaCount=2",), ("mode=worker",), ("image.digest=latest",),
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
    print("B-03 정적 검사 통과: 기본 비활성·실습/운영 읽기 전용 리소스 10개·잘못된 설정 11종 거부·위험 변경 3종 검출. 실환경은 미검증.")


if __name__ == "__main__":
    main()
