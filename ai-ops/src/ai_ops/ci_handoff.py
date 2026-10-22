"""비공개 큐 안에서만 사용하는 CI 전달 계약과 수집 결과의 연결을 검사한다."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictStr, model_validator

from ai_ops.ci_history import Identity, Revision
from ai_ops.contracts import StrictModel, canonical_json_bytes
from ai_ops.lab_ci_history import LabRecord, VerifiedLabHistory, WORKFLOW


SOURCE = "verified_lab_ci_v1"
TARGET = "url-shortener-b6-lab"
QUESTION = "같은 CI 실행의 최초 실패와 자동 원복, 사용자 경로 복구 여부와 자료 시각의 한계를 설명해 주세요."
MAX_HANDOFF_BYTES = 32768


class CIInputError(ValueError):
    """출처와 조사 입력의 결합이 맞지 않아 사람이 확인해야 하는 상태다."""


class CIHandoff(StrictModel):
    """인증된 큐 작성자가 넣는 자료다. 외부 JSON의 신뢰 선언으로 사용하지 않는다."""

    schema_version: Literal[1]
    repository: Literal["f-lab-edu/F-Lab-DevOps"]
    workflow: Literal[".github/workflows/ai-ops-lab-ci.yml"]
    run_id: Identity
    run_attempt: Identity
    artifact_id: Identity
    archive_digest: StrictStr = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_revision: Revision
    records: list[LabRecord] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def consistent(self):
        failed, restored, result = self.records
        for record, stage in zip(self.records, ("deployment", "rollback", "result"), strict=True):
            if (record.stage != stage or record.run_id != self.run_id or record.run_attempt != self.run_attempt
                    or record.source_revision != self.source_revision
                    or record.baseline_revision != failed.baseline_revision
                    or record.failure_revision != failed.failure_revision):
                raise ValueError("CI handoff identity conflict")
        if (failed.observed_at > restored.started_at or restored.observed_at > result.started_at
                or failed.observed.application_uid != restored.observed.application_uid
                or failed.observed.deployment_uid != restored.observed.deployment_uid):
            raise ValueError("CI handoff pair conflict")
        return self

    def history(self) -> VerifiedLabHistory:
        """큐 HMAC 검사 뒤에만 부른다. worker에서는 GitHub를 재조회하지 않는다."""
        return VerifiedLabHistory(self.run_id, self.run_attempt, self.artifact_id, self.archive_digest,
                                  self.source_revision, tuple(canonical_json_bytes(r.model_dump(mode="json")) for r in self.records))


def pack_history(history: VerifiedLabHistory) -> dict:
    if not isinstance(history, VerifiedLabHistory):
        raise CIInputError("검증된 메모리 내 CI 기록이 필요합니다")
    value = CIHandoff(schema_version=1, repository="f-lab-edu/F-Lab-DevOps", workflow=WORKFLOW,
        run_id=history.run_id, run_attempt=history.run_attempt, artifact_id=history.artifact_id,
        archive_digest=history.archive_digest, source_revision=history.source_revision,
        records=list(history.parsed())).model_dump(mode="json")
    if len(canonical_json_bytes(value)) > MAX_HANDOFF_BYTES:
        raise CIInputError("CI 전달 크기를 초과했습니다")
    return value


def unpack_history(value: dict) -> VerifiedLabHistory:
    """신뢰 큐 전용 내부 복원이다. 파일/HTTP 입력용 검증 우회 로더가 아니다."""
    if len(canonical_json_bytes(value)) > MAX_HANDOFF_BYTES:
        raise CIInputError("CI 전달 크기를 초과했습니다")
    return CIHandoff.model_validate(value).history()


def check_snapshot(snapshot, history):
    """재시작 시에도 snapshot의 두 관측이 접수했던 기록과 같은지 확인한다."""
    expected = history.parsed()[:2]
    entries = [entry for entry in snapshot.audit.entries if entry.ci_run_id or entry.time_field == "artifact_observed_at"]
    items = {item.evidence_id: item for item in snapshot.bundle.evidence}
    if len(entries) != 2 or {entry.ci_stage for entry in entries} != {"deployment", "rollback"}:
        raise CIInputError("수집 결과에 접수한 CI 두 단계가 없습니다")
    for record in expected:
        entry = next(entry for entry in entries if entry.ci_stage == record.stage)
        item = items[entry.evidence_id]
        if (entry.ci_run_id != history.run_id or entry.ci_run_attempt != history.run_attempt
                or entry.ci_artifact_id != history.artifact_id
                or entry.ci_archive_digest != history.archive_digest[7:]
                or entry.ci_source_revision != history.source_revision or entry.ci_workflow != WORKFLOW
                or entry.ci_run_conclusion != "failure" or entry.ci_verification_result != record.verification_result
                or entry.ci_user_path_recovery_verified is not False
                or entry.deployment_uid != record.observed.deployment_uid
                or entry.resource_uid != record.observed.application_uid
                or entry.observed_raw != record.observed_at.isoformat().replace("+00:00", "Z")
                or item.kind != "deployment" or item.status not in {"available", "stale"}):
            raise CIInputError("CI 수집 감사와 접수 출처가 다릅니다")
        if item.status == "available" and (
                item.payload.get("image") != record.observed.image
                or item.payload.get("gitops_revision") != record.observed.operation_revision
                or item.payload.get("ci_run_id") != history.run_id
                or item.payload.get("source_sha") != history.source_revision
                or item.payload.get("status") != ("failed" if record.stage == "deployment" else "healthy_after_rollback")):
            raise CIInputError("CI 수집 내용과 접수 기록이 다릅니다")
