"""실습 배포의 수동 소유권과 만료로 빼앗지 않는 공유 실행 잠금을 검사한다."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re

from ai_ops.b6_lab import NAMESPACE, PURPOSE
from ai_ops.contracts import _utc_datetime


LEASE_NAME = "b6-scenario-lock"
OWNER_KEY = "ai-ops/b6-deployment-owner"
NOT_BEFORE_KEY = "ai-ops/b6-not-before"


def require_manual_deployment(obj: dict) -> None:
    """GitOps가 표시된 배포에 기존 직접 patch 실행기를 적용하지 않는다."""
    metadata = obj["metadata"]
    annotations = metadata.get("annotations", {})
    if (annotations.get(OWNER_KEY, "manual") != "manual"
            or annotations.get("argocd.argoproj.io/tracking-id")
            or metadata.get("labels", {}).get("app.kubernetes.io/instance")):
        raise ValueError("GitOps 소유 배포는 직접 변경하지 않습니다")


class LabLease:
    """CAS로 한 실행만 소유하며 복구 실패·프로세스 종료 때 잠금을 보존한다."""

    def __init__(self, command, holder: str, *, clock=None):
        if not re.fullmatch(r"(?:manual|preflight|ci|adoption)-[a-f0-9]{32}", holder):
            raise ValueError("실습 잠금 실행 식별자가 다릅니다")
        self.command = command
        self.holder = holder
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.uid = None

    def _get(self):
        raw = self.command("-n", NAMESPACE, "get", "lease", LEASE_NAME, "--ignore-not-found", "-o", "json")
        return json.loads(raw) if raw.strip() else None

    def _validate(self, obj):
        metadata = obj["metadata"]
        if (obj.get("apiVersion") != "coordination.k8s.io/v1" or obj.get("kind") != "Lease"
                or metadata.get("namespace") != NAMESPACE or metadata.get("name") != LEASE_NAME
                or any(metadata.get("labels", {}).get(k) != v for k, v in PURPOSE.items())
                or not metadata.get("uid") or not metadata.get("resourceVersion")
                or obj.get("spec", {}).get("leaseDurationSeconds") is not None):
            raise ValueError("실습 공유 잠금의 소유 범위가 다릅니다")

    def acquire(self):
        current = self._get()
        now = _utc_datetime(self.clock())
        if current is None:
            desired = {"apiVersion": "coordination.k8s.io/v1", "kind": "Lease", "metadata": {
                "name": LEASE_NAME, "namespace": NAMESPACE, "labels": dict(PURPOSE)}, "spec": {}}
            operation = "create"
        else:
            self._validate(current)
            if current["spec"].get("holderIdentity"):
                raise ValueError("다른 실행 또는 미복구 실행이 공유 잠금을 보유하고 있습니다")
            not_before = current["metadata"].get("annotations", {}).get(NOT_BEFORE_KEY)
            if not_before and _utc_datetime(not_before) > now:
                raise ValueError("이전 실행의 공유 5분 지표 창이 남아 있습니다")
            desired = deepcopy(current)
            operation = "replace"
        desired["spec"].update(holderIdentity=self.holder, acquireTime=now.isoformat(), renewTime=now.isoformat())
        response = json.loads(self.command(operation, "-f", "-", "-o", "json", payload=json.dumps(desired)))
        self._validate(response)
        if response["spec"].get("holderIdentity") != self.holder:
            raise ValueError("실습 공유 잠금 취득을 확인하지 못했습니다")
        self.uid = response["metadata"]["uid"]
        return {"name": LEASE_NAME, "uid": self.uid, "holder": self.holder}

    def release(self, *, recovery_verified: bool, scenario_started: bool = True):
        if recovery_verified is not True or type(scenario_started) is not bool or self.uid is None:
            raise ValueError("복구를 확인한 실행만 자기 공유 잠금을 해제할 수 있습니다")
        current = self._get()
        if current is None:
            raise ValueError("실습 공유 잠금이 사라졌습니다")
        self._validate(current)
        if current["metadata"]["uid"] != self.uid or current["spec"].get("holderIdentity") != self.holder:
            raise ValueError("다른 실행의 공유 잠금은 해제하지 않습니다")
        current["spec"]["holderIdentity"] = ""
        not_before = _utc_datetime(self.clock()) + timedelta(seconds=305 if scenario_started else 0)
        current["metadata"].setdefault("annotations", {})[NOT_BEFORE_KEY] = not_before.isoformat()
        response = json.loads(self.command("replace", "-f", "-", "-o", "json", payload=json.dumps(current)))
        self._validate(response)
        if (response["metadata"]["uid"] != self.uid or response["spec"].get("holderIdentity")
                or response["metadata"].get("annotations", {}).get(NOT_BEFORE_KEY) != not_before.isoformat()):
            raise ValueError("실습 공유 잠금 해제를 확인하지 못했습니다")
        self.uid = None
        return {"released": True, "not_before": not_before.isoformat()}
