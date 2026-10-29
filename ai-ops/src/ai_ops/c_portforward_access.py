"""현재 실습 worker Pod 하나에만 사건 조회 포트 전달 권한을 묶는다."""

from __future__ import annotations

import re


NAMESPACE = "ai-ci"
DEPLOYMENT = "ci-worker"
ACCOUNT = "c2-projection-operator"
ROLE = "c2-projection-portforward"
POD_NAME = re.compile(r"ci-worker-[a-z0-9]{8,10}-[a-z0-9]{5}\Z")
UID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def _owner(value: dict, kind: str, name: str | None = None) -> dict:
    owners = [item for item in value.get("metadata", {}).get("ownerReferences", [])
              if item.get("controller") is True and item.get("kind") == kind
              and (name is None or item.get("name") == name)]
    if len(owners) != 1 or not UID.fullmatch(owners[0].get("uid", "")):
        raise ValueError("실습 worker 소유 관계가 유효하지 않습니다")
    return owners[0]


def build_portforward_access(pod: dict, replica_set: dict, deployment: dict) -> dict:
    """준비 상태·소유 관계·조회 sidecar를 확인한 뒤 Pod 이름만 허용한다."""
    name = pod.get("metadata", {}).get("name", "")
    if (pod.get("metadata", {}).get("namespace") != NAMESPACE
            or not POD_NAME.fullmatch(name)
            or not UID.fullmatch(pod["metadata"].get("uid", ""))
            or pod["metadata"].get("labels", {}).get("app") != DEPLOYMENT
            or pod.get("status", {}).get("phase") != "Running"
            or pod.get("metadata", {}).get("deletionTimestamp") is not None
            or deployment.get("metadata", {}).get("namespace") != NAMESPACE
            or deployment["metadata"].get("name") != DEPLOYMENT
            or deployment.get("spec", {}).get("replicas") != 1
            or deployment.get("status", {}).get("readyReplicas") != 1
            or replica_set.get("metadata", {}).get("namespace") != NAMESPACE):
        raise ValueError("현재 실습 worker Pod가 준비 상태가 아닙니다")
    parent = _owner(pod, "ReplicaSet")
    if (replica_set["metadata"].get("name") != parent["name"]
            or replica_set["metadata"].get("uid") != parent["uid"]
            or _owner(replica_set, "Deployment", DEPLOYMENT)["uid"]
            != deployment["metadata"].get("uid")):
        raise ValueError("실습 worker Pod의 Deployment 소유 관계가 다릅니다")
    containers = pod.get("spec", {}).get("containers", [])
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if ({item.get("name") for item in containers} != {"ci-worker", "projection-read"}
            or {item.get("name") for item in statuses} != {"ci-worker", "projection-read"}
            or not all(item.get("ready") is True for item in statuses)
            or pod["spec"].get("serviceAccountName") != DEPLOYMENT
            or not any(port.get("containerPort") == 9444
                       for item in containers if item.get("name") == "projection-read"
                       for port in item.get("ports", []))):
        raise ValueError("사건 조회 sidecar가 준비 상태가 아닙니다")
    meta = {"namespace": NAMESPACE, "annotations": {"ai-ops/c2-target-pod-uid": pod["metadata"]["uid"]}}
    return {"apiVersion": "v1", "kind": "List", "items": [
        {"apiVersion": "v1", "kind": "ServiceAccount",
         "metadata": {**meta, "name": ACCOUNT}, "automountServiceAccountToken": False},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "Role",
         "metadata": {**meta, "name": ROLE}, "rules": [
             {"apiGroups": [""], "resources": ["pods"], "resourceNames": [name], "verbs": ["get"]},
             {"apiGroups": [""], "resources": ["pods/portforward"], "resourceNames": [name], "verbs": ["create"]}]},
        {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
         "metadata": {**meta, "name": ROLE},
         "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": ROLE},
         "subjects": [{"kind": "ServiceAccount", "name": ACCOUNT, "namespace": NAMESPACE}]},
    ]}
