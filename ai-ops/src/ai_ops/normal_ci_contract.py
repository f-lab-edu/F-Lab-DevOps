"""격리 정상 관측 CI의 고정 대상·실행 출처·정제 영수증을 정의한다."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictStr, model_validator

from ai_ops.contracts import Sha256, StrictModel, UTCDateTime, sha256_json
from ai_ops.model_projection import TARGET
from ai_ops.model_sequence_projection import SequenceModelView, SequenceProjection


WORKFLOW = ".github/workflows/ai-ops-normal-ci.yml"
GROUP = "b6-normal-coordinator"
Name = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,252}$")]
UID = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9-]{1,80}$")]
Identity = Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{0,19}$")]
Revision = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]


def executor_hash() -> str:
    """실행·원복·정제 코드를 CI와 수집기가 같은 버전으로 고정한다."""
    root = Path(__file__).parent
    names = ("normal_ci_contract.py", "normal_ci_runner.py", "normal_ci_source.py",
             "b6_scenarios.py", "b6_sequence_capture.py", "b6_coordination.py",
             "model_projection.py", "model_sequence_projection.py")
    return sha256_json({name: hashlib.sha256((root/name).read_bytes()).hexdigest()
                        for name in names})


class NormalCIIdentity(StrictModel):
    repository: Literal["f-lab-edu/F-Lab-DevOps"] = "f-lab-edu/F-Lab-DevOps"
    workflow: Literal[".github/workflows/ai-ops-normal-ci.yml"] = WORKFLOW
    run_id: Identity
    run_attempt: Identity
    source_revision: Revision

    @property
    def session(self):
        return sha256_json(self.model_dump(mode="json"))[:32]


class NormalPod(StrictModel):
    name: Name
    uid: UID
    deployment_uid: UID
    image: StrictStr


class NormalCIBinding(StrictModel):
    schema_version: Literal[1] = 1
    cluster_arn: Literal["arn:aws:eks:ap-northeast-2:716174522908:cluster/urlshortener"]
    cluster_fingerprint: Sha256
    executor_sha256: Sha256
    app: NormalPod
    collector: NormalPod

    @model_validator(mode="after")
    def fixed_repositories(self):
        import re
        for pod, deployment, repository in (
            (self.app, "lab-api", "urlshortener"),
            (self.collector, "lab-collector", "urlshortener-practice-ai-ops"),
        ):
            if (not pod.name.startswith(deployment+"-") or not re.fullmatch(
                re.escape("716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/"+
                    repository)+r"@sha256:[a-f0-9]{64}", pod.image)):
                raise ValueError("normal_ci_pinned_pod_rejected")
        if self.app.name == self.collector.name or self.app.uid == self.collector.uid:
            raise ValueError("normal_ci_pod_identity_conflict")
        return self


class NormalCIReceipt(StrictModel):
    """출처 인증은 이후 GitHub 검증이 맡는다. JSON 해시는 인증 수단이 아니다."""

    schema_version: Literal[1] = 1
    record_type: Literal["b6_normal_ci_observation"] = "b6_normal_ci_observation"
    identity: NormalCIIdentity
    target: dict
    binding_hash: Sha256
    executor_sha256: Sha256
    source_run: Annotated[StrictStr, Field(pattern=r"^run-[a-f0-9]{32}$")]
    rules_ref: Annotated[StrictStr, Field(pattern=r"^inc-[a-f0-9]{32}/run-[a-f0-9]{32}/report\.md$")]
    source_bundle_hash: Sha256
    source_audit_hash: Sha256
    mapping_hash: Sha256
    view_hash: Sha256
    view: SequenceModelView
    started_at: UTCDateTime
    finished_at: UTCDateTime
    owned_data_removed: Literal[True]
    baseline_unchanged: Literal[True]
    lock_released: Literal[True]
    external_model_calls: Literal[0] = 0

    @model_validator(mode="after")
    def consistent(self):
        sequences = [item for item in self.view.items if item.kind == "cache_probe_sequence"]
        if (self.target != TARGET or self.rules_ref.split("/")[1] != self.source_run
                or self.started_at > self.finished_at
                or not self.started_at <= self.view.collected_at <= self.finished_at
                or len(sequences) != 1 or
                [item.cache_outcome for item in sequences[0].payload.reads] != ["miss", "hit", "miss", "hit"]
                or sha256_json(self.view.model_dump(mode="json")) != self.view_hash):
            raise ValueError("normal_ci_receipt_binding_rejected")
        return self

    @classmethod
    def from_projection(cls, projection: SequenceProjection, *, identity, binding,
                        started_at, finished_at):
        source = projection.base
        if (source.identity.deployment_uid != binding.app.deployment_uid
                or source.identity.cluster_fingerprint != binding.cluster_fingerprint
                or source.identity.exporter_sha256 != binding.executor_sha256
                or projection.probe.pod_uid != binding.app.uid
                or projection.probe.image_reference_hash != sha256_json(binding.app.image)):
            raise ValueError("normal_ci_receipt_source_changed")
        return cls(identity=identity, target=TARGET,
            binding_hash=sha256_json(binding.model_dump(mode="json")),
            executor_sha256=binding.executor_sha256, source_run=source.identity.run_id,
            rules_ref=f"{source.identity.incident_id}/{source.identity.run_id}/report.md",
            source_bundle_hash=source.source_bundle_hash, source_audit_hash=source.source_audit_hash,
            mapping_hash=projection.mapping_hash, view_hash=projection.view_hash,
            view=projection.view, started_at=started_at, finished_at=finished_at,
            owned_data_removed=True, baseline_unchanged=True, lock_released=True)
