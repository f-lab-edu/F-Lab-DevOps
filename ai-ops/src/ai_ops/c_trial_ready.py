"""시각 없는 실행 의도와 실제 준비 영수증을 승인한 한 번의 즉시 실행."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
import json
import os
import time

from pydantic import Field, model_validator

from ai_ops.c_trial_execution import TrialPlan, TrialAuthorization, C4TrialExecutor, verify_normal_trial
from ai_ops.contracts import StrictModel, Sha256, UTCDateTime, sha256_json, _utc_datetime


TIMING_FIELDS = {'schema_version', 'planned_at', 'approval_cutoff', 'manual_recovery_at',
                 'rollback_deadline', 'review_deadline', 'minimum_review_seconds'}
TARGET_FIELDS = set(TrialPlan.model_fields) - TIMING_FIELDS
ACTIVATION_KEY = 'ai-ops/c04-ready-activation'
ACK_KEYS = {role: 'ai-ops/c04-ready-ack-' + role for role in ('local', 'remote')}


class ReadyTrialPlan(StrictModel):
    """절대 시작 시각 없이 대상과 상대 기한을 승인하는 불변 계약."""

    schema_version: Literal[1] = 1
    record_type: Literal['c04_ready_intent'] = 'c04_ready_intent'
    targets: dict
    runtime_manifest_sha256: Sha256
    preflight_sha256: Sha256
    readiness_ttl_seconds: Literal[300] = 300
    review_after_start_seconds: Literal[210] = 210
    minimum_review_seconds: Literal[120] = 120
    owner_restore_after_start_seconds: Literal[240] = 240
    rollback_after_start_seconds: Literal[300] = 300
    start_write_budget_seconds: Literal[15] = 15

    @model_validator(mode='after')
    def target_contract(self):
        if set(self.targets) != TARGET_FIELDS:
            raise ValueError('ready_target_fields_rejected')
        # 기존 대상 타입·분리 저장소·서로 다른 정상/실패 기준을 그대로 사용한다.
        self.bind(datetime(2026, 1, 1, tzinfo=timezone.utc))
        return self

    @property
    def trial_id(self):
        return self.targets['trial_id']

    def digest(self):
        return sha256_json(self.model_dump(mode='json'))

    def bind(self, started_at):
        at = _utc_datetime(started_at)
        return TrialPlan(**self.targets, schema_version=2, planned_at=at,
            approval_cutoff=at-timedelta(microseconds=1),
            review_deadline=at+timedelta(seconds=self.review_after_start_seconds),
            minimum_review_seconds=self.minimum_review_seconds,
            manual_recovery_at=at+timedelta(seconds=self.owner_restore_after_start_seconds),
            rollback_deadline=at+timedelta(seconds=self.rollback_after_start_seconds))

    def source_plan(self):
        """시각과 무관한 기존 읽기 adapter의 대상 속성만 제공한다."""
        from types import SimpleNamespace
        return SimpleNamespace(**self.targets, digest=self.digest)


class ReadyReceipt(StrictModel):
    record_type: Literal['c04_ready_receipt'] = 'c04_ready_receipt'
    trial_id: int
    intent_hash: Sha256
    runtime_manifest_sha256: Sha256
    preflight_sha256: Sha256
    baseline_sha256: Sha256
    guards: dict[Literal['local', 'remote'], str]
    prepared_at: UTCDateTime
    expires_at: UTCDateTime

    @model_validator(mode='after')
    def bounds(self):
        if (set(self.guards) != {'local', 'remote'} or not all(self.guards.values())
                or (self.expires_at-self.prepared_at).total_seconds() != 300):
            raise ValueError('ready_receipt_rejected')
        return self

    def digest(self):
        return sha256_json(self.model_dump(mode='json'))


class ReadyApproval(StrictModel):
    record_type: Literal['c04_ready_approval'] = 'c04_ready_approval'
    trial_id: int
    intent_hash: Sha256
    readiness_hash: Sha256
    actor_github_id: Literal[149856960]
    approved_at: UTCDateTime
    fault_injection_approved: Literal[True]

    @model_validator(mode='before')
    @classmethod
    def explicit(cls, value):
        if isinstance(value, dict) and value.get('fault_injection_approved') is not True:
            raise ValueError('explicit_ready_approval_required')
        return value


class ReadyActivation(StrictModel):
    record_type: Literal['c04_ready_activation'] = 'c04_ready_activation'
    intent: ReadyTrialPlan
    readiness: ReadyReceipt
    approval: ReadyApproval
    execution: TrialPlan
    owner_holder: str = Field(pattern=r'^manual-[a-f0-9]{32}$')

    @model_validator(mode='after')
    def chain(self):
        p, r, a, e = self.intent, self.readiness, self.approval, self.execution
        if (p.digest() != r.intent_hash or a.intent_hash != p.digest()
                or a.readiness_hash != r.digest()
                or not p.trial_id == r.trial_id == a.trial_id == e.trial_id
                or r.runtime_manifest_sha256 != p.runtime_manifest_sha256
                or r.preflight_sha256 != p.preflight_sha256
                or not r.prepared_at <= a.approved_at < e.planned_at < r.expires_at
                or e != p.bind(e.planned_at)):
            raise ValueError('ready_activation_chain_rejected')
        return self

    def digest(self):
        return sha256_json(self.model_dump(mode='json'))

    def authorization(self):
        """상대 기한을 승인한 실제 답변의 파생 실행 바인딩. 새 사람 승인으로 기록하지 않는다."""
        return TrialAuthorization(trial_id=self.execution.trial_id,
            plan_hash=self.execution.digest(), actor_github_id=self.approval.actor_github_id,
            approved_at=self.approval.approved_at, fault_injection_approved=True)

    def rollback_plan(self):
        p = self.execution
        return dict(schema_version=2, target='urlshortener/ai-ops-lab/lab-api/practice',
            source_repository=p.repository, application_uid=p.application_uid,
            deployment_uid=p.deployment_uid, baseline_revision=p.normal_revision,
            failure_revision=p.failure_revision, normal_image=p.normal_image,
            failure_image=p.failure_image, deadline_utc=p.rollback_deadline.isoformat())


def baseline_hash(snapshot):
    """조회 시각/resourceVersion 대신 대상 spec·UID·빈 잠금의 불변 조건을 묶는다."""
    return sha256_json({k: {'uid': snapshot[k]['metadata']['uid'], 'spec': snapshot[k]['spec']}
                       for k in ('application', 'deployment', 'lease')})


class ReadyTrialController:
    """실제 준비 후 승인만 기다린다. 달력 시각으로 미루거나 끝난 실행을 재개하지 않는다."""

    def __init__(self, intent, transport, journal: Path, *, clock=None, monotonic=time.monotonic):
        self.intent = ReadyTrialPlan.model_validate(intent.model_dump(mode='json'))
        self.transport, self.journal = transport, Path(journal)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic
        self.receipt = self.executor = self.activation = None
        self.fallback_attempted = False
        self._origin = self._tick = None

    def moment(self):
        now = _utc_datetime(self.clock())
        if self._origin is not None and abs((now-self._origin).total_seconds()
                                           -(self.monotonic()-self._tick)) > 1:
            raise ValueError('ready_clock_changed_or_suspended')
        return now

    def prepare(self):
        if self.receipt is not None or self.journal.exists():
            raise ValueError('ready_run_already_prepared_or_consumed')
        self._origin, self._tick = _utc_datetime(self.clock()), self.monotonic()
        source = self.intent.source_plan()
        self.transport.require_ready_inputs(self.intent)
        self.transport.require_images(source)
        guards = self.transport.require_standby_guards(self.intent)
        snapshot = self.transport.snapshot()
        verify_normal_trial(source, snapshot, now=self.moment())
        at = self.moment()
        self.receipt = ReadyReceipt(trial_id=self.intent.trial_id, intent_hash=self.intent.digest(),
            runtime_manifest_sha256=self.intent.runtime_manifest_sha256,
            preflight_sha256=self.intent.preflight_sha256, baseline_sha256=baseline_hash(snapshot),
            guards=guards, prepared_at=at, expires_at=at+timedelta(seconds=300))
        return self.receipt

    def start(self, approval):
        """새 실제 승인을 소비하고 감시의 동일 기한 ACK 뒤 바로 주입한다."""
        if self.receipt is None or self.executor is not None or self.journal.exists():
            raise ValueError('fresh_ready_receipt_required')
        approval = ReadyApproval.model_validate(approval.model_dump(mode='json')
                                               if isinstance(approval, ReadyApproval) else approval)
        r, intent = self.receipt, self.intent
        now = self.moment()
        if (approval.trial_id != intent.trial_id or approval.intent_hash != intent.digest()
                or approval.readiness_hash != r.digest()
                or not r.prepared_at <= approval.approved_at <= now < r.expires_at):
            raise ValueError('fresh_ready_approval_required')
        source = intent.source_plan()
        self.transport.require_ready_inputs(intent)
        if self.transport.require_standby_guards(intent) != r.guards:
            raise ValueError('ready_guards_changed')
        self.transport.require_images(source)
        snapshot = self.transport.snapshot()
        verify_normal_trial(source, snapshot, now=self.moment())
        if baseline_hash(snapshot) != r.baseline_sha256 or self.moment() >= r.expires_at:
            raise ValueError('ready_baseline_changed_or_expired')
        # 디스크의 소비 표식은 취득/쓰기보다 먼저 O_EXCL로 게시한다. 오류/재기동도 재사용 불가.
        payload = dict(intent_hash=intent.digest(), readiness_hash=r.digest(),
                       approval=approval.model_dump(mode='json'), state='consumed')
        fd = os.open(self.journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f); f.flush(); os.fsync(f.fileno())
        # 마지막 현재 상태 재검증 뒤 한 번만 시작/리뷰/원복 기한을 고정한다.
        at = self.moment()
        if not approval.approved_at < at < r.expires_at:
            raise ValueError('ready_start_timestamp_rejected')
        plan = intent.bind(at)
        executor = self.executor = C4TrialExecutor(plan, self.transport, clock=self.moment)
        activation = self.activation = ReadyActivation(intent=intent, readiness=r,
            approval=approval, execution=plan, owner_holder=executor.lease.holder)
        try:
            held = executor.lease.acquire()
            if held['uid'] != plan.lease_uid:
                raise ValueError('ready_lease_uid_changed')
            self.transport.arm_ready_guards(activation, executor.lease)
            self.transport.require_ready_guard_acks(activation)
            handoff = executor.inject_ready(activation)
            return executor, handoff
        except BaseException:
            # 외부 쓰기의 응답을 잃어도 재시도하지 않는다. 원복 실패 시 소유 잠금은 보존한다.
            if not executor.finished:
                self.fallback_attempted = True
                if executor.injected:
                    executor.restore()
                elif executor.lease.uid is not None:
                    executor.cancel_before_injection()
            raise
