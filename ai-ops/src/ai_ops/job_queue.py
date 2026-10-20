"""B-04 수동 요청의 영속 접수·중복·시도·lease를 관리한다."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from ai_ops.b_contracts import BSettings
from ai_ops.contracts import sha256_json
from ai_ops.evidence import sanitize_question
from ai_ops.incidents import IncidentStore, _private_directory
from ai_ops.path_policy import permitted_path

STATES = ("queued", "collecting", "analyzing", "completed", "partial", "failed", "cancelled", "needs_review")
ACTIVE = ("collecting", "analyzing")
LEASE_SECONDS = 60
MAX_ATTEMPTS = 3
MAX_WAITING = 100
RETENTION_SECONDS = 7 * 24 * 60 * 60


class QueueBusy(ValueError):
    """다른 worker가 같은 저장소에서 실행 중이다."""


class LeaseLost(ValueError):
    """시도 소유권이 만료되거나 취소되어 기록할 수 없다."""


def utc(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


class JobQueue:
    """동시 실행 한 개·미완료 100개·최대 세 시도로 시작하는 로컬 PVC 큐다."""

    def __init__(self, store: Path, *, clock=time.time):
        self.root = permitted_path(store, "store")
        _private_directory(self.root)
        self.engine = IncidentStore(self.root)
        self.directory = self.root / "queue"
        _private_directory(self.directory)
        self.path = self.directory / "jobs.sqlite3"
        self.clock = clock
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError("지원하지 않는 큐 버전입니다")
            db.execute("CREATE TABLE IF NOT EXISTS episodes (episode_key TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL, created_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, episode_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','collecting','analyzing','completed','partial','failed','cancelled','needs_review')), generation INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0, lease_until REAL, available_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, reason TEXT, snapshot_hash TEXT, incident_id TEXT, run_id TEXT, bundle_hash TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts (job_id TEXT NOT NULL, generation INTEGER NOT NULL, parent_generation INTEGER, request_id TEXT UNIQUE NOT NULL, state TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL, reason TEXT, PRIMARY KEY(job_id,generation))")
            db.execute("CREATE INDEX IF NOT EXISTS waiting_jobs ON jobs(state,available_at,created_at)")
            # v1의 작업·시도·중복 키는 그대로 두고 삭제 재개 장부만 추가한다.
            # v2를 모르는 이전 worker는 저장소를 열지 못해 삭제된 키의 재접수를 막는다.
            db.execute("CREATE TABLE IF NOT EXISTS purges (job_id TEXT PRIMARY KEY, created_at REAL NOT NULL, deadline REAL NOT NULL, plan TEXT NOT NULL, completed_at REAL)")
            db.execute("PRAGMA user_version=2")

    def now(self) -> float:
        value = self.clock()
        if not math.isfinite(value) or value < 0:
            raise ValueError("큐 시각이 유효하지 않습니다")
        return value

    def _safe_files(self):
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                info = Path(str(self.path) + suffix).lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
                raise ValueError("큐 파일 권한 또는 링크가 안전하지 않습니다")

    @contextmanager
    def transaction(self):
        self._safe_files()
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=5000")
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise ValueError("큐 저장소는 WAL을 지원해야 합니다")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @contextmanager
    def worker_lock(self):
        """프로세스가 멈추면 OS가 해제하는 잠금으로 단일 worker를 강제한다."""
        fd = os.open(self.directory / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError("worker 잠금 파일이 안전하지 않습니다")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise QueueBusy("같은 저장소에서 worker가 이미 실행 중입니다") from None
            yield
        finally:
            os.close(fd)

    @staticmethod
    def _id(job_id):
        if not re.fullmatch(r"job-[0-9a-f]{32}", job_id):
            raise ValueError("작업 ID 형식이 유효하지 않습니다")

    def submit(self, settings: BSettings, *, target: str, question: str, request_id: str,
               route: str | None = None, operation: str | None = None) -> tuple[str, bool]:
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request_id):
            raise ValueError("요청 키는 영문·숫자와 . _ : - 로 된 1~128자여야 합니다")
        binding = next((b for b in settings.bindings.bindings if b.alias == target), None)
        if binding is None:
            raise ValueError("등록되지 않은 조사 대상입니다")
        settings.metrics.render("api_latency", binding, dimension=route)
        settings.metrics.render("db_latency", binding, dimension=operation)
        cleaned, _ = sanitize_question(question)
        payload = {"target": target, "question": cleaned, "config_hash": settings.config_hash,
                   "route": route, "operation": operation, "mode": "rules"}
        fingerprint = self.engine.request_fingerprint({"payload": payload, "original_question": question})
        episode_key = sha256_json({"source": "manual", "request_id": request_id})
        now = self.now()
        with self.transaction() as db:
            episode = db.execute("SELECT job_id FROM episodes WHERE episode_key=?", (episode_key,)).fetchone()
            if episode and db.execute("SELECT 1 FROM purges WHERE job_id=?", (episode["job_id"],)).fetchone():
                raise ValueError("보관 만료로 삭제 중이거나 삭제된 요청 키입니다. 새 요청 키를 사용하세요")
            row = db.execute("SELECT job_id,fingerprint FROM jobs WHERE episode_key=?", (episode_key,)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise ValueError("동일 요청 키에 다른 내용을 사용할 수 없습니다")
                return row["job_id"], True
            if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','collecting','analyzing')").fetchone()[0] >= MAX_WAITING:
                raise ValueError("대기 큐가 가득 찼습니다")
            job = "job-" + uuid.uuid4().hex
            db.execute("INSERT INTO episodes VALUES (?,?,?)", (episode_key, job, now))
            db.execute("INSERT INTO jobs (job_id,episode_key,fingerprint,payload,state,available_at,created_at,updated_at) VALUES (?,?,?,?,'queued',?,?,?)",
                       (job, episode_key, fingerprint, json.dumps(payload, ensure_ascii=False), now, now, now))
        return job, False

    def show(self, job: str) -> dict:
        self._id(job)
        with self.transaction() as db:
            purge = db.execute("SELECT deadline,completed_at FROM purges WHERE job_id=?", (job,)).fetchone()
            if purge:
                return {"job_id": job, "state": "purged" if purge["completed_at"] is not None else "purging",
                        "retention_deadline": utc(purge["deadline"]),
                        "purged_at": utc(purge["completed_at"]) if purge["completed_at"] is not None else None}
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job,)).fetchone()
            if row is None:
                raise ValueError("작업을 찾을 수 없습니다")
            attempts = db.execute("SELECT generation,parent_generation,state,reason FROM attempts WHERE job_id=? ORDER BY generation", (job,)).fetchall()
        # 질문·원래 요청 키·연결 설정은 조회 결과에 포함하지 않는다.
        fields = ("job_id", "state", "attempts", "generation", "reason", "incident_id", "run_id", "bundle_hash")
        return {**{key: row[key] for key in fields}, "created_at": utc(row["created_at"]),
                "updated_at": utc(row["updated_at"]), "lease_until": utc(row["lease_until"]) if row["lease_until"] else None,
                "retention_deadline": utc(row["created_at"] + RETENTION_SECONDS),
                "attempt_history": [dict(item) for item in attempts]}

    def cancel(self, job: str) -> dict:
        self._id(job)
        with self.transaction() as db:
            row = db.execute("SELECT state,generation FROM jobs WHERE job_id=?", (job,)).fetchone()
            if row is None:
                raise ValueError("작업을 찾을 수 없습니다")
            if row["state"] in ("queued", *ACTIVE):
                now = self.now()
                db.execute("UPDATE jobs SET state='cancelled',reason='operator_cancelled',lease_until=NULL,updated_at=? WHERE job_id=?", (now, job))
                db.execute("UPDATE attempts SET state='cancelled',reason='operator_cancelled',finished_at=? WHERE job_id=? AND generation=?", (now, job, row["generation"]))
        return self.show(job)

    def expired(self) -> list[dict]:
        with self.transaction() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs WHERE state IN ('collecting','analyzing') AND lease_until<=?", (self.now(),))]

    def claim(self) -> dict | None:
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('collecting','analyzing')").fetchone():
                return None
            now = self.now()
            row = db.execute("SELECT * FROM jobs WHERE state='queued' AND available_at<=? ORDER BY created_at,job_id LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            generation = row["generation"] + 1
            request = "queue-" + row["job_id"][4:] + "-a" + str(generation)
            db.execute("UPDATE jobs SET state='collecting',generation=?,attempts=attempts+1,lease_until=?,updated_at=?,reason=NULL WHERE job_id=?",
                       (generation, now + LEASE_SECONDS, now, row["job_id"]))
            db.execute("INSERT INTO attempts VALUES (?,?,?,?,'collecting',?,NULL,NULL)",
                       (row["job_id"], generation, generation - 1 or None, request, now))
            return dict(db.execute("SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone())

    @staticmethod
    def request_id(job: dict) -> str:
        return "queue-" + job["job_id"][4:] + "-a" + str(job["generation"])

    def assert_owner(self, db, job: dict):
        current = db.execute("SELECT state,generation,lease_until FROM jobs WHERE job_id=?", (job["job_id"],)).fetchone()
        if (current is None or current["generation"] != job["generation"] or current["state"] not in ACTIVE
                or current["lease_until"] <= self.now()):
            raise LeaseLost("작업 소유권이 만료되거나 취소됐습니다")

    def heartbeat(self, job: dict):
        with self.transaction() as db:
            self.assert_owner(db, job)
            db.execute("UPDATE jobs SET lease_until=?,updated_at=? WHERE job_id=?", (self.now() + LEASE_SECONDS, self.now(), job["job_id"]))

    def checkpoint(self, job: dict, digest: str):
        with self.transaction() as db:
            self.assert_owner(db, job)
            db.execute("UPDATE jobs SET state='analyzing',snapshot_hash=?,updated_at=? WHERE job_id=?", (digest, self.now(), job["job_id"]))
            db.execute("UPDATE attempts SET state='analyzing' WHERE job_id=? AND generation=?", (job["job_id"], job["generation"]))

    def finish(self, db, job: dict, record):
        if record.metadata["status"] not in ("completed", "partial") or record.bundle is None:
            raise ValueError("완료된 검증 보고서가 아닙니다")
        now = self.now()
        db.execute("UPDATE jobs SET state=?,lease_until=NULL,updated_at=?,incident_id=?,run_id=?,bundle_hash=?,reason=NULL WHERE job_id=?",
                   (record.metadata["status"], now, record.incident_id, record.run_id, record.bundle.bundle_hash, job["job_id"]))
        db.execute("UPDATE attempts SET state=?,finished_at=? WHERE job_id=? AND generation=?",
                   (record.metadata["status"], now, job["job_id"], job["generation"]))

    def fail(self, job: dict, reason: str, *, recovery: bool = False, review: bool = False):
        if reason not in ("interrupted", "collection_error", "analysis_error", "config_changed", "integrity_error", "operator_stopped"):
            raise ValueError("실패 사유가 허용 목록과 다릅니다")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job["job_id"],)).fetchone()
            if row["generation"] != job["generation"] or row["state"] not in ACTIVE:
                return
            if recovery:
                if row["lease_until"] > self.now():
                    raise LeaseLost("아직 유효한 시도는 복구할 수 없습니다")
            else:
                self.assert_owner(db, job)
            state = "needs_review" if review else "queued" if row["attempts"] < MAX_ATTEMPTS else "failed"
            now = self.now()
            db.execute("UPDATE jobs SET state=?,reason=?,lease_until=NULL,available_at=?,updated_at=? WHERE job_id=?",
                       (state, reason, now + min(5 * 2 ** (row["attempts"] - 1), 20), now, job["job_id"]))
            db.execute("UPDATE attempts SET state='failed',reason=?,finished_at=? WHERE job_id=? AND generation=?", (reason, now, job["job_id"], job["generation"]))
