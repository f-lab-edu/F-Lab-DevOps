"""실습의 고정 원복 기한 안에서 GitHub 읽기와 안전한 거절 코드를 다룬다."""

from ai_ops.c_approval import ApprovalGitHubReader, ApprovalRejected
from ai_ops.contracts import _utc_datetime


def recovery_github_reader(deadline, *, repository, clock):
    """Kubernetes/ECR 수집도 포함하는 실행 구간을 기존 원복 기한에 제한한다.

    개별 GitHub GET의 8초 제한, 180초 상한과 기존 쓰기 직전의 기한/신선도
    검사는 그대로다. 8초 미만이 남으면 기본값으로 늘리지 않고 거부한다.
    """
    seconds = min(180, int((_utc_datetime(deadline)-_utc_datetime(clock())).total_seconds()))
    if seconds < 8:
        raise ApprovalRejected('github_recovery_read_window_too_short')
    return ApprovalGitHubReader(max_read_seconds=seconds, repository=repository)


# 예외 원문/API 응답/자격을 결과에 복사하지 않는다. 알려진 고정 코드만 허용한다.
_SAFE_CODES = frozenset({
    'github_recovery_read_window_too_short', 'github_read_timeout', 'github_read_failed',
    'github_read_path_rejected', 'github_response_too_large',
    'fresh_live_proof_unavailable', 'live_proof_changed_or_stale', 'live_snapshot_stale',
    'live_proof_snapshot_time_mismatch', 'under_lock_proof_not_fresh',
    'runtime_not_failed_and_unlocked', 'runtime_target_changed', 'shared_lock_lost',
    'live_state_changed_during_preflight', 'preflight_too_slow_or_expired',
    'base_sha_changed', 'pull_request_changed_during_preflight',
    'approval_changed_during_preflight', 'github_gate_changed_during_preflight',
    'proposal_changed_during_preflight', 'proposal_not_active_or_not_owned',
    'executor_identity_not_separate', 'main_protection_unavailable',
    'check_runs_unavailable', 'execution_preflight_input_unavailable_or_invalid',
})


def approval_rejection_codes(error):
    """중첩된 승인 거절만 최대 8개 고정 코드로 보존하고 다른 원문은 제외한다."""
    codes, seen = [], set()
    while error is not None and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        if isinstance(error, ApprovalRejected):
            code = str(error)
            code = code if code in _SAFE_CODES else 'approval_rejected'
            if code not in codes:
                codes.append(code)
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return codes
