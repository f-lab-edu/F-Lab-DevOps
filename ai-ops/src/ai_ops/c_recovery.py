"""C단계 실습 복구의 읽기 투영과 실행 불가능한 변경안 계약을 제공한다."""

from __future__ import annotations

from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

from pydantic import Field, model_validator

from ai_ops.contracts import IncidentId, RunId, Sha256, StrictModel, Target, UTCDateTime, _utc_datetime, canonical_json_bytes, sha256_json
from ai_ops.incidents import IncidentStore, REPO_ROOT


REPOSITORY = "f-lab-edu/F-Lab-DevOps"
VALUES_PATH = "ai-ops/lab/values.yaml"
IMAGE_REPOSITORY = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener"
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
TARGET = Target(cluster_id="urlshortener", namespace="ai-ops-lab", workload="lab-api",
                service="lab-api", environment="practice")


def _encoded_values(digest: str) -> str:
    value = {"api": {"image": {"repository": IMAGE_REPOSITORY, "digest": digest}}}
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _image_diff(before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        _encoded_values(before).splitlines(keepends=True),
        _encoded_values(after).splitlines(keepends=True),
        fromfile="a/" + VALUES_PATH, tofile="b/" + VALUES_PATH,
    ))


def _private(path: Path, *, directory: bool) -> None:
    """심볼릭 링크와 다른 사용자에게 열린 자료 경로를 거부한다."""
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or not directory and info.st_nlink != 1):
        raise ValueError("C 자료 경로의 소유자·권한이 유효하지 않습니다")


class ImageFailureBinding(StrictModel):
    """보고서가 인용한 이미지 장애 세 자료의 원문 없는 결합값이다."""

    schema_version: int = 1
    pod_uid_hash: Sha256
    deployment_uid_hash: Sha256
    image_hash: Sha256
    observed_at: UTCDateTime
    evidence_ids: list[str] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def fixed_shape(self):
        if (self.schema_version != 1 or len(set(self.evidence_ids)) != 3
                or self.evidence_ids != sorted(self.evidence_ids)):
            raise ValueError("이미지 장애 결합값의 근거 ID가 유효하지 않습니다")
        return self


class IncidentProjection(StrictModel):
    schema_version: int = 1
    incident_id: IncidentId
    run_id: RunId
    target: Target
    bundle_hash: Sha256
    report_hash: Sha256
    audit_hash: Sha256
    report_status: str
    diagnosis_status: str
    current_state: str
    evidence_counts: dict[str, int]
    cited_evidence_ids: list[str]
    image_failure: ImageFailureBinding | None = None

    @model_validator(mode="after")
    def fixed_target(self):
        if self.schema_version != 1 or self.target != TARGET:
            raise ValueError("C 사건 투영은 등록된 실습 대상만 허용합니다")
        return self


def _image_failure_binding(record) -> ImageFailureBinding | None:
    """재검증된 v3 보고서의 Pod·이벤트·Deployment 인용만 추출한다."""
    if (getattr(getattr(record.report, "execution", None), "engine", None) != "rules-captured-v3"
            or record.report.diagnosis_status != "suspected_cause"):
        return None
    items = {item.evidence_id: item for item in record.bundle.evidence}
    entries = {entry.evidence_id: entry for entry in record.collection_audit.entries}
    candidates = []
    for fact in record.report.facts:
        ids = set(fact.evidence_ids)
        if len(ids) != 3 or not any(set(hypothesis.supporting_evidence_ids) == ids
                                    for hypothesis in record.report.hypotheses):
            continue
        selected = [items.get(item_id) for item_id in ids]
        if any(item is None or item.status != "available" or item.provenance.kind != "captured"
               for item in selected) or {item.kind for item in selected} != {"workload_status", "event", "deployment"}:
            continue
        pod = next(item for item in selected if item.kind == "workload_status")
        event = next(item for item in selected if item.kind == "event")
        deployment = next(item for item in selected if item.kind == "deployment")
        p, e, d = (entries.get(item.evidence_id) for item in (pod, event, deployment))
        if (any(entry is None or entry.status != "available" or entry.reason != "collected"
                for entry in (p, e, d))
                or p.time_field != "response_received_at" or d.time_field != "response_received_at"
                or e.time_field not in {"eventTime", "lastTimestamp"}
                or not all((p.resource_uid, p.resource_version, p.deployment_uid,
                            p.replicaset_uid, p.replicaset_resource_version))
                or d.resource_uid != p.deployment_uid
                or pod.payload.get("phase") != "Pending" or pod.payload.get("ready") is not False
                or pod.payload.get("image") != deployment.payload.get("image")
                or event.payload.get("type") != "Warning"
                or event.payload.get("involved_uid") != p.resource_uid
                or event.payload.get("reason") not in {"Failed", "BackOff", "ImagePullBackOff", "ErrImagePull"}
                or (event.payload["reason"] in {"Failed", "BackOff"} and not re.search(
                    r"(?<![\w])(?:ImagePullBackOff|ErrImagePull)(?![\w])", event.payload.get("message", "")))
                or not 0 <= (pod.observed_at - event.observed_at).total_seconds() <= 120
                or abs((deployment.observed_at - pod.observed_at).total_seconds()) > 120):
            continue
        candidates.append(ImageFailureBinding(
            pod_uid_hash=sha256_json(p.resource_uid), deployment_uid_hash=sha256_json(p.deployment_uid),
            image_hash=sha256_json(pod.payload["image"]), observed_at=pod.observed_at,
            evidence_ids=sorted(ids),
        ))
    return candidates[0] if len(candidates) == 1 else None


def read_incident_projection(root: Path, incident_id: str, run_id: str) -> IncidentProjection:
    """기존 저장 계약을 재검증하고 원문 없는 실습 사건 요약만 반환한다."""
    root = root.expanduser().absolute()
    if root.resolve().is_relative_to(REPO_ROOT.resolve()):
        raise ValueError("실행 자료를 저장소에서 읽을 수 없습니다")
    _private(root, directory=True)
    store = object.__new__(IncidentStore)
    store.root = root
    record = store._read_record(incident_id, run_id)
    if (record.request is None or record.bundle is None or record.report is None
            or record.collection_audit is None or record.request.target != TARGET
            or record.report.target != TARGET or record.request.source != "manual_snapshot"):
        raise ValueError("등록된 실습 사건의 검증된 수집 보고서가 필요합니다")
    ids = set()
    for item in record.report.facts:
        ids.update(item.evidence_ids)
    for item in record.report.hypotheses:
        ids.update(item.supporting_evidence_ids)
        ids.update(item.contradicting_evidence_ids)
    counts = Counter(f"{e.kind}:{e.status}" for e in record.bundle.evidence)
    return IncidentProjection(
        incident_id=incident_id, run_id=run_id, target=TARGET,
        bundle_hash=record.bundle.bundle_hash,
        report_hash=sha256_json(record.report.model_dump(mode="json")),
        audit_hash=sha256_json({"bundle_hash": record.bundle.bundle_hash,
                                "audit": record.collection_audit.model_dump(mode="json")}),
        report_status=record.report.analysis_status,
        diagnosis_status=record.report.diagnosis_status,
        current_state=record.report.current_state,
        evidence_counts=dict(sorted(counts.items())), cited_evidence_ids=sorted(ids),
        image_failure=_image_failure_binding(record),
    )


class RecoveryRequest(StrictModel):
    schema_version: int = 1
    request_id: str = Field(pattern=r"^creq-[0-9a-f]{32}$")
    requester_github_id: int = Field(gt=0)
    incident_id: IncidentId
    run_id: RunId
    bundle_hash: Sha256
    report_hash: Sha256
    failure_binding_hash: Sha256 | None = None
    target: Target
    requested_at: UTCDateTime

    @model_validator(mode="after")
    def fixed_target(self):
        if self.schema_version != 1 or self.target != TARGET:
            raise ValueError("C 해결 요청은 등록된 실습 대상만 허용합니다")
        return self


def create_request(projection: IncidentProjection, requester_github_id: int,
                   *, now: datetime | None = None) -> RecoveryRequest:
    """인증 주체는 추후 서비스가 검증해 전달하며 요청은 변경 승인이 아니다."""
    if type(requester_github_id) is not int or requester_github_id <= 0 or projection.target != TARGET:
        raise ValueError("실습 사건과 검증된 GitHub 사용자 ID가 필요합니다")
    return RecoveryRequest(request_id="creq-" + uuid.uuid4().hex,
                           requester_github_id=requester_github_id,
                           incident_id=projection.incident_id, run_id=projection.run_id,
                           bundle_hash=projection.bundle_hash, report_hash=projection.report_hash,
                           failure_binding_hash=(sha256_json(projection.image_failure.model_dump(mode="json"))
                                                 if projection.image_failure else None),
                           target=TARGET, requested_at=now or datetime.now(timezone.utc))


class RecoveryProposal(StrictModel):
    schema_version: int = 1
    proposal_id: str = Field(pattern=r"^cprop-[0-9a-f]{32}$")
    request_id: str = Field(pattern=r"^creq-[0-9a-f]{32}$")
    incident_id: IncidentId
    run_id: RunId
    bundle_hash: Sha256
    report_hash: Sha256
    target: Target
    target_uid: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    repository: str
    values_path: str
    base_sha: str
    current_digest: str
    desired_digest: str
    diff: str = Field(min_length=1, max_length=8192)
    diff_hash: Sha256
    proposal_hash: Sha256
    created_at: UTCDateTime
    expires_at: UTCDateTime
    state: str = "draft"
    failure_binding_hash: Sha256 | None = None
    recheck_hash: Sha256 | None = None
    source_evidence_ids: list[str] = Field(default_factory=list)
    impact_scope: str | None = None
    verification_checks: list[str] = Field(default_factory=list)
    failure_action: str | None = None

    @model_validator(mode="after")
    def fixed_scope(self):
        if (self.schema_version not in (1, 2) or self.target != TARGET or self.repository != REPOSITORY or self.values_path != VALUES_PATH
                or self.state != "draft" or not COMMIT.fullmatch(self.base_sha)
                or not DIGEST.fullmatch(self.current_digest) or not DIGEST.fullmatch(self.desired_digest)
                or self.current_digest == self.desired_digest
                or not 0 < (self.expires_at - self.created_at).total_seconds() <= 86400
                or self.diff != _image_diff(self.current_digest, self.desired_digest)
                or hashlib.sha256(self.diff.encode()).hexdigest() != self.diff_hash):
            raise ValueError("복구 변경안의 대상·시간·diff 계약이 유효하지 않습니다")
        if self.schema_version == 2:
            if (self.failure_binding_hash is None or self.recheck_hash is None
                    or len(self.source_evidence_ids) != 3
                    or len(set(self.source_evidence_ids)) != 3
                    or self.source_evidence_ids != sorted(self.source_evidence_ids)
                    or self.impact_scope != "ai-ops-lab/lab-api image only"
                    or self.verification_checks != [
                        "argo_synced_healthy", "pod_image_matches_desired_digest",
                        "pod_ready", "readyz_success", "items_flow_success"]
                    or self.failure_action != "stop_and_report_without_unapproved_rollback"):
                raise ValueError("검증된 변경안의 근거·영향·확인·실패 조건이 유효하지 않습니다")
        elif (self.failure_binding_hash is not None or self.recheck_hash is not None
              or self.source_evidence_ids or self.impact_scope is not None
              or self.verification_checks or self.failure_action is not None):
            raise ValueError("오프라인 초안에는 검증된 변경안 필드를 넣을 수 없습니다")
        body = self.model_dump(mode="json", exclude={"proposal_hash"})
        hashes = {sha256_json(body)}
        if self.schema_version == 1:
            old_fields = {"failure_binding_hash", "recheck_hash", "source_evidence_ids",
                          "impact_scope", "verification_checks", "failure_action"}
            hashes.add(sha256_json({key: value for key, value in body.items() if key not in old_fields}))
        if self.proposal_hash not in hashes:
            raise ValueError("복구 변경안 해시가 일치하지 않습니다")
        return self


def _values(raw: bytes) -> dict:
    """현재 Helm values를 작은 JSON 부분집합으로 고정한다. JSON은 YAML 문법이다."""
    if len(raw) > 2048:
        raise ValueError("실습 values 크기가 허용 범위를 넘었습니다")
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"api"}
            or not isinstance(value["api"], dict) or set(value["api"]) != {"image"}
            or not isinstance(value["api"]["image"], dict)
            or set(value["api"]["image"]) != {"repository", "digest"}
            or value["api"]["image"]["repository"] != IMAGE_REPOSITORY
            or not isinstance(value["api"]["image"]["digest"], str)
            or not DIGEST.fullmatch(value["api"]["image"]["digest"])):
        raise ValueError("실습 values에 허용되지 않는 필드가 있습니다")
    return value


def draft_proposal(request: RecoveryRequest, *, target_uid: str, base_sha: str,
                   current_image: str, desired_digest: str, values_bytes: bytes,
                   now: datetime | None = None) -> RecoveryProposal:
    """이미지 한 필드의 재현 가능한 초안만 만든다. ECR·PR·클러스터 변경은 하지 않는다."""
    if request.target != TARGET or not COMMIT.fullmatch(base_sha):
        raise ValueError("실습 대상과 고정 Git revision이 필요합니다")
    current = _values(values_bytes)
    current_digest = current["api"]["image"]["digest"]
    if (current_image != IMAGE_REPOSITORY + "@" + current_digest
            or not isinstance(desired_digest, str) or not DIGEST.fullmatch(desired_digest)
            or current_digest == desired_digest):
        raise ValueError("현재 이미지와 복구 digest가 유효하지 않습니다")
    # values 파일의 고정 형식을 유지해 한 줄의 digest만 바뀌게 한다.
    expected = _encoded_values(current_digest).encode()
    if values_bytes != expected:
        raise ValueError("실습 values의 고정 직렬화 형식이 다릅니다")
    diff = _image_diff(current_digest, desired_digest)
    moment = now or datetime.now(timezone.utc)
    if moment.utcoffset() != timedelta(0):
        raise ValueError("초안 시각은 UTC여야 합니다")
    body = dict(schema_version=1, proposal_id="cprop-" + uuid.uuid4().hex,
                request_id=request.request_id, incident_id=request.incident_id, run_id=request.run_id,
                bundle_hash=request.bundle_hash, report_hash=request.report_hash,
                target=TARGET.model_dump(mode="json"), target_uid=target_uid,
                repository=REPOSITORY, values_path=VALUES_PATH, base_sha=base_sha,
                current_digest=current_digest, desired_digest=desired_digest,
                diff=diff, diff_hash=hashlib.sha256(diff.encode()).hexdigest(),
                created_at=moment.isoformat().replace("+00:00", "Z"),
                expires_at=(moment + timedelta(hours=24)).isoformat().replace("+00:00", "Z"), state="draft")
    return RecoveryProposal.model_validate({**body, "proposal_hash": sha256_json(body)})


def draft_verified_proposal(request: RecoveryRequest, projection: IncidentProjection,
                            readiness: dict, values_bytes: bytes,
                            *, now: datetime | None = None) -> RecoveryProposal:
    """읽기 전용 재확인 결과로 v2 초안을 만든다. 승인·PR·배포는 하지 않는다."""
    moment = now or datetime.now(timezone.utc)
    if moment.utcoffset() != timedelta(0):
        raise ValueError("초안 시각은 UTC여야 합니다")
    failure = projection.image_failure
    if (failure is None or readiness.get("proposal_ready") is not True
            or readiness.get("proposal_allowed") is not False
            or readiness.get("blockers") != ["draft_only_c3_approval_required"]
            or request.incident_id != projection.incident_id or request.run_id != projection.run_id
            or request.bundle_hash != projection.bundle_hash or request.report_hash != projection.report_hash
            or request.failure_binding_hash != sha256_json(failure.model_dump(mode="json"))
            or readiness.get("request_id") != request.request_id
            or readiness.get("incident_id") != projection.incident_id
            or readiness.get("run_id") != projection.run_id
            or readiness.get("bundle_hash") != projection.bundle_hash
            or readiness.get("report_hash") != projection.report_hash
            or readiness.get("failure_binding_hash") != request.failure_binding_hash
            or readiness.get("target") != "urlshortener/ai-ops-lab/lab-api/practice"
            or readiness.get("pod_uid_hash") != failure.pod_uid_hash
            or readiness.get("deployment_uid_hash") != failure.deployment_uid_hash
            or readiness.get("image_hash") != failure.image_hash
            or readiness.get("current_digest") == readiness.get("prior_digest")):
        raise ValueError("재확인 근거와 사건·요청이 일치하지 않습니다")
    from ai_ops.c_recovery_proof import PROPOSAL_IDENTITY_FIELDS
    try:
        binding = {field: readiness[field] for field in PROPOSAL_IDENTITY_FIELDS}
        binding["proof_checked_at"] = readiness["proof_checked_at"]
        checked = _utc_datetime(readiness["proof_checked_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("재확인 결합값이 유효하지 않습니다") from exc
    if (readiness.get("proposal_readiness_hash") != sha256_json(binding)
            or not checked <= moment <= checked + timedelta(seconds=30)):
        raise ValueError("재확인 근거가 변경됐거나 만료됐습니다")
    offline = draft_proposal(request, target_uid=readiness["target_uid"],
                             base_sha=readiness["base_sha"],
                             current_image=IMAGE_REPOSITORY + "@" + readiness["current_digest"],
                             desired_digest=readiness["prior_digest"],
                             values_bytes=values_bytes, now=moment)
    body = offline.model_dump(mode="json", exclude={"proposal_hash"})
    body.update(schema_version=2, failure_binding_hash=request.failure_binding_hash,
                recheck_hash=readiness["proposal_readiness_hash"],
                source_evidence_ids=failure.evidence_ids,
                impact_scope="ai-ops-lab/lab-api image only",
                verification_checks=["argo_synced_healthy", "pod_image_matches_desired_digest",
                                     "pod_ready", "readyz_success", "items_flow_success"],
                failure_action="stop_and_report_without_unapproved_rollback")
    return RecoveryProposal.model_validate({**body, "proposal_hash": sha256_json(body)})


class RecoveryDraftStore:
    """개인 디스크의 초안만 원자 저장한다. 승인·실행 상태는 수용하지 않는다."""

    def __init__(self, root: Path):
        self.root = root.expanduser().absolute()
        if self.root.resolve().is_relative_to(REPO_ROOT.resolve()):
            raise ValueError("C 상태는 저장소 밖에 보관해야 합니다")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private(self.root, directory=True)
        self.path = self.root / "recovery-drafts.sqlite3"
        if self.path.exists() or self.path.is_symlink():
            _private(self.path, directory=False)
        else:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, body BLOB NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, body BLOB NOT NULL)")

    @contextmanager
    def _connect(self):
        _private(self.root, directory=True)
        _private(self.path, directory=False)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            with db:
                yield db
        finally:
            db.close()

    def save_request(self, request: RecoveryRequest) -> RecoveryRequest:
        rb = canonical_json_bytes(request.model_dump(mode="json"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT body FROM requests WHERE id=?", (request.request_id,)).fetchone()
            if existing and existing[0] != rb:
                raise ValueError("동일 요청 ID의 내용이 다릅니다")
            db.execute("INSERT OR IGNORE INTO requests VALUES (?,?)", (request.request_id, rb))
        return request

    def read_request(self, request_id: str) -> RecoveryRequest:
        if not re.fullmatch(r"creq-[0-9a-f]{32}", request_id):
            raise ValueError("해결 요청 ID가 유효하지 않습니다")
        with self._connect() as db:
            row = db.execute("SELECT body FROM requests WHERE id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("해결 요청이 없습니다")
        return RecoveryRequest.model_validate_json(row[0])

    def save(self, request: RecoveryRequest, proposal: RecoveryProposal) -> RecoveryProposal:
        if (request.request_id != proposal.request_id or request.incident_id != proposal.incident_id
                or request.run_id != proposal.run_id or request.target != proposal.target
                or request.bundle_hash != proposal.bundle_hash or request.report_hash != proposal.report_hash
                or (proposal.schema_version == 2 and
                    request.failure_binding_hash != proposal.failure_binding_hash)):
            raise ValueError("요청과 변경안이 일치하지 않습니다")
        if proposal.state != "draft":
            raise ValueError("초안 외 상태는 아직 저장할 수 없습니다")
        rb = canonical_json_bytes(request.model_dump(mode="json"))
        pb = canonical_json_bytes(proposal.model_dump(mode="json"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT body FROM requests WHERE id=?", (request.request_id,)).fetchone()
            if existing and existing[0] != rb:
                raise ValueError("동일 요청 ID의 내용이 다릅니다")
            previous = db.execute("SELECT body FROM proposals WHERE request_id=?", (request.request_id,)).fetchone()
            if previous and previous[0] != pb:
                raise ValueError("이미 다른 변경안이 등록된 요청입니다")
            collision = db.execute("SELECT body FROM proposals WHERE id=?", (proposal.proposal_id,)).fetchone()
            if collision and collision[0] != pb:
                raise ValueError("동일 변경안 ID의 내용이 다릅니다")
            db.execute("INSERT OR IGNORE INTO requests VALUES (?,?)", (request.request_id, rb))
            db.execute("INSERT OR IGNORE INTO proposals VALUES (?,?,?)", (proposal.proposal_id, request.request_id, pb))
        return proposal

    def read(self, proposal_id: str) -> RecoveryProposal:
        if not re.fullmatch(r"cprop-[0-9a-f]{32}", proposal_id):
            raise ValueError("변경안 ID가 유효하지 않습니다")
        with self._connect() as db:
            row = db.execute("SELECT body FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise ValueError("변경안이 없습니다")
        return RecoveryProposal.model_validate_json(row[0])


def read_existing_request(root: Path, request_id: str) -> RecoveryRequest:
    """감사 명령에서 기존 개인 요청만 읽는다. DB나 폴더를 새로 만들지 않는다."""
    if not re.fullmatch(r"creq-[0-9a-f]{32}", request_id):
        raise ValueError("해결 요청 ID가 유효하지 않습니다")
    root = root.expanduser().absolute()
    if root.resolve().is_relative_to(REPO_ROOT.resolve()):
        raise ValueError("C 상태는 저장소 밖에 보관해야 합니다")
    _private(root, directory=True)
    path = root / "recovery-drafts.sqlite3"
    _private(path, directory=False)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        row = db.execute("SELECT body FROM requests WHERE id=?", (request_id,)).fetchone()
    if row is None:
        raise ValueError("해결 요청이 없습니다")
    return RecoveryRequest.model_validate_json(row[0])
