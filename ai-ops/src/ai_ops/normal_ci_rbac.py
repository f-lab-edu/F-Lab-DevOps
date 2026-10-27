"""정상 관측 CI의 현재 실습 Pod 두 개와 기존 공유 잠금만 허용한다."""

from ai_ops.b6_coordination import LEASE_NAME
from ai_ops.b6_lab import NAMESPACE, PURPOSE
from ai_ops.normal_ci_contract import GROUP, NormalCIBinding


def build_normal_rbac(binding: NormalCIBinding) -> dict:
    """Pod 교체 후 기존 권한을 넓히지 않고 관리자 재고정을 요구한다."""
    binding = NormalCIBinding.model_validate_json(binding.model_dump_json())
    items = []
    for namespace, name, rules in (
        (NAMESPACE, GROUP, [
            {"apiGroups": ["apps"], "resources": ["deployments"],
             "resourceNames": ["lab-api", "lab-collector", "lab-postgres", "lab-redis"], "verbs": ["get"]},
            {"apiGroups": ["apps"], "resources": ["replicasets"], "verbs": ["get", "list"]},
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
            {"apiGroups": [""], "resources": ["pods"],
             "resourceNames": [binding.app.name, binding.collector.name], "verbs": ["get"]},
            {"apiGroups": [""], "resources": ["pods/exec"],
             "resourceNames": [binding.app.name, binding.collector.name], "verbs": ["create"]},
            {"apiGroups": ["coordination.k8s.io"], "resources": ["leases"],
             "resourceNames": [LEASE_NAME], "verbs": ["get", "update"]},
        ]),
        ("argocd", "b6-normal-argo-read", [
            {"apiGroups": ["argoproj.io"], "resources": ["applications"],
             "resourceNames": ["lab-target"], "verbs": ["get"]},
        ]),
    ):
        def obj(kind, **extra):
            return {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": kind,
                "metadata": {"namespace": namespace, "name": name, "labels": dict(PURPOSE)}, **extra}
        items += [obj("Role", rules=rules), obj("RoleBinding",
            roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": name},
            subjects=[{"kind": "Group", "name": GROUP, "apiGroup": "rbac.authorization.k8s.io"}])]
    return {"apiVersion": "v1", "kind": "List", "items": items}
