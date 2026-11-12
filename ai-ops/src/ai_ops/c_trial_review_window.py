"""실제 안내 이후의 최소 리뷰 시간을 보장하고 늦은 리뷰를 거부한다."""

from ai_ops.contracts import _utc_datetime


def require_review_notice(plan, notice, *, pull_number, head, now):
    if plan.schema_version != 2:
        raise ValueError('review_notice_requires_new_plan')
    from ai_ops.c_trial_notice import validate_delivery
    validate_delivery(plan, notice, pull_number=pull_number, head=head)
    notified = _utc_datetime(notice['notified_at'])
    if (notice.get('trial_id') != plan.trial_id or notice.get('plan_hash') != plan.digest()
            or notice.get('pull_number') != pull_number or notice.get('head_sha') != head
            or _utc_datetime(notice['review_deadline']) != plan.review_cutoff()
            or not plan.planned_at <= notified <= now < plan.manual_recovery_at
            or (plan.review_cutoff()-notified).total_seconds() < plan.minimum_review_seconds):
        raise ValueError('insufficient_actual_review_notice_window')


def review_in_time(review, plan):
    return plan.planned_at <= _utc_datetime(review['submitted_at']) <= plan.review_cutoff()


def review_path_ended(value):
    return value.get('stage') in ('minimum_notification_window_missed','stopped',
                                 'review_deadline_passed_no_execution_context',
                                 'proposal_cancelled_or_expired')
