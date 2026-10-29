"""실습 앱 하나의 GitOps 정의와 초기 수동 동기화·최소 권한을 준비한다."""

from __future__ import annotations

from copy import deepcopy
import json
import re

from ai_ops.b6_coordination import LEASE_NAME, OWNER_KEY
from ai_ops.b6_lab import NAMESPACE, PURPOSE, validate_lab


ARGO_NAMESPACE = "argocd"
APPLICATION = "lab-target"
PROJECT = "b6-lab"
GITOPS_PATH = "ai-ops/lab"
REPOSITORY = "https://github.com/f-lab-edu/F-Lab-DevOps.git"
CONTROLLER = "system:serviceaccount:argocd:argocd-application-controller"
POLICY = "b6-lab-argo-deployment-scope"


def _object(kind, name, namespace, *, api="argoproj.io/v1alpha1", **fields):
    return {"apiVersion": api, "kind": kind, "metadata": {
        "name": name, **({"namespace": namespace} if namespace else {}), "labels": dict(PURPOSE)}, **fields}


def build_gitops(bundle: dict, revision: str) -> dict:
    """검증된 실습 정의에서 Secret 값 없이 앱 Deployment 하나만 복사한다."""
    validate_lab(bundle)
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("검토한 GitOps 소스의 전체 SHA가 필요합니다")
    deployment = deepcopy(next(item for item in bundle["items"] if item["kind"] == "Deployment" and item["metadata"]["name"] == "lab-api"))
    deployment["metadata"]["annotations"] = {OWNER_KEY: "gitops", "argocd.argoproj.io/sync-options": "Prune=false,Delete=false"}
    project = _object("AppProject", PROJECT, ARGO_NAMESPACE, spec={
        "description": "B-06 실습 앱의 수동 동기화와 자동 롤백 검증 전용",
        "sourceRepos": [REPOSITORY],
        "destinations": [{"server": "https://kubernetes.default.svc", "namespace": NAMESPACE}],
        "clusterResourceWhitelist": [], "clusterResourceBlacklist": [{"group": "*", "kind": "*"}],
        "namespaceResourceWhitelist": [{"group": "apps", "kind": "Deployment"}],
        "roles": [{"name": "ci-sync", "description": "지정 실습 Application 상태 조회와 동기화만 허용",
            "policies": [f"p, proj:{PROJECT}:ci-sync, applications, {action}, {PROJECT}/{APPLICATION}, allow"
                         for action in ("get", "sync")]}],
    })
    application = _object("Application", APPLICATION, ARGO_NAMESPACE, spec={
        "project": PROJECT, "source": {"repoURL": REPOSITORY, "path": GITOPS_PATH,
            "targetRevision": revision},
        "destination": {"server": "https://kubernetes.default.svc", "namespace": NAMESPACE},
        "syncPolicy": {"syncOptions": ["CreateNamespace=false", "FailOnSharedResource=true"]},
    })
    argo_read = _object("Role", "b6-lab-argo-read", ARGO_NAMESPACE, api="rbac.authorization.k8s.io/v1", rules=[{
        "apiGroups": ["argoproj.io"], "resources": ["applications"], "resourceNames": [APPLICATION], "verbs": ["get"]}])
    argo_binding = _object("RoleBinding", "b6-lab-argo-read", ARGO_NAMESPACE, api="rbac.authorization.k8s.io/v1",
        subjects=[{"kind": "ServiceAccount", "name": "lab-investigator", "namespace": NAMESPACE}],
        roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "b6-lab-argo-read"})
    coordinator = _object("Role", "b6-s10-coordinator", NAMESPACE, api="rbac.authorization.k8s.io/v1", rules=[
        {"apiGroups": ["coordination.k8s.io"], "resources": ["leases"], "resourceNames": [LEASE_NAME], "verbs": ["get", "update"]},
        {"apiGroups": ["apps"], "resources": ["deployments"], "resourceNames": ["lab-api"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
        {"apiGroups": ["apps"], "resources": ["replicasets"], "verbs": ["get"]}])
    coordinator_binding = _object("RoleBinding", "b6-s10-coordinator", NAMESPACE, api="rbac.authorization.k8s.io/v1",
        subjects=[{"kind": "Group", "name": "b6-s10-coordinator", "apiGroup": "rbac.authorization.k8s.io"}],
        roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "b6-s10-coordinator"})
    # AppProject의 종류/namespace 제한만으로는 다른 이름의 Deployment 쓰기를 막지 못한다.
    policy = _object("ValidatingAdmissionPolicy", POLICY, None, api="admissionregistration.k8s.io/v1", spec={
        "failurePolicy": "Fail", "matchConstraints": {"namespaceSelector": {"matchLabels": {
            "kubernetes.io/metadata.name": NAMESPACE}}, "resourceRules": [{"apiGroups": ["apps"], "apiVersions": ["v1"],
            "operations": ["CREATE", "UPDATE", "DELETE"], "resources": ["deployments"], "scope": "Namespaced"}]},
        "matchConditions": [{"name": "argo-controller-only", "expression": f"request.userInfo.username == '{CONTROLLER}'"}],
        "validations": [{"expression": "request.operation != 'DELETE' && object.metadata.name == 'lab-api'",
                         "message": "실습 Argo는 lab-api 생성·갱신만 허용하며 삭제와 다른 Deployment 변경은 거부합니다."}],
    })
    policy_binding = _object("ValidatingAdmissionPolicyBinding", POLICY, None, api="admissionregistration.k8s.io/v1", spec={
        "policyName": POLICY, "validationActions": ["Deny"]})
    config = next(item for item in bundle["items"] if item["kind"] == "ConfigMap" and item["metadata"]["name"] == "lab-collector")
    bindings = json.loads(config["data"]["bindings.json"])
    bindings["bindings"][0]["argo"].update(namespace=ARGO_NAMESPACE, application=APPLICATION, path=GITOPS_PATH)
    return {"schema_version": 1, "deployment": deployment, "bootstrap": [policy, policy_binding, project,
            argo_read, argo_binding, coordinator, coordinator_binding, application],
            "collector_bindings": bindings, "revision": revision, "automatic_sync_enabled": False,
            "credentials_created": False, "live_adoption_performed": False}


def validate_gitops(plan: dict, bundle: dict) -> dict:
    """배포 이름·권한·소스·수동 동기화 정의가 넓어진 변경을 거부한다."""
    if plan != build_gitops(bundle, plan.get("revision")):
        raise ValueError("실습 GitOps 정의에 허용하지 않은 변경이 있습니다")
    return {"namespace": NAMESPACE, "application": f"{ARGO_NAMESPACE}/{APPLICATION}", "project": PROJECT,
        "managed_resources": ["apps/Deployment:ai-ops-lab/lab-api"], "automatic_sync_enabled": False,
        "prune_enabled": False, "cluster_resources_managed_by_application": False,
        "investigator_writes": False, "credentials_created": False, "live_adoption_performed": False}
