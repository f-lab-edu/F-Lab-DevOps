"""GitHub 정상 관측 실행과 단일 정제 artifact를 독립적으로 검증한다."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import re
import stat
import zipfile

from ai_ops.ci_history import (GitHubReader, MAX_ZIP_BYTES, NORMAL_RUNS_PATH, PREFIX,
                               REPOSITORY, _positive)
from ai_ops.contracts import _utc_datetime, sha256_json
from ai_ops.evidence import _json_object
from ai_ops.normal_ci_contract import NormalCIBinding, NormalCIReceipt, WORKFLOW


MAX_NORMAL_RECORD = 32768


@dataclass(frozen=True)
class VerifiedNormalHistory:
    """GitHub API·ZIP 해시 검증 뒤에만 생성하는 메모리 내 인계 자료다."""

    run_id: str
    run_attempt: str
    artifact_id: str
    archive_digest: str
    source_revision: str
    receipt: NormalCIReceipt


def _receipt_archive(raw: bytes, digest: str) -> NormalCIReceipt:
    """ZIP을 디스크에 풀지 않고 한 개의 정제 결과만 제한해서 읽는다."""
    if (not isinstance(raw, bytes) or len(raw) > MAX_ZIP_BYTES
            or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or "sha256:" + hashlib.sha256(raw).hexdigest() != digest):
        raise ValueError("normal_ci_archive_digest_rejected")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) != 1:
            raise ValueError("normal_ci_archive_members_rejected")
        entry = entries[0]
        if (entry.filename != "result.json" or entry.is_dir() or entry.flag_bits & 1
                or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                or not 0 < entry.file_size <= MAX_NORMAL_RECORD):
            raise ValueError("normal_ci_archive_member_rejected")
        with archive.open(entry) as stream:
            body = stream.read(MAX_NORMAL_RECORD + 1)
    if len(body) > MAX_NORMAL_RECORD:
        raise ValueError("normal_ci_receipt_too_large")
    return NormalCIReceipt.model_validate(_json_object(body))


def verify_normal_ci_history(run_id: int, expected_head: str, binding: NormalCIBinding,
                             *, reader=None) -> VerifiedNormalHistory:
    """성공한 main workflow의 정확한 시도·artifact·현재 고정 대상을 대조한다."""
    run_id = _positive(run_id)
    if not isinstance(expected_head, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_head):
        raise ValueError("normal_ci_source_revision_rejected")
    binding = NormalCIBinding.model_validate(binding)
    reader = reader or GitHubReader()
    run = _json_object(reader.get(f"{PREFIX}/runs/{run_id}"))
    attempt = _positive(run.get("run_attempt"))
    if (type(run.get("id")) is not int or run["id"] != run_id
            or run.get("head_sha") != expected_head or run.get("head_branch") != "main"
            or run.get("path") != WORKFLOW or run.get("event") != "workflow_dispatch"
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise ValueError("normal_ci_run_scope_rejected")
    start, end = _utc_datetime(run["run_started_at"]), _utc_datetime(run["updated_at"])
    if start > end:
        raise ValueError("normal_ci_run_time_rejected")
    listing = _json_object(reader.get(f"{PREFIX}/runs/{run_id}/artifacts?per_page=100"))
    items, count = listing.get("artifacts"), listing.get("total_count")
    if (type(count) is not int or not 0 <= count <= 100 or not isinstance(items, list)
            or len(items) != count or any(not isinstance(item, dict) for item in items)):
        raise ValueError("normal_ci_artifact_listing_rejected")
    matches = [item for item in items if item.get("name") == f"b6-normal-observation-{run_id}-{attempt}"]
    if len(matches) != 1:
        raise ValueError("normal_ci_artifact_missing_or_ambiguous")
    artifact = matches[0]
    artifact_id = _positive(artifact.get("id"))
    origin = artifact.get("workflow_run", {})
    digest = artifact.get("digest")
    repository_id = run.get("repository", {}).get("id")
    head_repository_id = run.get("head_repository", {}).get("id")
    if (artifact.get("expired") is not False or type(origin.get("id")) is not int
            or origin["id"] != run_id or origin.get("head_sha") != expected_head
            or origin.get("head_branch") != "main"
            or type(repository_id) is not int or repository_id < 1
            or type(head_repository_id) is not int or head_repository_id != repository_id
            or origin.get("repository_id") != repository_id
            or origin.get("head_repository_id") != head_repository_id
            or type(artifact.get("size_in_bytes")) is not int
            or not 0 < artifact["size_in_bytes"] <= MAX_ZIP_BYTES):
        raise ValueError("normal_ci_artifact_scope_rejected")
    receipt = _receipt_archive(reader.get(f"{PREFIX}/artifacts/{artifact_id}/zip",
                                          max_bytes=MAX_ZIP_BYTES), digest)
    if (receipt.identity.run_id != str(run_id) or receipt.identity.run_attempt != str(attempt)
            or receipt.identity.source_revision != expected_head
            or receipt.identity.workflow != WORKFLOW or receipt.identity.repository != REPOSITORY
            or receipt.binding_hash != sha256_json(binding.model_dump(mode="json"))
            or receipt.executor_sha256 != binding.executor_sha256
            or not start <= receipt.started_at <= receipt.finished_at <= end):
        raise ValueError("normal_ci_receipt_origin_rejected")
    return VerifiedNormalHistory(str(run_id), str(attempt), str(artifact_id),
                                 digest, expected_head, receipt)
