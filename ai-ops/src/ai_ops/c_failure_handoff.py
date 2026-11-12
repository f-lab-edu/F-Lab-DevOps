"""주입 소유자의 실패를 잠금을 비우지 않고 C-02/C-03에 넘긴다."""

from copy import deepcopy
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ai_ops.b6_coordination import FAILURE_HANDOFF_KEY as HANDOFF_KEY, LabLease, NOT_BEFORE_KEY
from ai_ops.c2_active_capture import ISOLATED_REPOSITORY, _validate_active_failure
from ai_ops.contracts import StrictModel, UTCDateTime, _utc_datetime, sha256_json


Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
Image = Annotated[str, Field(pattern=
    r"^764428911194\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener@sha256:[a-f0-9]{64}$")]
Identity = Annotated[str, Field(min_length=1, max_length=128)]


class FailureHandoff(StrictModel):
    schema_version: Literal[1] = 1
    record_type: Literal["injection_failure_handoff"] = "injection_failure_handoff"
    repository: Literal["https://github.com/joungGo/c03-execution-lab.git"] = ISOLATED_REPOSITORY
    trial_id: Annotated[int, Field(ge=1, le=10**15)]
    owner_holder: Annotated[str, Field(pattern=r"^manual-[a-f0-9]{32}$")]
    executor_holder: Annotated[str, Field(pattern=r"^c3-[a-f0-9]{32}$")]
    lease_uid: Identity
    lease_acquired_at: UTCDateTime
    application_uid: Identity
    deployment_uid: Identity
    deployment_generation: Annotated[int, Field(ge=1)]
    failure_pod_uid: Identity
    failure_revision: Revision
    failure_image: Image
    normal_revision: Revision
    normal_image: Image
    injected_at: UTCDateTime
    offered_at: UTCDateTime
    rollback_deadline: UTCDateTime

    @model_validator(mode="after")
    def bounded_failure(self):
        if (not self.lease_acquired_at <= self.injected_at <= self.offered_at < self.rollback_deadline
                or not 0 < (self.rollback_deadline-self.injected_at).total_seconds() <= 300
                or self.failure_revision == self.normal_revision
                or self.failure_image == self.normal_image):
            raise ValueError("실패 인계의 기한·정상 기준이 유효하지 않습니다")
        return self

    def digest(self):
        return sha256_json(self.model_dump(mode="json"))


def validate_failure_lease(record, application, deployment, lease, *, now):
    """인계는 승인 증거가 아니다. 원본 Lease와 현재 고정 대상을 대조한다."""
    if not isinstance(record, FailureHandoff):
        raise ValueError("정확한 실패 인계가 필요합니다")
    moment = _utc_datetime(now)
    LabLease(lambda *args: None, record.owner_holder)._validate(lease)
    metadata = lease["metadata"]
    spec = lease["spec"]
    holder = spec.get("holderIdentity")
    acquired = _utc_datetime(spec.get("acquireTime"))
    not_before = metadata.get("annotations", {}).get(NOT_BEFORE_KEY)
    if (not record.offered_at <= moment < record.rollback_deadline
            or metadata.get("uid") != record.lease_uid or metadata.get("deletionTimestamp")
            or metadata.get("annotations", {}).get(HANDOFF_KEY) != record.digest()
            or holder not in (record.owner_holder, record.executor_holder)
            or (holder == record.owner_holder and acquired != record.lease_acquired_at)
            or (holder == record.executor_holder and not record.offered_at <= acquired <= moment)
            or (not_before and _utc_datetime(not_before) > moment)
            or application.get("metadata", {}).get("uid") != record.application_uid
            or deployment.get("metadata", {}).get("uid") != record.deployment_uid
            or deployment.get("metadata", {}).get("generation") != record.deployment_generation
            or application.get("metadata", {}).get("deletionTimestamp")
            or deployment.get("metadata", {}).get("deletionTimestamp")
            or application.get("spec", {}).get("source") != {
                "repoURL": record.repository, "path": "ai-ops/lab",
                "targetRevision": record.failure_revision}):
        raise ValueError("실패 인계의 소유자·대상·기한이 다릅니다")
    return holder


def _failure_snapshot(record, snapshot, *, now, offered=True):
    """호출자는 새 Kubernetes 원본과 조회 시각을 반환해야 한다."""
    checked_at = _utc_datetime(snapshot["checked_at"])
    if not 0 <= (now-checked_at).total_seconds() <= 15:
        raise ValueError("실패 인계 원본이 오래됐습니다")
    lease = snapshot["lease"]
    if offered:
        holder = validate_failure_lease(record, snapshot["application"],
            snapshot["deployment"], lease, now=now)
    else:
        holder = record.owner_holder
        LabLease(lambda *args: None, holder)._validate(lease)
        if (lease["metadata"].get("uid") != record.lease_uid
                or lease["metadata"].get("deletionTimestamp")
                or lease["metadata"].get("annotations", {}).get(HANDOFF_KEY)
                or _utc_datetime(lease["spec"].get("acquireTime")) != record.lease_acquired_at):
            raise ValueError("주입 소유자의 원본 Lease가 다릅니다")
    _validate_active_failure(snapshot["application"], snapshot["deployment"],
        snapshot["replica_set"], snapshot["pods"], lease,
        application_uid=record.application_uid, deployment_uid=record.deployment_uid,
        failure_revision=record.failure_revision, failure_image=record.failure_image,
        source_repository=record.repository, expected_lease_holder=holder, now=now)
    if (snapshot["pods"][0]["metadata"].get("uid") != record.failure_pod_uid
            or snapshot["pods"][0]["metadata"].get("deletionTimestamp")
            or snapshot["replica_set"]["metadata"].get("deletionTimestamp")
            or _utc_datetime(snapshot["application"]["status"]["operationState"]["startedAt"]) < record.injected_at
            or _utc_datetime(snapshot["pods"][0]["metadata"]["creationTimestamp"]) < record.injected_at):
        raise ValueError("이전 실행의 실패를 인계할 수 없습니다")
    return lease


def offer_failure_handoff(owner: LabLease, *, read_failure, trial_id, executor_holder,
                           normal_revision, normal_image, failure_revision, failure_image,
                           application_uid, deployment_uid, injected_at, rollback_deadline):
    """주입 프로세스가 소유한 Lease에 실패 인계 hash만 CAS로 기록한다."""
    if not isinstance(owner, LabLease) or owner.uid is None:
        raise ValueError("주입 프로세스가 확인한 자기 잠금이 필요합니다")
    snapshot = read_failure()
    now = _utc_datetime(owner.clock())
    record = FailureHandoff(trial_id=trial_id, owner_holder=owner.holder,
        executor_holder=executor_holder, lease_uid=owner.uid,
        lease_acquired_at=snapshot["lease"]["spec"]["acquireTime"],
        application_uid=application_uid, deployment_uid=deployment_uid,
        deployment_generation=snapshot["deployment"]["metadata"]["generation"],
        failure_pod_uid=snapshot["pods"][0]["metadata"].get("uid"),
        failure_revision=failure_revision, failure_image=failure_image,
        normal_revision=normal_revision, normal_image=normal_image,
        injected_at=injected_at, offered_at=now, rollback_deadline=rollback_deadline)
    lease = _failure_snapshot(record, snapshot, now=now, offered=False)
    current = owner._get()
    if current != lease:
        raise ValueError("실패 조회 뒤 Lease가 바뀌었습니다")
    desired = deepcopy(current)
    desired["metadata"].setdefault("annotations", {})[HANDOFF_KEY] = record.digest()
    response = json.loads(owner.command("replace", "-f", "-", "-o", "json", payload=json.dumps(desired)))
    validate_failure_lease(record, snapshot["application"], snapshot["deployment"],
                           response, now=_utc_datetime(owner.clock()))
    if response["spec"].get("holderIdentity") != owner.holder:
        raise ValueError("실패 인계 제공 뒤 소유권이 바뀌었습니다")
    return record


def transfer_failure_handoff(owner: LabLease, record: FailureHandoff, *, read_failure):
    """같은 UID의 Lease를 비우지 않고 지정 C-03 실행자에게 CAS로 넘긴다."""
    if (not isinstance(owner, LabLease) or not isinstance(record, FailureHandoff)
            or owner.uid != record.lease_uid or owner.holder != record.owner_holder):
        raise ValueError("원래 주입 소유자만 실패 잠금을 넘길 수 있습니다")
    snapshot = read_failure()
    now = _utc_datetime(owner.clock())
    current = _failure_snapshot(record, snapshot, now=now)
    if current["spec"].get("holderIdentity") != owner.holder or owner._get() != current:
        raise ValueError("실패 잠금이 이미 인계됐거나 바뀌었습니다")
    desired = deepcopy(current)
    desired["spec"].update(holderIdentity=record.executor_holder,
                           acquireTime=now.isoformat(), renewTime=now.isoformat())
    response = json.loads(owner.command("replace", "-f", "-", "-o", "json", payload=json.dumps(desired)))
    validate_failure_lease(record, snapshot["application"], snapshot["deployment"],
                           response, now=_utc_datetime(owner.clock()))
    if response["spec"].get("holderIdentity") != record.executor_holder:
        raise ValueError("실패 잠금 인계를 확인하지 못했습니다")
    recipient = LabLease(owner.command, record.executor_holder, clock=owner.clock)
    recipient.uid = record.lease_uid
    owner.uid = None
    # 인계 뒤 대상이 바뀌어도 잠금은 그대로 보존한다.
    confirmed = _failure_snapshot(record, read_failure(), now=_utc_datetime(owner.clock()))
    if confirmed["spec"].get("holderIdentity") != recipient.holder:
        raise ValueError("실패 잠금 인계 뒤 지정 실행자의 소유권을 잃었습니다")
    return recipient


def assess_failure_handoff_proof(record, snapshot, *, now, **proof_args):
    """C-02 장애를 재확인한 뒤 기존 C-03 정상 CI/ECR/사건 증거 경로를 호출한다."""
    from ai_ops.c_recovery_proof import assess_recovery_proof

    now = _utc_datetime(now)
    _failure_snapshot(record, snapshot, now=now)
    return assess_recovery_proof(snapshot["application"], snapshot["deployment"],
        snapshot["lease"], snapshot["values_bytes"], snapshot["replica_set"], snapshot["pods"],
        **proof_args, now=now, expected_lease_holder=snapshot["lease"]["spec"]["holderIdentity"],
        repository="joungGo/c03-execution-lab")


class FailureHandoffReader:
    """기존 C-03 원본 reader 전후로 실패 인계와 남은 기한을 검사한다."""

    def __init__(self, handoff: FailureHandoff, live_reader, read_failure, *, clock):
        self.handoff = handoff
        self.live_reader = live_reader
        self.read_failure = read_failure
        self.clock = clock

    def read(self, *, expected_lease=None):
        record = self.handoff
        if expected_lease != {"name": "b6-scenario-lock", "uid": record.lease_uid,
                              "holder": record.executor_holder}:
            raise ValueError("실패 인계의 지정 실행 잠금이 필요합니다")
        first = self.read_failure()
        _failure_snapshot(record, first, now=_utc_datetime(self.clock()))
        if first["lease"]["spec"].get("holderIdentity") != record.executor_holder:
            raise ValueError("아직 지정 C-03 실행자에게 인계되지 않았습니다")
        result = self.live_reader.read(expected_lease=expected_lease)
        second = self.read_failure()
        _failure_snapshot(record, second, now=_utc_datetime(self.clock()))
        if second["lease"]["spec"].get("holderIdentity") != record.executor_holder:
            raise ValueError("C-03 원본 조회 중 실행 소유권을 잃었습니다")
        if any(first[key] != result[key] or result[key] != second[key]
               for key in ("application", "deployment", "lease")):
            raise ValueError("C-03 원본 조회 중 실패 인계 대상이 바뀌었습니다")
        return result


def acquire_failure_preflight(*, owner, handoff, read_failure, **preflight_args):
    """정확한 제안에만 소유권을 넘기고 기존 C-03 승인·CI·잠금 검사를 실행한다."""
    from ai_ops.c_execution_preflight import verify_execution_preflight

    if not isinstance(handoff, FailureHandoff) or "expected_lease" in preflight_args:
        raise ValueError("실패 인계 실행 입력이 유효하지 않습니다")
    bound_reader = preflight_args["live_reader"]
    if not isinstance(bound_reader, FailureHandoffReader) or bound_reader.handoff != handoff:
        raise ValueError("실패 인계에 묶인 C-03 reader가 필요합니다")
    link = preflight_args["pr_link"]
    proposal = preflight_args["drafts"].read(preflight_args["proposal_id"],
        actor_github_id=preflight_args["allowed_github_id"], now=_utc_datetime(owner.clock()))
    if (proposal.repository != "joungGo/c03-execution-lab"
            or not link.matches_draft(proposal)
            or link.application_uid != handoff.application_uid
            or proposal.target_uid != handoff.deployment_uid
            or link.observed_revision_sha != handoff.failure_revision
            or proposal.current_digest != handoff.failure_image.split("@", 1)[1]
            or proposal.desired_digest != handoff.normal_image.split("@", 1)[1]):
        raise ValueError("실패 인계와 승인 제안의 범위가 다릅니다")
    recipient = transfer_failure_handoff(owner, handoff, read_failure=read_failure)
    held = {"name": "b6-scenario-lock", "uid": recipient.uid, "holder": recipient.holder}
    preflight_args["clock"] = owner.clock
    result = verify_execution_preflight(**preflight_args, expected_lease=held)
    _failure_snapshot(handoff, read_failure(), now=_utc_datetime(owner.clock()))
    return {**result, "locked_preflight_ready": True, "lease": held,
            "failure_handoff_hash": handoff.digest()}, recipient


def plan_failure_merge(*, handoff, live_reader: FailureHandoffReader, locked,
                        proposal, pr_link):
    """기한과 현재 실패를 재검사한 뒤 기존의 정확한 head 병합 계획만 만든다."""
    from ai_ops.c_merge_guard import plan_exact_merge_request

    if (not isinstance(live_reader, FailureHandoffReader) or live_reader.handoff != handoff
            or locked.get("failure_handoff_hash") != handoff.digest()):
        raise ValueError("병합 계획과 실패 인계가 다릅니다")
    lease = _failure_snapshot(handoff, live_reader.read_failure(),
                              now=_utc_datetime(live_reader.clock()))
    if (locked.get("lease") != {"name": "b6-scenario-lock", "uid": handoff.lease_uid,
                               "holder": handoff.executor_holder}
            or lease["spec"].get("holderIdentity") != handoff.executor_holder):
        raise ValueError("병합 직전 지정 실행자의 소유권이 다릅니다")
    plan = plan_exact_merge_request(locked=locked, proposal=proposal, pr_link=pr_link,
                                   now=_utc_datetime(live_reader.clock()))
    _failure_snapshot(handoff, live_reader.read_failure(), now=_utc_datetime(live_reader.clock()))
    return {**plan, "failure_handoff_hash": handoff.digest(),
            "rollback_deadline": handoff.rollback_deadline.isoformat()}
