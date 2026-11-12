"""실행 대상과 잠금은 엄격히 비교하고 일관된 Argo 조회 시각 갱신만 구분한다."""
from copy import deepcopy
from datetime import datetime, timezone

from ai_ops.contracts import _utc_datetime


def same_application_observation(before, after, *, now):
    if before == after:
        return True
    try:
        left, right = deepcopy(before), deepcopy(after)
        old_version = left["metadata"].pop("resourceVersion")
        new_version = right["metadata"].pop("resourceVersion")
        old_time = left["status"].pop("reconciledAt")
        new_time = right["status"].pop("reconciledAt")
        return (isinstance(old_version, str) and bool(old_version)
                and isinstance(new_version, str) and bool(new_version)
                and old_version != new_version
                and isinstance(old_time, str) and isinstance(new_time, str)
                and _utc_datetime(old_time) < _utc_datetime(new_time) <= _utc_datetime(now)
                and left == right)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def same_recovery_resources(before, after, *, now=None):
    """Application의 두 관측 필드 외에는 전체 원본의 동일성을 요구한다.

    resourceVersion은 불투명 문자열이다. 수치 순서를 추정하지 않으며,
    generation/managedFields/spec/status와 Deployment/Lease는 제외하지 않는다.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return (same_application_observation(before["application"], after["application"], now=now)
            and before["deployment"] == after["deployment"]
            and before["lease"] == after["lease"])
