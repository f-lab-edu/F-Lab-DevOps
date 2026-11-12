"""C-02 장애 관측과 C-03 PR 기준을 별도 로컬 기록으로 묶는다."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from pydantic import Field, model_validator

from ai_ops.c_recovery import COMMIT, DIGEST, IMAGE_REPOSITORY, RECOVERY_REPOSITORIES, REPOSITORY, VALUES_PATH, _encoded_values, _image_diff, _private
from ai_ops.c_recovery_proof import proposal_identity
from ai_ops.contracts import IncidentId, RunId, Sha256, StrictModel, UTCDateTime, _utc_datetime, sha256_json


UID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class RecoveryPRLink(StrictModel):
    schema_version: int = 1
    proposal_id: str = Field(pattern=r"^cprop-[0-9a-f]{32}$")
    proposal_hash: Sha256
    request_id: str = Field(pattern=r"^creq-[0-9a-f]{32}$")
    incident_id: IncidentId
    run_id: RunId
    repository: str
    values_path: str
    observed_revision_sha: str
    pr_base_sha: str
    lab_tree_sha: str
    application_uid: str = Field(pattern=UID_PATTERN)
    target_uid: str = Field(pattern=UID_PATTERN)
    current_digest: str
    desired_digest: str
    recheck_hash: Sha256
    diff: str = Field(max_length=8192)
    diff_hash: Sha256
    created_at: UTCDateTime
    expires_at: UTCDateTime
    link_hash: Sha256

    @model_validator(mode="after")
    def verify_record(self):
        if (self.schema_version != 1 or self.repository not in RECOVERY_REPOSITORIES or self.values_path != VALUES_PATH
                or not all(COMMIT.fullmatch(sha) for sha in
                           (self.observed_revision_sha, self.pr_base_sha, self.lab_tree_sha))
                or not all(DIGEST.fullmatch(digest) for digest in (self.current_digest, self.desired_digest))
                or self.current_digest == self.desired_digest
                or self.diff != _image_diff(self.current_digest, self.desired_digest)
                or self.diff_hash != hashlib.sha256(self.diff.encode()).hexdigest()
                or not timedelta(0) < self.expires_at - self.created_at <= timedelta(hours=24)
                or self.link_hash != sha256_json(self.model_dump(mode="json", exclude={"link_hash"}))):
            raise ValueError("C-03 연결 기록의 범위·diff·해시·기한이 유효하지 않습니다")
        return self

    def matches_draft(self, draft):
        return (draft.schema_version == 2
                and all(getattr(self, name) == getattr(draft, name) for name in (
                    "proposal_id", "proposal_hash", "request_id", "incident_id", "run_id",
                    "repository", "values_path", "target_uid", "current_digest", "desired_digest"))
                and self.observed_revision_sha == draft.base_sha
                and draft.created_at <= self.created_at < self.expires_at <= draft.expires_at)


def read_private_c_record(path: Path) -> bytes:
    _private(path, directory=False)
    with path.open("rb") as stream:
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError("C 기록의 크기가 허용 범위를 넘었습니다")
    return data


def require_matching_recheck(draft, original, refreshed, moment):
    """초안에 봉인한 최초 증거와 새 원본 재조회의 관측 대상을 비교한다."""
    if draft.schema_version != 2 or not draft.created_at <= moment < draft.expires_at:
        raise ValueError("inactive_v2_draft")
    bound = []
    for record in (original, refreshed):
        if (record.get("proposal_ready") is not True or record.get("proposal_allowed") is not False
                or record.get("blockers") != ["draft_only_c3_approval_required"]
                or record.get("cluster_or_github_write_performed") is not False):
            raise ValueError("proposal_recheck_required")
        identity = proposal_identity(record)
        if identity.get("source_repository", REPOSITORY) != draft.repository:
            raise ValueError("proposal_recheck_repository_mismatch")
        checked_at = _utc_datetime(record["proof_checked_at"])
        digest = sha256_json({**identity, "proof_checked_at": record["proof_checked_at"]})
        if any(value is None for value in identity.values()) or digest != record.get("proposal_readiness_hash"):
            raise ValueError("proposal_recheck_hash_mismatch")
        bound.append((identity, checked_at, digest))
    (prior, prior_at, prior_hash), (current, checked_at, _) = bound
    if (prior_hash != draft.recheck_hash or prior != current
            or not prior_at <= draft.created_at <= prior_at + timedelta(seconds=30)
            or not prior_at <= checked_at <= moment <= checked_at + timedelta(seconds=30)):
        raise ValueError("proposal_recheck_changed_or_stale")
    for key, expected in {
        "base_sha": draft.base_sha, "target_uid": draft.target_uid, "current_digest": draft.current_digest,
        "prior_digest": draft.desired_digest, "incident_id": draft.incident_id, "run_id": draft.run_id,
        "request_id": draft.request_id, "bundle_hash": draft.bundle_hash, "report_hash": draft.report_hash,
        "failure_binding_hash": draft.failure_binding_hash,
    }.items():
        if current[key] != expected:
            raise ValueError("proposal_recheck_draft_mismatch")


def make_pr_link(draft, refreshed, pr_base_sha, lab_tree_sha, moment):
    body = {
        "schema_version": 1,
        **{name: getattr(draft, name) for name in (
            "proposal_id", "proposal_hash", "request_id", "incident_id", "run_id",
            "repository", "values_path", "target_uid", "current_digest", "desired_digest")},
        "observed_revision_sha": draft.base_sha, "pr_base_sha": pr_base_sha, "lab_tree_sha": lab_tree_sha,
        "application_uid": refreshed["application_uid"], "recheck_hash": refreshed["proposal_readiness_hash"],
        "diff": draft.diff, "diff_hash": draft.diff_hash,
        "created_at": moment.isoformat().replace("+00:00", "Z"),
        "expires_at": draft.expires_at.isoformat().replace("+00:00", "Z"),
    }
    return RecoveryPRLink.model_validate({**body, "link_hash": sha256_json(body)})


def check_rendered_image_change(chart_files, before_digest, after_digest):
    """고정 커밋의 Helm 입력을 두 번 렌더해 실습 Deployment 이미지 한 값만 비교한다."""
    with tempfile.TemporaryDirectory() as directory:
        chart = Path(directory)
        for name, data in chart_files.items():
            target = chart / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        def render(digest):
            (chart / "values.yaml").write_text(_encoded_values(digest), encoding="utf-8")
            output = subprocess.run(
                ["helm", "template", "lab-target", str(chart), "--namespace", "ai-ops-lab"],
                capture_output=True, timeout=10, check=True,
            ).stdout
            if not 0 < len(output) <= 65536:
                raise ValueError("unexpected_lab_render_size")
            lines = output.decode().splitlines()
            if lines.count("---") != 1:
                raise ValueError("unexpected_lab_render_documents")
            document = json.loads("\n".join(line for line in lines if line != "---" and not line.startswith("#")))
            containers = document["spec"]["template"]["spec"]["containers"]
            if (document["apiVersion"] != "apps/v1" or document["kind"] != "Deployment"
                    or document["metadata"]["name"] != "lab-api"
                    or document["metadata"]["namespace"] != "ai-ops-lab"
                    or len(containers) != 1 or containers[0]["name"] != "api"
                    or containers[0]["image"] != f"{IMAGE_REPOSITORY}@{digest}"):
                raise ValueError("unexpected_lab_render_target")
            del containers[0]["image"]
            return document

        if render(before_digest) != render(after_digest):
            raise ValueError("unexpected_lab_render_change")
