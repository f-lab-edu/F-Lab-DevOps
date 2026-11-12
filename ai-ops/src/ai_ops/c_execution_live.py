"""C-03 잠금 전후에 C-02 원본 조회를 새로 수행해 장애 근거를 묶는다."""

from __future__ import annotations

from datetime import datetime, timezone
import subprocess

from ai_ops.c_approval import ApprovalRejected
from ai_ops.c_recovery_proof import assess_proposal_recheck
from ai_ops.contracts import _utc_datetime


class C3LiveReader:
    """각 read에서 새 원본을 조회하고 두 관측의 동일성을 확인한다.

    collect(expected_lease)는 C-02 원본 수집으로 만든 (incident_result, raw_snapshot)
    쌍을 반환해야 한다. 잠금 전 자료를 잠금 후 첫 근거로 재사용하지 않는다.
    """

    def __init__(self, collect, *, clock=None):
        self.collect = collect
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._previous = {}

    def read(self, *, expected_lease=None):
        try:
            holder = None if expected_lease is None else expected_lease["holder"]
            if holder is not None and (not isinstance(holder, str) or not holder.startswith("c3-")):
                raise ValueError("invalid_c3_lease_holder")
            first = self._previous.get(holder)
            if first is None:
                first, _ = self.collect(expected_lease)
            second, snapshot = self.collect(expected_lease)
            proof = assess_proposal_recheck(first, second, now=_utc_datetime(self.clock()))
            if proof.get("proposal_ready") is not True:
                raise ValueError("live_proof_not_ready")
            if holder is not None:
                acquired = _utc_datetime(snapshot["lease"]["spec"]["acquireTime"])
                if _utc_datetime(proof["proof_checked_at"]) < acquired:
                    raise ValueError("proof_predates_shared_lock")
            self._previous[holder] = second
            return {**snapshot, "proof": proof, "checked_at": proof["proof_checked_at"]}
        except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            self._previous.pop(holder if "holder" in locals() else None, None)
            raise ApprovalRejected("fresh_live_proof_unavailable") from exc
