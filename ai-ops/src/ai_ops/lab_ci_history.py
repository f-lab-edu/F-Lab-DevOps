"""운영자가 GitHub에서 검증한 S10 기록만 읽기 전용 조사 입력에 연결한다."""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr, model_validator

from ai_ops.ci_history import (
    GitHubReader, Identity, MAX_RECORD_BYTES, MAX_ZIP_BYTES, PREFIX, REPOSITORY,
    Revision, _positive,
)
from ai_ops.contracts import StrictModel, UTCDateTime, _utc_datetime
from ai_ops.evidence import _json_object

WORKFLOW = ".github/workflows/ai-ops-lab-ci.yml"
CLUSTER = "arn:aws:eks:ap-northeast-2:716174522908:cluster/urlshortener"
REGISTRY = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener"
Uid = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9-]{1,80}$")]


class LabObservation(StrictModel):
    application_uid: Uid
    deployment_uid: Uid
    generation_observed: StrictBool
    image: StrictStr = Field(max_length=512)
    ready_replicas: StrictInt = Field(ge=0, le=100)
    image_pull_reasons: list[Literal["ErrImagePull", "ImagePullBackOff"]] = Field(max_length=2)
    sync_status: Literal["Synced", "OutOfSync", "Unknown"]
    health_status: Literal["Healthy", "Degraded", "Missing", "Progressing", "Suspended", "Unknown"]
    operation_phase: Literal["Succeeded", "Failed", "Error", "Running", "Terminating"]
    operation_revision: Revision


class LabRecord(StrictModel):
    schema_version: Literal[2]
    record_type: Literal["b6_lab_ci_observation"]
    repository: Literal["f-lab-edu/F-Lab-DevOps"]
    workflow: Literal[".github/workflows/ai-ops-lab-ci.yml"]
    run_id: Identity
    run_attempt: Identity
    source_revision: Revision
    cluster_arn: Literal["arn:aws:eks:ap-northeast-2:716174522908:cluster/urlshortener"]
    namespace: Literal["ai-ops-lab"]
    application: Literal["argocd/lab-target"]
    baseline_revision: Revision
    failure_revision: Revision
    evidence_import_enabled: Literal[False]
    user_path_recovery_verified: Literal[False]
    stage: Literal["deployment", "rollback", "result"]
    started_at: UTCDateTime
    observed_at: UTCDateTime
    verification_result: Literal["failure", "success", "recovered_after_failure"]
    observed: LabObservation | None

    @model_validator(mode="after")
    def consistent(self):
        if self.started_at > self.observed_at or self.baseline_revision == self.failure_revision:
            raise ValueError("lab CI interval or revision conflict")
        if self.stage == "result":
            if self.verification_result != "recovered_after_failure" or self.observed is not None:
                raise ValueError("lab CI result conflict")
            return self
        obs = self.observed
        if obs is None or not obs.generation_observed or obs.operation_phase != "Succeeded":
            raise ValueError("lab CI observation incomplete")
        if self.stage == "deployment":
            if (self.verification_result != "failure" or obs.ready_replicas != 0
                    or not obs.image_pull_reasons or obs.health_status == "Healthy"
                    or obs.operation_revision != self.failure_revision
                    or not re.fullmatch(re.escape(REGISTRY) + r":b6-missing-[0-9a-f]{32}", obs.image)):
                raise ValueError("lab CI failure conflict")
        elif (self.verification_result != "success" or obs.ready_replicas != 1
                or obs.image_pull_reasons or obs.sync_status != "Synced" or obs.health_status != "Healthy"
                or obs.operation_revision != self.baseline_revision
                or not re.fullmatch(re.escape(REGISTRY) + r"@sha256:[0-9a-f]{64}", obs.image)):
            raise ValueError("lab CI rollback conflict")
        return self


@dataclass(frozen=True)
class VerifiedLabHistory:
    """검증 함수가 반환하는 메모리 내 입력이다. 외부 JSON을 신뢰하는 로더는 제공하지 않는다.

    동일 프로세스의 운영자 코드를 신뢰하며 Python 객체 자체를 보안 경계로 취급하지 않는다.
    """

    run_id: str
    run_attempt: str
    artifact_id: str
    archive_digest: str
    source_revision: str
    records: tuple[bytes, bytes, bytes]

    def parsed(self) -> tuple[LabRecord, LabRecord, LabRecord]:
        return tuple(LabRecord.model_validate(_json_object(raw)) for raw in self.records)

    def summary(self) -> dict:
        return {"repository": REPOSITORY, "workflow": WORKFLOW, "run_id": self.run_id,
                "run_attempt": self.run_attempt, "artifact_id": self.artifact_id,
                "archive_digest": self.archive_digest, "source_revision": self.source_revision,
                "pair_status": "failure_then_rollback_verification_success",
                "user_path_recovery_verified": False,
                "records": [record.model_dump(mode="json") for record in self.parsed()]}


def _archive(raw: bytes, digest: str) -> tuple[bytes, bytes, bytes]:
    if (len(raw) > MAX_ZIP_BYTES or not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or "sha256:" + hashlib.sha256(raw).hexdigest() != digest):
        raise ValueError("lab CI archive digest rejected")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = ("deployment.json", "rollback.json", "result.json")
        if len(entries) != 3 or sorted(entry.filename for entry in entries) != sorted(names):
            raise ValueError("lab CI archive members rejected")
        records = []
        for name in names:
            entry = archive.getinfo(name)
            if (entry.is_dir() or entry.flag_bits & 1
                    or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                    or not 0 < entry.file_size <= MAX_RECORD_BYTES):
                raise ValueError("lab CI archive member rejected")
            with archive.open(entry) as stream:
                content = stream.read(MAX_RECORD_BYTES + 1)
            if len(content) > MAX_RECORD_BYTES:
                raise ValueError("lab CI record too large")
            document = _json_object(content)
            if (type(document.get("schema_version")) is not int
                    or type(document.get("evidence_import_enabled")) is not bool
                    or type(document.get("user_path_recovery_verified")) is not bool):
                raise ValueError("lab CI strict type rejected")
            LabRecord.model_validate(document)
            records.append(content)
        return tuple(records)


def verify_lab_ci_history(run_id: int, expected_head: str, *, reader=None) -> VerifiedLabHistory:
    """고정된 main 워크플로의 완료 기록을 확인한다. 자료가 없거나 모순이면 가져오기를 거부한다."""
    run_id = _positive(run_id)
    if not isinstance(expected_head, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise ValueError("lab CI source revision rejected")
    reader = reader or GitHubReader()
    run = _json_object(reader.get(f"{PREFIX}/runs/{run_id}"))
    attempt = _positive(run.get("run_attempt"))
    if (type(run.get("id")) is not int or run["id"] != run_id or run.get("head_sha") != expected_head
            or run.get("head_branch") != "main" or run.get("path") != WORKFLOW
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("status") != "completed" or run.get("event") != "workflow_dispatch"
            or run.get("conclusion") != "failure"):
        raise ValueError("lab CI run scope rejected")
    start, end = _utc_datetime(run["run_started_at"]), _utc_datetime(run["updated_at"])
    if start > end:
        raise ValueError("lab CI run interval rejected")
    listing = _json_object(reader.get(f"{PREFIX}/runs/{run_id}/artifacts?per_page=100"))
    artifacts, count = listing.get("artifacts"), listing.get("total_count")
    if (type(count) is not int or not 0 <= count <= 100 or not isinstance(artifacts, list)
            or len(artifacts) != count or any(not isinstance(item, dict) for item in artifacts)):
        raise ValueError("lab CI artifact listing incomplete")
    selected = [item for item in artifacts if item.get("name") == f"b6-lab-observation-{run_id}-{attempt}"]
    if len(selected) != 1:
        raise ValueError("lab CI artifact missing or ambiguous")
    artifact = selected[0]
    artifact_id = _positive(artifact.get("id"))
    origin = artifact.get("workflow_run", {})
    if (artifact.get("expired") is not False or type(origin.get("id")) is not int
            or origin["id"] != run_id or origin.get("head_sha") != expected_head
            or origin.get("head_branch") != "main" or type(artifact.get("size_in_bytes")) is not int
            or not 0 < artifact["size_in_bytes"] <= MAX_ZIP_BYTES):
        raise ValueError("lab CI artifact scope rejected")
    raw = reader.get(f"{PREFIX}/artifacts/{artifact_id}/zip", max_bytes=MAX_ZIP_BYTES)
    history = VerifiedLabHistory(str(run_id), str(attempt), str(artifact_id), artifact.get("digest"),
                                 expected_head, _archive(raw, artifact.get("digest")))
    records = history.parsed()
    common = {"run_id", "run_attempt", "source_revision", "baseline_revision", "failure_revision"}
    for record, stage in zip(records, ("deployment", "rollback", "result"), strict=True):
        if (record.stage != stage or record.run_id != str(run_id) or record.run_attempt != str(attempt)
                or record.source_revision != expected_head or not start <= record.started_at <= record.observed_at <= end
                or any(getattr(record, key) != getattr(records[0], key) for key in common)):
            raise ValueError("lab CI record identity or time rejected")
    failed, restored, result = records
    if (failed.observed_at > restored.started_at or restored.observed_at > result.started_at
            or failed.observed.application_uid != restored.observed.application_uid
            or failed.observed.deployment_uid != restored.observed.deployment_uid):
        raise ValueError("lab CI pair rejected")
    return history
