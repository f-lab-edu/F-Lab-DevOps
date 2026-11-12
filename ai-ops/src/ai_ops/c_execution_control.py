"""실행 취소와 이미 확인한 외부 변경을 사용자 전용 디스크에 기록한다."""

from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path

from ai_ops.c_trial_guards import private_json
from ai_ops.contracts import canonical_json_bytes, _utc_datetime
from ai_ops.incidents import _atomic_write
from ai_ops.path_policy import permitted_path, write_private_json


class ExecutionCancelled(ValueError):
    pass


LOCKED_FIELDS = ('locked_preflight_ready', 'preflight_ready', 'execution_allowed',
    'proposal_id', 'pr_link_hash', 'base_sha', 'head_sha', 'pull_number', 'review_id',
    'required_check_run_id', 'executor_actor_id', 'lease', 'checked_at')


class ExecutionControl:
    """한 프로세스만 갱신한다. 재기동은 읽기 검증과 기존 소유자 원복만 허용한다."""

    def __init__(self, root, plan, owner, *, clock=None):
        self.root = permitted_path(Path(root), 'store')
        self.path = self.root/'execution-journal.json'
        self.cancel_path = self.root/'cancel-execution.json'
        self.plan, self.owner = plan, dict(owner)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.draft = None
        self.value = {'record_type': 'c05_execution_journal', 'trial_id': plan.trial_id,
            'plan_hash': plan.digest(), 'owner': self.owner, 'version': 0, 'events': [],
            'confirmed': {}, 'pending_operation': None, 'cancelled': False}
        write_private_json(self.path, self.value)
        self._persisted = deepcopy(self.value)

    def record(self, stage, **facts):
        if private_json(self.path) != self._persisted:
            raise ValueError('execution_journal_changed')
        self.value['version'] += 1
        self.value['events'].append({'sequence': self.value['version'], 'stage': stage,
            'at': self.clock().isoformat(), **facts})
        raw = canonical_json_bytes(self.value)
        if len(raw) > 65536 or len(self.value['events']) > 128:
            raise ValueError('execution_journal_limit')
        _atomic_write(self.path, raw)
        self._persisted = deepcopy(self.value)

    def check(self):
        cancelled = False
        if self.cancel_path.exists():
            request = private_json(self.cancel_path)
            if (set(request) != {'trial_id', 'plan_hash', 'actor_github_id', 'cancel_execution'}
                    or request['trial_id'] != self.plan.trial_id
                    or request['plan_hash'] != self.plan.digest()
                    or request['actor_github_id'] != 149856960
                    or type(request['actor_github_id']) is not int
                    or request['cancel_execution'] is not True):
                raise ValueError('execution_cancel_binding_invalid')
            cancelled = True
        if self.draft is not None:
            store, proposal, actor = self.draft
            status = store.status(proposal, actor, now=self.clock())
            if status['state'] == 'cancelled':
                cancelled = True
            elif status['state'] != 'draft':
                raise ValueError('execution_proposal_inactive')
        if cancelled:
            if not self.value['cancelled']:
                self.value['cancelled'] = True
                self.record('cancelled_pending_steps_only')
            raise ExecutionCancelled('execution_cancelled_pending_steps_only')

    def bind_proposal(self, store, proposal_id, actor):
        self.draft = (store, proposal_id, actor)
        self.check()

    def before(self, operation, **facts):
        self.check()
        self.value['pending_operation'] = operation
        self.record(operation+'_attempt', **facts)

    def confirmed(self, operation, revision):
        self.value['pending_operation'] = None
        self.value['confirmed'][operation] = revision
        self.record(operation+'_confirmed', revision=revision)

    def merged(self, locked, proposal, link, response):
        persisted_locked = {k: locked[k] for k in LOCKED_FIELDS}
        persisted_locked['checked_at'] = _utc_datetime(locked['checked_at']).isoformat()
        persisted_locked['lease'] = {k: locked['lease'][k] for k in ('name', 'uid', 'holder')}
        self.value['verified_merge'] = {'locked': persisted_locked,
            'proposal': proposal.model_dump(mode='json'), 'pr_link': link.model_dump(mode='json'),
            'response': {'merged': True, 'sha': response['sha']}}
        self.confirmed('merge', response['sha'])

    def rollback_verified(self):
        # Disk errors must not prevent the independently authorized safe rollback.
        try:
            self.confirmed('rollback', self.plan.normal_revision)
        except (ValueError, OSError):
            pass

    def summary(self):
        return {'cancelled': self.value['cancelled'],
                'confirmed_changes': dict(self.value['confirmed']),
                'pending_or_unconfirmed_operation': self.value['pending_operation'],
                'automatic_resume_allowed': False}


def verified_resume_merge(path, *, plan, owner, reader):
    """로컬 SHA 주장을 믿지 않고 현재 PR/main/트리와 실제 병합 주체를 재검증한다."""
    from ai_ops.c_merge_guard import verify_merged_commit
    from ai_ops.c_pr_link import RecoveryPRLink
    from ai_ops.c_recovery import RecoveryProposal
    from ai_ops.c_trial_github import BOT_ID

    value = private_json(path)
    if (value.get('record_type') != 'c05_execution_journal'
            or value.get('trial_id') != plan.trial_id or value.get('plan_hash') != plan.digest()
            or value.get('owner') != owner):
        raise ValueError('resume_execution_journal_binding_invalid')
    merge = value.get('verified_merge')
    if merge is None:
        return None
    proposal = RecoveryProposal.model_validate(merge['proposal'])
    link = RecoveryPRLink.model_validate(merge['pr_link'])
    locked = merge['locked']
    if (proposal.repository != 'joungGo/c03-execution-lab'
            or proposal.target_uid != plan.deployment_uid
            or link.application_uid != plan.application_uid
            or proposal.desired_digest != plan.normal_image.split('@')[1]
            or proposal.current_digest != plan.failure_image.split('@')[1]
            or locked['base_sha'] != plan.seed_main
            or locked['executor_actor_id'] != BOT_ID
            or locked['lease']['uid'] != plan.lease_uid
            or locked['lease']['holder'] != owner['executor_holder']):
        raise ValueError('resume_execution_merge_scope_invalid')
    verified = verify_merged_commit(reader=reader, locked=locked, proposal=proposal,
        pr_link=link, merge_response=merge['response'])
    return verified['merge_sha']
