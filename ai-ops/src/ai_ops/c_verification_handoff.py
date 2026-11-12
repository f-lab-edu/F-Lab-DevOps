"""C-03 검증 성공을 실제 확인 시각과 함께 C-04로 넘긴다.

보존 JSON에 현재 시각을 덧붙이는 진입점은 없다. 실행기의 실제 GitHub·잠금·동기화
검증 함수를 호출한 뒤에만 새 영수증을 만든다. 해시는 전자서명이나 실행 승인이 아니다.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import model_validator

from ai_ops.contracts import Sha256, StrictModel, UTCDateTime, _utc_datetime, sha256_json
from ai_ops.c_verification import SyncReceipt
from ai_ops.c_verification_collect import REPOSITORIES


class C03Handoff(StrictModel):
    schema_version: Literal[1] = 1
    record_type: Literal["c03_c04_handoff"] = "c03_c04_handoff"
    repository: REPOSITORIES
    execution: SyncReceipt
    snapshot_checked_at: UTCDateTime
    created_at: UTCDateTime
    result_hash: Sha256
    snapshot_hash: Sha256
    pr_link_hash: Sha256

    @model_validator(mode="after")
    def bound_time_and_hash(self):
        if (self.execution.verified_at != self.created_at
                or not 0 <= (self.created_at-self.snapshot_checked_at).total_seconds() <= 15
                or self.execution.source_hash != sha256_json(self.source())):
            raise ValueError("C-03 인계의 확인 시각·출처 결합값이 다릅니다")
        return self

    def source(self):
        return {"repository": self.repository, "result_hash": self.result_hash,
            "snapshot_hash": self.snapshot_hash, "pr_link_hash": self.pr_link_hash,
            "snapshot_checked_at": self.snapshot_checked_at.isoformat(),
            "verified_at": self.created_at.isoformat(),
            "execution": self.execution.model_dump(mode="json", exclude={"source_hash"})}


def verify_c03_handoff(*, reader, snapshot, locked, proposal, pr_link, merge_response,
                       clock=lambda: datetime.now(timezone.utc)) -> C03Handoff:
    """C-03 검증을 우회하지 않는 실행기 내 연결점이다. 외부 파일을 검증한 척하지 않는다."""
    # C-03 실행기 소스가 없는 체크아웃에서도 C-04 로컬 검사·준비 명령은 사용할 수 있다.
    # 실제 인계에는 검증된 C-03 실행기 모듈이 반드시 필요하며 없으면 즉시 실패한다.
    from ai_ops.c_sync_guard import verify_sync_result

    started = _utc_datetime(clock())
    result = verify_sync_result(reader=reader, snapshot=snapshot, locked=locked,
        proposal=proposal, pr_link=pr_link, merge_response=merge_response, now=started)
    finished = _utc_datetime(clock())
    checked = _utc_datetime(snapshot["checked_at"])
    if (finished < started or not 0 <= (finished-checked).total_seconds() <= 15
            or result.get("sync_verified") is not True or result.get("service_resolved") is not False
            or result.get("application_uid") != pr_link.application_uid
            or result.get("target_uid") != proposal.target_uid
            or result.get("desired_digest") != proposal.desired_digest):
        raise ValueError("C-03 인계 원본·검증 완료 시각이 유효하지 않습니다")
    repository = "https://github.com/"+proposal.repository+".git"
    source = {"repository": repository, "result_hash": sha256_json(result),
        "snapshot_hash": sha256_json({"application": snapshot["application"],
            "deployment": snapshot["deployment"], "lease": snapshot["lease"], "checked_at": snapshot["checked_at"]}),
        "pr_link_hash": pr_link.link_hash,
        "snapshot_checked_at": checked.isoformat(), "verified_at": finished.isoformat()}
    execution = SyncReceipt(binding={"application_uid": pr_link.application_uid, "target_uid": proposal.target_uid},
        proposal_id=proposal.proposal_id, proposal_hash=proposal.proposal_hash,
        merge_sha=result["merge_sha"], desired_digest=proposal.desired_digest,
        sync_verified=True, service_resolved=False, verified_at=finished, source_hash="0"*64)
    source["execution"] = execution.model_dump(mode="json", exclude={"source_hash"})
    execution = execution.model_copy(update={"source_hash": sha256_json(source)})
    return C03Handoff(repository=repository, execution=execution, snapshot_checked_at=checked,
        created_at=finished, result_hash=source["result_hash"], snapshot_hash=source["snapshot_hash"],
        pr_link_hash=pr_link.link_hash)
