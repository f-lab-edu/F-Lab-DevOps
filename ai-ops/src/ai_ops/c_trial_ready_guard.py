"""승인 전에는 미기동 상태로 대기하고 한 번 연결한 원복 기한을 변경하지 않는다."""

from datetime import datetime, timedelta, timezone
import json
import time

from ai_ops.c_trial_ready import ReadyActivation, ACTIVATION_KEY, ACK_KEYS


def standby_guard(intent, *, role, guard_id, get_lease, replace_lease, rollback,
                  publish_ready, clock=None, monotonic=time.monotonic, sleep=time.sleep):
    if role not in ACK_KEYS or not guard_id:
        raise ValueError('standby_guard_identity_rejected')
    clock = clock or (lambda: datetime.now(timezone.utc))
    started, tick = clock(), monotonic()
    initial = get_lease()
    uid = intent.targets['lease_uid']
    if initial['metadata']['uid'] != uid or initial['spec'].get('holderIdentity'):
        raise ValueError('standby_initial_lease_rejected')
    if ACTIVATION_KEY in initial['metadata'].get('annotations', {}):
        raise ValueError('standby_old_activation_present')
    publish_ready(dict(state='standby', role=role, guard_id=guard_id,
        intent_hash=intent.digest(), prepared_at=started.isoformat(),
        standby_expires_at=(started+timedelta(minutes=15)).isoformat()))
    activation = None
    while monotonic()-tick < 900:
        now = clock()
        if abs((now-started).total_seconds()-(monotonic()-tick)) > 1:
            raise ValueError('standby_clock_changed_or_suspended')
        lease = get_lease()
        # 네트워크 조회 중 activation이 게시될 수 있다. 검증 시각은 응답 뒤에 읽는다.
        now = clock()
        if abs((now-started).total_seconds()-(monotonic()-tick)) > 1:
            raise ValueError('standby_clock_changed_or_suspended')
        if lease['metadata']['uid'] != uid:
            raise ValueError('standby_lease_uid_changed')
        raw = lease['metadata'].get('annotations', {}).get(ACTIVATION_KEY)
        if raw:
            activation = ReadyActivation.model_validate_json(raw)
            if (activation.intent != intent or activation.readiness.guards[role] != guard_id
                    or lease['spec'].get('holderIdentity') != activation.owner_holder
                    or not 0 <= (now-activation.execution.planned_at).total_seconds()
                    <= intent.start_write_budget_seconds):
                raise ValueError('standby_activation_rejected')
            annotations = lease['metadata']['annotations']
            ack = json.dumps({'activation_hash': activation.digest(), 'guard_id': guard_id})
            if ACK_KEYS[role] in annotations and json.loads(annotations[ACK_KEYS[role]]) != json.loads(ack):
                raise ValueError('standby_ack_overwrite_rejected')
            annotations[ACK_KEYS[role]] = ack
            if replace_lease(lease):
                break
            # CAS 충돌만 새 원본으로 재시도한다. 다른 감시의 ACK를 덮지 않는다.
        sleep(.1)
    else:
        return {'status': 'standby_expired_without_activation', 'rollback_started': False}
    # 승인 전 대기 시간은 이 300초에 포함하지 않는다. 주입 전 연결 비용은 포함한다.
    deadline = activation.execution.rollback_deadline
    mono_deadline = monotonic()+max(0, (deadline-clock()).total_seconds())
    while clock() < deadline and monotonic() < mono_deadline:
        sleep(min(.25, max(0, mono_deadline-monotonic())))
    rollback(activation.rollback_plan(), initial)
    return {'status': 'deadline_rollback_checked', 'rollback_started': True,
            'activation_hash': activation.digest(), 'deadline_utc': deadline.isoformat()}
