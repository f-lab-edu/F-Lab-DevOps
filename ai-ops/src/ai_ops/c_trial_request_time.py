"""작은 관측 시각 차이는 기한 내 실제 대기로만 처리하며 요청을 소급하지 않는다."""
from datetime import datetime,timezone
import time

from ai_ops.c_recovery import IncidentProjection
from ai_ops.contracts import _utc_datetime


MAX_OBSERVATION_WAIT_SECONDS=5


def wait_for_request_projection(fetch, *, deadline, clock=None,
                                monotonic=time.monotonic, sleep=time.sleep):
    """인증된 읽기만 최대 2회 수행한다. 요청 생성·재시도나 승인은 하지 않는다."""
    clock=clock or (lambda:datetime.now(timezone.utc))
    started=_utc_datetime(clock());tick=monotonic();cutoff=_utc_datetime(deadline)
    def checkpoint():
        now=_utc_datetime(clock());elapsed=monotonic()-tick
        if elapsed<0 or abs((now-started).total_seconds()-elapsed)>1:
            raise ValueError('incident_request_clock_changed')
        if elapsed>MAX_OBSERVATION_WAIT_SECONDS or now>=cutoff:
            raise ValueError('incident_request_time_budget_exhausted')
        return now,elapsed
    checkpoint()
    projection=fetch()
    if not isinstance(projection,IncidentProjection) or projection.image_failure is None:
        raise ValueError('incident_failure_projection_required')
    projection=projection.model_copy(deep=True)
    now,elapsed=checkpoint()
    observed=projection.image_failure.observed_at
    if observed<=now:
        return projection
    remaining=(observed-now).total_seconds()
    if (remaining>MAX_OBSERVATION_WAIT_SECONDS-elapsed or observed>=cutoff):
        raise ValueError('incident_observation_outside_wait_window')
    while observed>now:
        sleep(min(.25,(observed-now).total_seconds()))
        now,_=checkpoint()
    refreshed=fetch()
    now,_=checkpoint()
    if refreshed!=projection or refreshed.image_failure.observed_at>now:
        raise ValueError('incident_projection_changed_while_waiting')
    return refreshed
