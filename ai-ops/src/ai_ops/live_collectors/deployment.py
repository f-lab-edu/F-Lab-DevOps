"""등록된 Argo Application의 배포 문맥만 선별하고 없는 이력을 구분한다."""

from __future__ import annotations

import re

from ai_ops.b_contracts import ReadRequest, ReadTransport
from ai_ops.collection import ReadFailure, SnapshotBuilder
from ai_ops.live_collectors.common import add, metadata, read_json
from ai_ops.live_collectors.kubernetes import WorkloadContext


def collect_deployment(builder: SnapshotBuilder, transport: ReadTransport, workload: WorkloadContext) -> None:
    binding = builder.binding
    if workload.deployment is not None:
        obj = workload.deployment
        add(builder, "deployment-object", "deployment", at=workload.observed_at,
            payload={"image": workload.image, "status": "unknown"},
            context={"resource_uid": obj["metadata"]["uid"], "resource_version": obj["metadata"]["resourceVersion"]})
    else:
        add(builder, "deployment-object", "deployment", failure=ReadFailure("missing", "empty_response"))
    path = f"/apis/argoproj.io/v1alpha1/namespaces/{binding.argo.namespace}/applications/{binding.argo.application}"
    obj = read_json(builder, transport, ReadRequest(binding.kubernetes_connection, path))
    if isinstance(obj, ReadFailure):
        add(builder, "deployment-argo", "deployment", failure=obj)
    else:
        try:
            meta = metadata(obj, kind="Application", namespace=binding.argo.namespace, name=binding.argo.application)
            spec = obj["spec"]
            source, destination = spec["source"], spec["destination"]
            if (spec.get("sources") or source.get("repoURL") != binding.argo.repo_url or source.get("path") != binding.argo.path
                    or destination.get("namespace") != binding.target.namespace
                    or destination.get("server") != binding.argo.destination_server or destination.get("name")):
                raise ValueError("application scope mismatch")
            status = obj["status"]
            sync = status["sync"]
            compared = sync["comparedTo"]
            if (compared.get("source", {}).get("repoURL") != binding.argo.repo_url
                    or compared.get("source", {}).get("path") != binding.argo.path
                    or compared.get("destination") != destination):
                raise ValueError("application compared scope mismatch")
            revision = sync["revision"]
            if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
                raise ValueError("application revision is not immutable")
            # 현재 Healthy는 과거 배포 실패/롤백이나 전체 서비스 건강을 입증하지 않는다.
            health = status.get("health", {}).get("status")
            payload = {"gitops_revision": revision, "status": "progressing" if health == "Progressing" else "unknown"}
            add(builder, "deployment-argo", "deployment", at=status.get("reconciledAt"),
                time_field="status_reconciled_at", payload=payload,
                context={"resource_uid": meta["uid"], "resource_version": meta["resourceVersion"]})
            _history(builder, obj, binding)
        except (KeyError, TypeError, ValueError, AttributeError):
            add(builder, "deployment-argo", "deployment", failure=ReadFailure("invalid", "scope_mismatch"))
    # 현재 API만으로 CI 실패 시점·자동 롤백 전후를 복원했다고 주장하지 않는다.
    add(builder, "deployment-ci-history", "deployment", failure=ReadFailure("missing", "missing_history"))


def _history(builder, obj, binding):
    history = obj["status"].get("history")
    if not isinstance(history, list) or not history:
        add(builder, "deployment-history", "deployment", failure=ReadFailure("missing", "missing_history"))
        return
    if len(history) > 10:
        add(builder, "deployment-history-limit", "deployment", failure=ReadFailure("missing", "truncated"), context={"truncated": True})
    for index, entry in enumerate(history[-10:]):
        try:
            source = entry["source"]
            revision = entry["revision"]
            if (source.get("repoURL") != binding.argo.repo_url or source.get("path") != binding.argo.path
                    or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision)):
                raise ValueError("history source mismatch")
            add(builder, f"deployment-history-{index}", "deployment", at=entry.get("deployedAt"),
                time_field="history_deployed_at", payload={"gitops_revision": revision, "status": "unknown"})
        except (ValueError, KeyError, TypeError, AttributeError):
            add(builder, f"deployment-history-{index}", "deployment", failure=ReadFailure("invalid", "scope_mismatch"))
