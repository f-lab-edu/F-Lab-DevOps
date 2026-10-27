"""운영자 권한으로 CI 기록의 출처를 점검한다. 조사 증거로 자동 승격하지 않는다."""

from __future__ import annotations

import hashlib
import io
import os
import re
import selectors
import signal
import stat
import subprocess
import time
import zipfile
from datetime import timedelta
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from ai_ops.contracts import StrictModel, UTCDateTime, _utc_datetime
from ai_ops.evidence import _json_object


REPOSITORY = "f-lab-edu/F-Lab-DevOps"
WORKFLOW = ".github/workflows/ci-cd.yml"
PREFIX = f"repos/{REPOSITORY}/actions"
LAB_RUNS_PATH = PREFIX + "/workflows/ai-ops-lab-ci.yml/runs?branch=main&event=workflow_dispatch&status=completed&per_page=20"
NORMAL_RUNS_PATH = PREFIX + "/workflows/ai-ops-normal-ci.yml/runs?branch=main&event=workflow_dispatch&status=completed&per_page=20"
MAX_API_BYTES = 2 * 1024 * 1024
MAX_ZIP_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 65536
READ_TIMEOUT_SECONDS = 20
Revision = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
Identity = Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{0,19}$")]
Image = Annotated[StrictStr, Field(pattern=(
    r"^[0-9]{12}\.dkr\.ecr\.[a-z]{2}-[a-z]+-[0-9]\.amazonaws\.com/urlshortener"
    r"(?::(?:sha-[0-9a-f]{7}(?:[0-9a-f]{33})?|release-[0-9a-f]{40}|v1)|@sha256:[0-9a-f]{64})$"
))]


class ObservedDeployment(StrictModel):
    revision: Revision | None
    sync_status: Literal["Synced", "OutOfSync", "Unknown"]
    health_status: Literal["Healthy", "Degraded", "Missing", "Progressing", "Suspended", "Unknown"]
    operation_revision: Revision | None
    operation_phase: Literal["Succeeded", "Failed", "Error", "Running", "Terminating"] | None
    reconciled_at: UTCDateTime | None
    images: list[Image] = Field(max_length=16)
    images_omitted: StrictInt = Field(ge=0, le=16)


class DeploymentObservation(StrictModel):
    schema_version: Literal[1]
    record_type: Literal["deployment_observation"]
    repository: Literal["f-lab-edu/F-Lab-DevOps"]
    run_id: Identity
    run_attempt: Identity
    source_revision: Revision | None
    stage: Literal["deployment_verification", "rollback_verification"]
    verification_result: Literal["success", "failure"]
    verification_started_at: UTCDateTime | None
    verification_finished_at: UTCDateTime | None
    observed_at: UTCDateTime
    expected_revision: Revision | None
    expected_image: Image | None
    application: Literal["url-shortener"]
    namespace: Literal["url-shortener"]
    argo_namespace: Literal["argocd"]
    collection_status: Literal["available", "unavailable"]
    reason: Literal["unconfigured", "read_failed", "timeout", "too_large", "scope_mismatch", "invalid_payload"] | None
    observed: ObservedDeployment | None

    @model_validator(mode="after")
    def check_consistency(self):
        available = self.collection_status == "available"
        if available != (self.observed is not None) or available != (self.reason is None):
            raise ValueError("invalid collection state")
        start, finish = self.verification_started_at, self.verification_finished_at
        if ((start is None) != (finish is None) or
                start is not None and not start <= finish <= self.observed_at):
            raise ValueError("invalid verification interval")
        return self


class GitHubReader:
    """기존 gh 인증으로 고정 GitHub API만 읽으며 원문과 인증값을 저장하지 않는다."""

    def get(self, path: str, *, max_bytes: int = MAX_API_BYTES) -> bytes:
        # 기존 호스트 설정과 무관하게 접속할 GitHub 호스트와 경로를 고정한다.
        if path not in (LAB_RUNS_PATH, NORMAL_RUNS_PATH) and not re.fullmatch(re.escape(PREFIX) + r"/(?:runs/[1-9][0-9]*(?:/artifacts\?per_page=100)?|artifacts/[1-9][0-9]*/zip)", path):
            raise ValueError("CI API path rejected")
        process = subprocess.Popen(["gh", "api", "--hostname", "github.com", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            data = bytearray()
            deadline = time.monotonic() + READ_TIMEOUT_SECONDS
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError("CI API timeout")
                    chunk = os.read(process.stdout.fileno(), min(8192, max_bytes + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError("CI API response too large")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or process.wait(timeout=remaining) != 0:
                raise ValueError("CI API read failed")
            return bytes(data)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()


def _positive(value):
    if type(value) is not int or not 1 <= value < 10 ** 20:
        raise ValueError("CI identity rejected")
    return value


def _record(raw: bytes, filename: str, digest: str) -> DeploymentObservation:
    """GitHub가 반환한 ZIP 해시와 단일 파일을 확인하고 디스크에 풀지 않는다."""
    if (len(raw) > MAX_ZIP_BYTES or not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or "sha256:" + hashlib.sha256(raw).hexdigest() != digest):
        raise ValueError("CI archive digest rejected")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) != 1:
            raise ValueError("CI archive members rejected")
        entry = entries[0]
        if (entry.filename != filename or entry.is_dir() or entry.flag_bits & 1
                or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                or not 0 < entry.file_size <= MAX_RECORD_BYTES):
            raise ValueError("CI archive member rejected")
        with archive.open(entry) as stream:
            content = stream.read(MAX_RECORD_BYTES + 1)
        if len(content) > MAX_RECORD_BYTES:
            raise ValueError("CI record too large")
    document = _json_object(content)
    if type(document.get("schema_version")) is not int:
        raise ValueError("CI schema version rejected")
    return DeploymentObservation.model_validate(document)


def inspect_ci_history(run_id: int, expected_head: str, reader=None) -> dict:
    """GitHub API에서 확인한 실행·재시도 자료만 검사하고 누락을 그대로 남긴다."""
    run_id = _positive(run_id)
    if not isinstance(expected_head, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise ValueError("CI source revision rejected")
    reader = reader or GitHubReader()
    run = _json_object(reader.get(f"{PREFIX}/runs/{run_id}"))
    attempt = _positive(run.get("run_attempt"))
    if (type(run.get("id")) is not int or run["id"] != run_id or run.get("head_sha") != expected_head
            or run.get("head_branch") != "main" or run.get("path") != WORKFLOW
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("status") != "completed" or run.get("event") not in {"push", "workflow_dispatch"}
            or run.get("conclusion") not in {"success", "failure", "cancelled", "timed_out"}):
        raise ValueError("CI run scope rejected")
    run_start, run_end = _utc_datetime(run["run_started_at"]), _utc_datetime(run["updated_at"])
    if run_start > run_end:
        raise ValueError("CI run interval rejected")
    listing = _json_object(reader.get(f"{PREFIX}/runs/{run_id}/artifacts?per_page=100"))
    artifacts = listing.get("artifacts")
    count = listing.get("total_count")
    if (type(count) is not int or not 0 <= count <= 100 or not isinstance(artifacts, list)
            or len(artifacts) != count):
        raise ValueError("CI artifact listing incomplete")
    result = {
        "schema_version": 1, "scope": "github_source_inspection_only",
        "repository": REPOSITORY, "workflow": WORKFLOW, "run_id": str(run_id),
        "run_attempt": str(attempt), "source_revision": expected_head, "conclusion": run["conclusion"],
        "cluster_identity_verified": False, "evidence_import_enabled": False,
        "target_namespace": "url-shortener", "lab_target_compatible": False,
        "records": [], "missing_stages": [], "pair_status": "incomplete",
    }
    records = {}
    for suffix, stage, filename in (("deployment", "deployment_verification", "deployment.json"),
                                     ("rollback", "rollback_verification", "rollback.json")):
        name = f"deployment-observation-{run_id}-{attempt}-{suffix}"
        selected = [item for item in artifacts if item.get("name") == name]
        if len(selected) > 1:
            raise ValueError("CI artifact conflict")
        if not selected or selected[0].get("expired") is True:
            result["missing_stages"].append(stage)
            continue
        artifact = selected[0]
        artifact_id = _positive(artifact.get("id"))
        origin = artifact.get("workflow_run", {})
        if (artifact.get("expired") is not False or type(origin.get("id")) is not int
                or origin["id"] != run_id or origin.get("head_sha") != expected_head
                or origin.get("head_branch") != "main"
                or type(artifact.get("size_in_bytes")) is not int
                or not 0 < artifact["size_in_bytes"] <= MAX_ZIP_BYTES):
            raise ValueError("CI artifact scope rejected")
        raw = reader.get(f"{PREFIX}/artifacts/{artifact_id}/zip", max_bytes=MAX_ZIP_BYTES)
        record = _record(raw, filename, artifact.get("digest"))
        if (record.run_id != str(run_id) or record.run_attempt != str(attempt)
                or record.source_revision != expected_head or record.stage != stage
                or not run_start <= record.observed_at <= run_end
                or record.verification_started_at is not None and record.verification_started_at < run_start):
            raise ValueError("CI record identity or time rejected")
        records[stage] = record
        result["records"].append({"artifact_id": str(artifact_id), "archive_digest": artifact["digest"],
            **record.model_dump(mode="json")})
    deployment = records.get("deployment_verification")
    rollback = records.get("rollback_verification")
    if deployment is not None and deployment.verification_result == "failure" and run["conclusion"] == "success":
        raise ValueError("CI final outcome conflict")
    if deployment is not None and rollback is not None:
        if (deployment.verification_result != "failure" or rollback.verification_started_at is None
                or deployment.verification_finished_at is None
                or deployment.observed_at > rollback.verification_started_at):
            result["pair_status"] = "inconclusive"
        elif (rollback.verification_result == "success" and _matches_rollback(rollback)
                and deployment.expected_revision is not None
                and deployment.expected_image is not None
                and deployment.expected_revision != rollback.expected_revision
                and deployment.expected_image != rollback.expected_image):
            result["pair_status"] = "failure_then_rollback_verification_success"
        else:
            result["pair_status"] = "inconclusive"
    # 현재 Healthy만으로 과거 실패를 부정하거나 사용자 요청의 복구를 단정하지 않는다.
    result["user_path_recovery_verified"] = False
    return result


def _matches_rollback(record: DeploymentObservation) -> bool:
    observed = record.observed
    return bool(record.expected_revision and record.expected_image and observed
        and observed.revision == record.expected_revision
        and observed.operation_revision == record.expected_revision
        and observed.operation_phase == "Succeeded" and observed.sync_status == "Synced"
        and observed.health_status == "Healthy" and set(observed.images) == {record.expected_image}
        and observed.images_omitted == 0
        and observed.reconciled_at is not None
        and record.verification_started_at <= observed.reconciled_at <= record.observed_at
        and record.observed_at - record.verification_finished_at <= timedelta(seconds=120))
