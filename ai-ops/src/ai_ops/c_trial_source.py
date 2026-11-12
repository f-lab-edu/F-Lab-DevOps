"""실제 C-02 인증된 조사/요청 조회를 주입 소유권 아래 C-03에 연결한다."""

import importlib.util
from pathlib import Path

from ai_ops.c2_active_capture import validate_active_failure
from ai_ops.c_trial_read_cache import ArchiveBytesReader, ImmutableGitReader


class TrialSource:
    def __init__(self, context, handoff, transport, *, clock, git_reader=None, history_reader=None):
        # C-03 원본 수집기는 보존 실행 묶음의 고정 스크립트에서 로드한다.
        path = Path(__file__).resolve().parents[2]/"scripts/audit_c2_live_preflight.py"
        spec = importlib.util.spec_from_file_location("c04_bound_source", path)
        self.audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.audit)
        # 현재 run/listing/expired 상태와 ZIP digest 검증은 기존 검증기를 그대로 통과한다.
        from ai_ops.ci_history import GitHubReader
        if git_reader is None:
            from ai_ops.c_approval import ApprovalGitHubReader
            git_reader=ApprovalGitHubReader(repository=getattr(context,'recovery_repository','joungGo/c03-execution-lab'))
        original_history = self.audit.verify_normal_ci_history
        self.archive_reader = ArchiveBytesReader(history_reader or GitHubReader())
        self.git_reader = ImmutableGitReader(git_reader)
        self.audit.verify_normal_ci_history = lambda *args, **kwargs: original_history(
            *args, **kwargs, reader=self.archive_reader)
        self.context = context
        self.handoff = handoff
        self.transport = transport
        self.clock = clock

    def collect(self, expected_lease=None):
        record = self.handoff
        before = self.transport.snapshot()
        current_holder = before["lease"]["spec"].get("holderIdentity")
        expected_holder = record.owner_holder if expected_lease is None else record.executor_holder
        if (current_holder != expected_holder or (expected_lease is not None and expected_lease != {
                "name": "b6-scenario-lock", "uid": record.lease_uid, "holder": record.executor_holder})):
            raise ValueError("현재 실패 인계 소유권이 다릅니다")
        validate_active_failure(before["application"], before["deployment"], before["replica_set"],
            before["pods"], before["lease"], failure_revision=record.failure_revision,
            failure_image=record.failure_image, application_uid=record.application_uid,
            deployment_uid=record.deployment_uid, now=self.clock(),
            source_repository=record.repository, failure_handoff=record)
        result, _, _, _, raw = self.audit._collect(self.context, expected_lease_holder=expected_holder,
            include_snapshot=True, read_values=lambda revision:self.audit._github_values(
                revision, reader=self.git_reader, repository=self.context.recovery_repository))
        after = self.transport.snapshot()
        if any(before[key] != raw[key] or raw[key] != after[key]
               for key in ("application", "deployment", "lease")):
            raise ValueError("인증된 사건 원본 조회 중 대상이 바뀌었습니다")
        validate_active_failure(after["application"], after["deployment"], after["replica_set"],
            after["pods"], after["lease"], failure_revision=record.failure_revision,
            failure_image=record.failure_image, application_uid=record.application_uid,
            deployment_uid=record.deployment_uid, now=self.clock(),
            source_repository=record.repository, failure_handoff=record)
        return result, raw

    def reader(self):
        from ai_ops.c_execution_live import C3LiveReader

        return C3LiveReader(self.collect, clock=self.clock)
