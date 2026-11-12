"""실제 격리 주입·조사 Job·C-03 복구 adapter를 한 소유권으로 연결한다."""

from datetime import datetime, timezone
from typing import Annotated, Literal
import uuid

from pydantic import Field, field_validator, model_validator

from ai_ops.b6_coordination import LabLease, NOT_BEFORE_KEY
from ai_ops.c2_active_capture import ISOLATED_REPOSITORY, build_active_capture_job
from ai_ops.c_failure_handoff import (
    FailureHandoffReader, Image, Identity, Revision, acquire_failure_preflight,
    offer_failure_handoff, plan_failure_merge,
)
from ai_ops.contracts import Sha256, StrictModel, UTCDateTime, _utc_datetime, sha256_json


class TrialPlan(StrictModel):
    schema_version: Literal[1, 2] = 1
    trial_id: Annotated[int, Field(ge=1, le=10**15)]
    repository: Literal["https://github.com/joungGo/c03-execution-lab.git"] = ISOLATED_REPOSITORY
    application_uid: Identity
    deployment_uid: Identity
    lease_uid: Identity
    worker_uid: Identity
    pvc_uid: Identity
    normal_revision: Revision
    failure_revision: Revision
    seed_main: Revision
    workflow_main: Revision
    normal_image: Image
    failure_image: Image
    planned_at: UTCDateTime
    approval_cutoff: UTCDateTime
    manual_recovery_at: UTCDateTime
    rollback_deadline: UTCDateTime
    review_deadline: UTCDateTime | None = None
    minimum_review_seconds: Annotated[int, Field(ge=120, le=120)] | None = None

    @model_validator(mode="after")
    def timing(self):
        if (self.approval_cutoff >= self.planned_at
                or (self.schema_version == 1 and ((self.manual_recovery_at-self.planned_at).total_seconds() != 180
                    or self.review_deadline is not None or self.minimum_review_seconds is not None))
                or (self.schema_version == 2 and ((self.manual_recovery_at-self.planned_at).total_seconds() != 240
                    or self.review_deadline is None
                    or (self.review_deadline-self.planned_at).total_seconds() != 210
                    or self.minimum_review_seconds != 120))
                or (self.rollback_deadline-self.planned_at).total_seconds() != 300
                or self.normal_revision in (self.failure_revision, self.seed_main)
                or self.normal_image == self.failure_image):
            raise ValueError("실습 일정·원복 기준이 유효하지 않습니다")
        return self

    def digest(self):
        return sha256_json(self.model_dump(mode="json", exclude_none=True))

    def review_cutoff(self):
        from datetime import timedelta
        return self.review_deadline or self.manual_recovery_at-timedelta(seconds=60)


class TrialAuthorization(StrictModel):
    trial_id: int
    plan_hash: Sha256
    actor_github_id: Literal[149856960]
    approved_at: UTCDateTime
    fault_injection_approved: Literal[True]

    @field_validator("fault_injection_approved", mode="before")
    @classmethod
    def explicit_approval(cls, value):
        if value is not True:
            raise ValueError("실제 사용자 실행 승인이 필요합니다")
        return value


def verify_normal_trial(plan, snapshot, *, now, held=None):
    """주입 직전 또는 원복 후 현재 이름·UID·SHA·이미지·실습 구성을 검사한다."""
    app, dep, lease = (snapshot[key] for key in ("application", "deployment", "lease"))
    source = app["spec"]["source"]
    status = app["status"]
    if (app["metadata"].get("uid") != plan.application_uid
            or app["metadata"].get("name") != "lab-target"
            or app["metadata"].get("namespace") != "argocd"
            or app["metadata"].get("deletionTimestamp") or app.get("operation")
            or app["spec"].get("project") != "b6-lab"
            or app["spec"].get("destination") != {"server": "https://kubernetes.default.svc",
                                                  "namespace": "ai-ops-lab"}
            or source != {"repoURL": plan.repository, "path": "ai-ops/lab",
                          "targetRevision": plan.normal_revision}
            or app["spec"].get("syncPolicy", {}).get("automated") is not None
            or status["sync"].get("status") != "Synced"
            or status["sync"].get("revision") != plan.normal_revision
            or status["health"].get("status") != "Healthy"
            or status["operationState"].get("phase") != "Succeeded"
            or status["operationState"]["syncResult"].get("revision") != plan.normal_revision
            or dep["metadata"].get("name") != "lab-api"
            or dep["metadata"].get("namespace") != "ai-ops-lab"
            or dep["metadata"].get("uid") != plan.deployment_uid
            or dep["metadata"].get("deletionTimestamp")
            or dep["spec"].get("replicas") != 1
            or len(dep["spec"]["template"]["spec"]["containers"]) != 1
            or dep["spec"]["template"]["spec"]["containers"][0].get("name") != "api"
            or dep["spec"]["template"]["spec"]["containers"][0].get("image") != plan.normal_image
            or dep["status"].get("observedGeneration") != dep["metadata"].get("generation")
            or dep["status"].get("readyReplicas") != 1):
        raise ValueError("실습 정상 원본이 계획과 다릅니다")
    LabLease(lambda *args: None, "manual-"+"0"*32)._validate(lease)
    expected = held.holder if held is not None else None
    not_before = lease["metadata"].get("annotations", {}).get(NOT_BEFORE_KEY)
    if (lease["metadata"]["uid"] != plan.lease_uid
            or (lease["spec"].get("holderIdentity") or None) != expected
            or (not_before and _utc_datetime(not_before) > now)
            or not 0 <= (now-_utc_datetime(snapshot["checked_at"])).total_seconds() <= 15):
        raise ValueError("실습 공유 잠금 또는 조회 시각이 다릅니다")


class C4TrialExecutor:
    """한 프로세스가 주입 소유권을 보유하고 지정 C-03 실행자로 넘긴다."""

    def __init__(self, plan: TrialPlan, transport, *, clock=None):
        self.plan = plan
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease = LabLease(transport.lease_command, "manual-"+uuid.uuid4().hex, clock=self.clock)
        self.executor_holder = "c3-"+uuid.uuid4().hex
        self.handoff = None
        self.merge_sha = None
        self.injected = False
        self.finished = False
        self.prepared_at = None
        self.last_step='initial'
        self.control = None

    def check_cancellation(self):
        if self.control is not None:
            self.control.check()

    def _before_write(self, operation, **facts):
        if self.control is not None:
            self.control.before(operation, **facts)
        if self._moment() >= self.plan.rollback_deadline:
            raise ValueError('execution_journal_crossed_write_deadline')

    def _confirm_write(self, operation, revision):
        if self.control is not None:
            self.control.confirmed(operation, revision)

    def _authorize(self, authorization):
        if (not isinstance(authorization, TrialAuthorization)
                or authorization.fault_injection_approved is not True
                or authorization.actor_github_id != 149856960
                or authorization.trial_id != self.plan.trial_id
                or authorization.plan_hash != self.plan.digest()
                or authorization.approved_at > self.plan.approval_cutoff
                or authorization.approved_at > self._moment()):
            raise ValueError("현재 계획의 실제 실행 승인이 필요합니다")

    def prepare(self, authorization):
        """준비 마감 전에 실제 조건을 확인하며 잠금·Argo 쓰기는 하지 않는다."""
        self._authorize(authorization)
        if self.prepared_at is not None or self.injected or self.finished or self._moment() >= self.plan.approval_cutoff:
            raise ValueError("준비 마감 또는 실행 상태가 다릅니다")
        self.transport.require_guards(self.plan)
        self.transport.require_images(self.plan)
        verify_normal_trial(self.plan, self.transport.snapshot(), now=self._moment())
        if self._moment() >= self.plan.approval_cutoff:
            raise ValueError("실제 준비 검사 중 마감을 넘었습니다")
        self.prepared_at = self._moment()

    def _moment(self):
        return _utc_datetime(self.clock())

    def inject(self, authorization: TrialAuthorization):
        return self._inject(authorization)

    def inject_ready(self, activation):
        """실제 준비 승인과 양쪽 감시 ACK에 묶인 소유 잠금을 이어받아 즉시 시작한다."""
        from ai_ops.c_trial_ready import ReadyActivation
        activation = ReadyActivation.model_validate(activation.model_dump(mode='json'))
        if (activation.execution != self.plan or activation.owner_holder != self.lease.holder
                or self.lease.uid != self.plan.lease_uid or self.prepared_at is not None):
            raise ValueError('ready_executor_binding_rejected')
        self.transport.require_ready_guard_acks(activation)
        self.prepared_at = activation.readiness.prepared_at
        return self._inject(activation.authorization(), activation=activation)

    def _inject(self, authorization, *, activation=None):
        plan = self.plan
        now = self._moment()
        self._authorize(authorization)
        ready = activation is not None
        elapsed = (now-plan.planned_at).total_seconds()
        in_window = (0 <= elapsed <= activation.intent.start_write_budget_seconds) if ready else (-30 <= elapsed <= 5)
        if (self.injected or self.finished or not isinstance(authorization, TrialAuthorization)
                or self.prepared_at is None or self.prepared_at >= plan.approval_cutoff
                or authorization.fault_injection_approved is not True
                or authorization.actor_github_id != 149856960
                or authorization.trial_id != plan.trial_id or authorization.plan_hash != plan.digest()
                or not authorization.approved_at <= plan.approval_cutoff
                or authorization.approved_at > now
                or not in_window
                or (plan.rollback_deadline-now).total_seconds() < 240):
            raise ValueError("새 실행별 승인·예정 시각이 필요합니다")
        self.transport.require_guards(plan)
        self.transport.require_images(plan)
        verify_normal_trial(plan, self.transport.snapshot(), now=self._moment(),
                            held=self.lease if ready else None)
        held = {'uid': self.lease.uid} if ready else self.lease.acquire()
        if held["uid"] != plan.lease_uid:
            raise ValueError("잠금 취득 뒤 UID가 달라졌습니다")
        if not ready:
            self.transport.wait_until(plan.planned_at)
        verify_normal_trial(plan, self.transport.snapshot(), now=self._moment(), held=self.lease)
        self.transport.require_guards(plan)
        self.transport.require_images(plan)
        if ready:
            self.transport.require_ready_guard_acks(activation)
        budget = activation.intent.start_write_budget_seconds if ready else 5
        if not 0 <= (self._moment()-plan.planned_at).total_seconds() <= budget:
            raise ValueError("잠금 아래 검사 중 예정 주입 시각을 지났습니다")
        injected_at = self._moment()
        # 첫 외부 쓰기의 응답을 잃어도 다시 주입하지 않는다.
        self._before_write('inject_set', revision=plan.failure_revision)
        if not 0 <= (self._moment()-plan.planned_at).total_seconds() <= budget:
            raise ValueError('execution_journal_crossed_injection_budget')
        self.injected = True
        self.transport.argo("sync", ["app", "set", "lab-target", "--revision", plan.failure_revision])
        self._confirm_write('inject_set', plan.failure_revision)
        self._before_write('inject_sync', revision=plan.failure_revision)
        self.transport.argo("sync", ["app", "sync", "lab-target", "--revision", plan.failure_revision,
                                    "--async"])
        self._confirm_write('inject_sync', plan.failure_revision)
        self.transport.wait_failure(plan)
        self.handoff = offer_failure_handoff(self.lease, read_failure=self.transport.snapshot,
            trial_id=plan.trial_id, executor_holder=self.executor_holder,
            normal_revision=plan.normal_revision, normal_image=plan.normal_image,
            failure_revision=plan.failure_revision, failure_image=plan.failure_image,
            application_uid=plan.application_uid, deployment_uid=plan.deployment_uid,
            injected_at=injected_at, rollback_deadline=plan.rollback_deadline)
        return self.handoff

    def investigate(self):
        from ai_ops.c2_active_capture import validate_active_failure

        if self.handoff is None or self.finished:
            raise ValueError("현재 실행의 실패 인계가 필요합니다")
        self.check_cancellation()
        snapshot = self.transport.snapshot()
        validate_active_failure(snapshot["application"], snapshot["deployment"],
            snapshot["replica_set"], snapshot["pods"], snapshot["lease"],
            failure_revision=self.plan.failure_revision, failure_image=self.plan.failure_image,
            application_uid=self.plan.application_uid, deployment_uid=self.plan.deployment_uid,
            source_repository=self.plan.repository, now=self._moment(), failure_handoff=self.handoff)
        worker = self.transport.get("worker")
        if worker["metadata"].get("uid") != self.plan.worker_uid:
            raise ValueError("조사 worker가 계획과 다릅니다")
        if self.transport.get("pvc")["metadata"].get("uid") != self.plan.pvc_uid:
            raise ValueError("조사 PVC가 계획과 다릅니다")
        job = build_active_capture_job(worker, trial_id=self.plan.trial_id,
                                       failure_revision=self.plan.failure_revision)
        job["metadata"]["annotations"]["ai-ops/failure-handoff-sha256"] = self.handoff.digest()
        return self.transport.create_capture_job(job)

    def recover(self, **preflight_args):
        from ai_ops.c_approval import verify_review
        from ai_ops.c_merge_guard import verify_merged_commit
        from ai_ops.c_sync_guard import plan_exact_sync
        from ai_ops.c_verification_handoff import verify_c03_handoff

        if self.handoff is None or self.finished or self._moment() >= self.plan.manual_recovery_at:
            raise ValueError("현재 실패 인계와 남은 사람 승인 시간이 필요합니다")
        if self.control is not None:
            self.control.bind_proposal(preflight_args['drafts'], preflight_args['proposal_id'],
                                       preflight_args['allowed_github_id'])
        preflight_args.pop("clock", None)
        review_args = {key: preflight_args[key] for key in (
            "drafts", "proposal_id", "pull_number", "allowed_github_id", "pr_author_id", "pr_link", "reader")}
        review_record=verify_review(**review_args, clock=self.clock)
        if self.plan.schema_version==2:
            from ai_ops.c_trial_review_window import require_review_notice
            require_review_notice(self.plan,preflight_args.pop('review_notice'),
                pull_number=preflight_args['pull_number'],head=review_record['head_sha'],now=self._moment())
            if not self.plan.planned_at <= _utc_datetime(review_record['approved_at']) <= self.plan.review_cutoff():
                raise ValueError('실제 리뷰가 현재 계획의 승인 마감을 벗어났습니다')
        # 병합 자격의 실제 발급/출처 검증과 폐기는 transport의 한 세션으로 제한한다.
        with self.transport.merge_session() as merge:
            preflight_args["executor_identity"] = merge.identity
            bound = FailureHandoffReader(self.handoff, preflight_args["live_reader"],
                                         self.transport.snapshot, clock=self.clock)
            preflight_args["live_reader"] = bound
            locked, self.lease = acquire_failure_preflight(owner=self.lease, handoff=self.handoff,
                read_failure=self.transport.snapshot, **preflight_args)
            proposal = preflight_args["drafts"].read(preflight_args["proposal_id"],
                actor_github_id=preflight_args["allowed_github_id"], now=self._moment())
            link = preflight_args["pr_link"]
            request = plan_failure_merge(handoff=self.handoff, live_reader=bound,
                                         locked=locked, proposal=proposal, pr_link=link)
            # 승인·CI는 직전 잠금 아래 preflight에서 확인했고, 쓰기 직전에는 원본과 짧은 기한을 대조한다.
            self._require_write_window()
            if not 0 <= (self._moment()-_utc_datetime(locked["checked_at"])).total_seconds() < 15:
                raise ValueError("병합 API 호출 전 실행 검증의 유효기간을 지났습니다")
            self._before_write('merge', head=locked['head_sha'], pull_number=locked['pull_number'])
            if not 0 <= (self._moment()-_utc_datetime(locked['checked_at'])).total_seconds() < 15:
                raise ValueError('execution_journal_crossed_merge_preflight_window')
            response = merge.merge(request)
            # API 성공 뒤 읽기 검증 실패를 병합 실패로 해석해 재시도하지 않는다.
            verified = verify_merged_commit(reader=preflight_args["reader"], locked=locked,
                proposal=proposal, pr_link=link, merge_response=response)
            self.merge_sha = verified["merge_sha"]
            if self.control is not None:
                self.control.merged(locked, proposal, link, response)
            sync = plan_exact_sync(reader=preflight_args["reader"], live_reader=bound,
                locked=locked, proposal=proposal, pr_link=link, merge_response=response, now=self._moment())
            for command in sync["commands"]:
                # set 이후 source가 달라지므로 두 번째 호출은 UID/소유권/기한을 대조한다.
                self._require_write_window(allowed_revision=verified["merge_sha"])
                operation = 'argo_'+command[1]
                self._before_write(operation, revision=verified['merge_sha'])
                self.transport.argo("sync", command)
                self._confirm_write(operation, verified['merge_sha'])
            snapshot = self.transport.wait_healthy(verified["merge_sha"], self.plan.normal_image)
            self.check_cancellation()
            receipt = verify_c03_handoff(reader=preflight_args["reader"], snapshot=snapshot,
                locked=locked, proposal=proposal, pr_link=link, merge_response=response, clock=self.clock)
            self._confirm_write('c03', verified['merge_sha'])
            self.lease.release(recovery_verified=True)
            self.finished = True
            return receipt

    def _require_write_window(self, *, allowed_revision=None):
        snapshot = self.transport.snapshot()
        now = self._moment()
        if now >= self.plan.rollback_deadline:
            raise ValueError("원복 시작 기한을 지나 새 C-03 쓰기를 중지합니다")
        app, dep, lease = (snapshot[key] for key in ("application", "deployment", "lease"))
        if (self.handoff is None or lease["metadata"]["uid"] != self.handoff.lease_uid
                or lease["spec"].get("holderIdentity") != self.handoff.executor_holder
                or app["metadata"]["uid"] != self.handoff.application_uid
                or dep["metadata"]["uid"] != self.handoff.deployment_uid
                or lease["metadata"].get("annotations", {}).get(
                    "ai-ops/c03-failure-handoff") != self.handoff.digest()
                or app["spec"].get("project") != "b6-lab"
                or app["spec"].get("syncPolicy", {}).get("automated") is not None
                or app["spec"]["source"].get("repoURL") != self.plan.repository
                or app["spec"]["source"].get("path") != "ai-ops/lab"
                or app["metadata"].get("deletionTimestamp") or dep["metadata"].get("deletionTimestamp")
                or app["spec"]["source"].get("targetRevision") is None
                or len(dep["spec"]["template"]["spec"]["containers"]) != 1
                or dep["spec"].get("replicas") != 1
                or app["spec"]["source"]["targetRevision"] not in
                    (self.plan.failure_revision, allowed_revision)
                or dep["spec"]["template"]["spec"]["containers"][0]["image"] not in
                    (self.plan.failure_image, self.plan.normal_image)
                or not 0 <= (now-_utc_datetime(snapshot["checked_at"])).total_seconds() <= 15):
            raise ValueError("실제 쓰기 직전 소유권·대상이 달라졌습니다")

    def restore(self):
        """현재 실행의 정확한 소유자만 고정 정상 SHA로 복귀한다."""
        if not self.injected or self.finished:
            raise ValueError("이번 프로세스가 시작한 미완료 실습만 원복합니다")
        self.last_step='restore_current_owner_read'
        snapshot = self.transport.snapshot()
        app, dep, lease = (snapshot[key] for key in ("application", "deployment", "lease"))
        holder = lease["spec"].get("holderIdentity")
        if (lease["metadata"].get("uid") != self.plan.lease_uid
                or holder not in (self.lease.holder, self.executor_holder)
                or app["metadata"].get("uid") != self.plan.application_uid
                or app["spec"]["source"].get("targetRevision") is None
                or dep["metadata"].get("uid") != self.plan.deployment_uid
                or app["spec"]["source"] != {"repoURL": self.plan.repository, "path": "ai-ops/lab",
                    "targetRevision": app["spec"]["source"]["targetRevision"]}
                or app["spec"]["source"]["targetRevision"] not in
                    (self.plan.normal_revision, self.plan.failure_revision, self.merge_sha)
                or app.get("operation")
                or dep["spec"]["template"]["spec"]["containers"][0]["image"] not in
                    (self.plan.normal_image, self.plan.failure_image)):
            raise ValueError("수동 원복의 고정 소유자·대상을 확인하지 못했습니다")
        if holder == self.executor_holder:
            # 원복 시작 기한 이후에도 자기 실행의 보존 잠금을 탈취 없이 복구할 수 있다.
            if (self.handoff is None or lease["metadata"].get("annotations", {}).get(
                    "ai-ops/c03-failure-handoff") != self.handoff.digest()):
                raise ValueError("현재 c3 잠금의 실패 인계가 다릅니다")
            self.lease = LabLease(self.transport.lease_command, holder, clock=self.clock)
            self.lease.uid = self.plan.lease_uid
        self.last_step='restore_set_normal_revision'
        self.transport.argo("rollback", ["app", "set", "lab-target", "--revision", self.plan.normal_revision])
        self.last_step='restore_sync_normal_revision'
        self.transport.argo("rollback", ["app", "sync", "lab-target", "--revision", self.plan.normal_revision,
                                        "--timeout", "150"])
        self.last_step='restore_wait_and_verify_normal'
        verify_normal_trial(self.plan, self.transport.wait_healthy(self.plan.normal_revision,
                            self.plan.normal_image), now=self._moment(), held=self.lease)
        self.last_step='restore_release_owned_lease'
        self.lease.release(recovery_verified=True)
        self.finished = True
        if self.control is not None:
            self.control.rollback_verified()

    def cancel_before_injection(self):
        """주입이 시작되기 전 실제 정상 상태를 확인한 자기 잠금만 해제한다."""
        if self.injected or self.finished or self.lease.uid is None:
            raise ValueError("주입 전 자기 잠금 상태가 다릅니다")
        verify_normal_trial(self.plan, self.transport.snapshot(), now=self._moment(), held=self.lease)
        self.lease.release(recovery_verified=True, scenario_started=False)
        self.finished = True
