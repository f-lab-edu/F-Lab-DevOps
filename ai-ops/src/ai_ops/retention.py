"""큐 소유 자료의 보관표와 중단 후 재개 가능한 연계 삭제를 제공한다."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import time

from ai_ops.incidents import ID_PATTERN, _private_directory, _read_json
from ai_ops.contracts import sha256_json
from ai_ops.job_queue import JobQueue, RETENTION_SECONDS, utc

TERMINAL = {"completed", "partial", "failed", "cancelled", "needs_review"}
RUN_FILES = {"input.json", "metadata.json", "report.json", "report.md",
             "collection-audit.json", "tool-audit.json"}


def private_file(path: Path) -> None:
    """삭제 직전에도 링크·소유권·공유 권한을 검사한다."""
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("삭제 대상 파일 권한 또는 링크가 안전하지 않습니다")


def unlink_private(path: Path) -> None:
    private_file(path)
    path.unlink()


def sync_directory(path: Path) -> None:
    """이름 이동과 삭제도 디렉터리에 반영한 뒤 다음 단계를 기록한다."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class QueueRetention:
    """7일 기한은 접수 시각 기준이며 질문·본문을 보관표에 노출하지 않는다."""

    def __init__(self, queue: JobQueue):
        self.queue = queue
        self.engine = queue.engine
        self.staging = queue.directory / "expired"

    @staticmethod
    def _run_files(directory: Path) -> None:
        _private_directory(directory)
        for path in directory.iterdir():
            if path.name not in RUN_FILES and not re.fullmatch(r"\.tmp-[A-Za-z0-9_-]+", path.name):
                raise ValueError("실행 폴더에 소유권을 확인하지 못한 파일이 있습니다")
            private_file(path)

    def _plan(self, db, job: dict) -> dict:
        if any(self.queue.directory.glob(".tmp-*")):
            raise ValueError("소유자를 확인하지 못한 큐 임시 파일을 먼저 검토해야 합니다")
        requests = [row[0] for row in db.execute(
            "SELECT request_id FROM attempts WHERE job_id=? ORDER BY generation", (job["job_id"],))]
        runs = []
        # 인덱스 게시 직전 중단된 실행도 metadata 또는 고정 input으로 소유자를 대조한다.
        for incident in self.queue.root.glob("inc-*"):
            if not ID_PATTERN.fullmatch(incident.name):
                raise ValueError("사건 폴더 이름이 유효하지 않습니다")
            if incident.is_symlink() or not incident.is_dir():
                raise ValueError("사건 경로가 안전하지 않습니다")
            for directory in incident.glob("run-*"):
                if not ID_PATTERN.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
                    raise ValueError("실행 경로가 안전하지 않습니다")
                metadata = directory / "metadata.json"
                source = directory / "input.json"
                if metadata.exists() or metadata.is_symlink():
                    value = _read_json(metadata)
                    if value.get("incident_id") != incident.name or value.get("run_id") != directory.name:
                        raise ValueError("실행 metadata와 경로가 일치하지 않습니다")
                elif source.exists() or source.is_symlink():
                    value = _read_json(source).get("request", {})
                    if value.get("incident_id") != incident.name:
                        raise ValueError("실행 input과 사건 경로가 일치하지 않습니다")
                else:
                    # 연결 정보가 없는 부분 파일은 임의로 특정 큐 작업에 귀속시키지 않는다.
                    raise ValueError("소유자를 확인할 수 없는 미완성 실행을 먼저 검토해야 합니다")
                request = value.get("request_id")
                if request in requests:
                    self._run_files(directory)
                    runs.append({"incident_id": incident.name, "run_id": directory.name, "request_id": request})
        plan = {"job_id": job["job_id"], "requests": requests, "runs": runs}
        self._validate_plan(plan)
        self._validate_indexes(plan)
        snapshot = self.queue.directory / (job["job_id"] + ".json")
        if snapshot.exists() or snapshot.is_symlink():
            private_file(snapshot)
        return plan

    def _validate_plan(self, plan: dict) -> None:
        if set(plan) != {"job_id", "requests", "runs"}:
            raise ValueError("삭제 계획 구조가 유효하지 않습니다")
        self.queue._id(plan["job_id"])
        allowed = {f"queue-{plan['job_id'][4:]}-a{n}" for n in range(1, 4)}
        if (not isinstance(plan["requests"], list) or len(plan["requests"]) > 3
                or not set(plan["requests"]) <= allowed or len(set(plan["requests"])) != len(plan["requests"])):
            raise ValueError("삭제 계획의 시도 소유권이 다릅니다")
        if not isinstance(plan["runs"], list) or len(plan["runs"]) > 3:
            raise ValueError("삭제 계획의 실행 수가 유효하지 않습니다")
        seen = set()
        for run in plan["runs"]:
            if set(run) != {"incident_id", "run_id", "request_id"} or run["request_id"] not in plan["requests"]:
                raise ValueError("삭제 계획의 실행 소유권이 다릅니다")
            self.engine._run_dir(run["incident_id"], run["run_id"])
            if run["request_id"] in seen:
                raise ValueError("한 시도에 여러 실행이 연결됐습니다")
            seen.add(run["request_id"])

    def _validate_indexes(self, plan: dict) -> None:
        owners = {run["request_id"]: run for run in plan["runs"]}
        for request in plan["requests"]:
            path = self.engine._index_path(request)
            if path.exists() or path.is_symlink():
                private_file(path)
                value = _read_json(path)
                run = owners.get(request)
                if (run is None or value.get("request_id") != request
                        or any(value.get(key) != run[key] for key in ("incident_id", "run_id"))):
                    raise ValueError("요청 인덱스와 삭제 대상이 다릅니다")

    @staticmethod
    def _check_owner(directory: Path, run: dict, *, partial: bool = False) -> None:
        metadata = directory / "metadata.json"
        source = directory / "input.json"
        if metadata.exists() or metadata.is_symlink():
            value = _read_json(metadata)
            if any(value.get(key) != run[key] for key in ("request_id", "incident_id", "run_id")):
                raise ValueError("삭제 직전 실행 소유권이 변경됐습니다")
        elif source.exists() or source.is_symlink():
            value = _read_json(source).get("request", {})
            if any(value.get(key) != run[key] for key in ("request_id", "incident_id")):
                raise ValueError("삭제 직전 입력 소유권이 변경됐습니다")
        elif not partial:
            raise ValueError("삭제 직전 실행 소유권을 확인할 수 없습니다")

    def _stage(self, plan: dict) -> Path:
        _private_directory(self.staging)
        stage = self.staging / plan["job_id"]
        _private_directory(stage)
        for run in plan["runs"]:
            source = self.engine._run_dir(run["incident_id"], run["run_id"])
            destination = stage / (run["incident_id"] + "--" + run["run_id"])
            if source.exists():
                self._run_files(source)
                self._check_owner(source, run)
                if destination.exists() or destination.is_symlink():
                    raise ValueError("실행의 원본과 삭제 대기본이 동시에 있습니다")
                os.rename(source, destination)
                sync_directory(source.parent)
                sync_directory(stage)
            if destination.exists() or destination.is_symlink():
                self._run_files(destination)
                self._check_owner(destination, run, partial=True)
            if source.parent.exists() and not any(source.parent.iterdir()):
                source.parent.rmdir()
                sync_directory(self.queue.root)
        snapshot = self.queue.directory / (plan["job_id"] + ".json")
        destination = stage / "snapshot.json"
        if snapshot.exists() or snapshot.is_symlink():
            private_file(snapshot)
            if destination.exists() or destination.is_symlink():
                raise ValueError("고정 입력의 삭제 대기본이 중복됐습니다")
            os.rename(snapshot, destination)
            sync_directory(self.queue.directory)
            sync_directory(stage)
        return stage

    def _delete(self, plan: dict) -> None:
        self._validate_plan(plan)
        self._validate_indexes(plan)
        stage = self._stage(plan)
        expected = {run["incident_id"] + "--" + run["run_id"] for run in plan["runs"]}
        # 일부 삭제 후 중단돼도 남은 파일만 같은 계획으로 지운다.
        for item in stage.iterdir():
            if item.name == "snapshot.json":
                private_file(item)
            elif item.name in expected:
                self._run_files(item)
            else:
                raise ValueError("삭제 대기 폴더에 다른 소유자의 자료가 있습니다")
        for item in stage.iterdir():
            if item.name == "snapshot.json":
                unlink_private(item)
            else:
                for file in item.iterdir():
                    unlink_private(file)
                item.rmdir()
        sync_directory(stage)
        stage.rmdir()
        sync_directory(self.staging)
        for request in plan["requests"]:
            index = self.engine._index_path(request)
            if index.exists():
                unlink_private(index)
        sync_directory(self.queue.root / "requests")

    def run(self, *, apply: bool = False, job_id: str | None = None) -> dict:
        with self.queue.worker_lock():
            return self._run_locked(apply=apply, job_id=job_id)

    def _run_locked(self, *, apply: bool = False, job_id: str | None = None,
                    batch_limit: int | None = None, stopped=lambda: False,
                    progress=lambda: None, cursor: tuple[float, str] | None = None) -> dict:
        """worker 잠금 소유자만 호출한다. 예약 정리는 만료 후보를 소량 처리한다."""
        if job_id is not None:
            self.queue._id(job_id)
        if batch_limit is not None and (type(batch_limit) is not int or not 1 <= batch_limit <= 10 or job_id is not None):
            raise ValueError("예약 정리 묶음 설정이 유효하지 않습니다")
        result = []
        truncated = False
        more = False
        last_scanned = None
        started = time.monotonic()
        # worker의 읽기·게시, 저장소 재조정, 보관 정리가 동시에 실행되지 않게 한다.
        # 예약 정리는 다른 운영자가 엔진을 쓰고 있으면 기다리지 않고 다음 회차로 미룬다.
        with self.engine._locked(reconcile=False, blocking=batch_limit is None):
            with self.queue.transaction() as db:
                if batch_limit is None:
                    rows = [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at,job_id")]
                    purges = {row["job_id"]: dict(row) for row in db.execute("SELECT * FROM purges")}
                else:
                    base = "SELECT * FROM jobs WHERE created_at<=? AND lease_until IS NULL AND state IN ('completed','partial','failed','cancelled','needs_review')"
                    params = (self.queue.now() - RETENTION_SECONDS,)
                    tail = " ORDER BY created_at,job_id LIMIT ?"
                    if cursor is not None:
                        self.queue._id(cursor[1])
                        rows = [dict(row) for row in db.execute(base + " AND (created_at,job_id)>(?,?)" + tail,
                                                               (*params, *cursor, batch_limit + 1))]
                    else:
                        rows = []
                    if not rows:
                        rows = [dict(row) for row in db.execute(base + tail, (*params, batch_limit + 1))]
                    more = len(rows) > batch_limit
                    rows = rows[:batch_limit]
                    purges = {}
                    for row in rows:
                        saved = db.execute("SELECT * FROM purges WHERE job_id=?", (row["job_id"],)).fetchone()
                        if saved is not None:
                            purges[row["job_id"]] = dict(saved)
            if job_id is not None:
                rows = [row for row in rows if row["job_id"] == job_id]
                if not rows and job_id not in purges:
                    raise ValueError("작업을 찾을 수 없습니다")
            elif batch_limit is None:
                truncated = len(rows) > 1000
                rows = rows[:1000]
            for row in rows:
                if batch_limit is not None and (stopped() or time.monotonic() - started >= 20):
                    more = True
                    break
                progress()
                job = row["job_id"]
                purge = purges.get(job)
                deadline = row["created_at"] + RETENTION_SECONDS
                entry = {"job_id": job, "state": row["state"], "created_at": utc(row["created_at"]),
                         "retention_deadline": utc(deadline), "action": "keep"}
                if purge is None and (row["state"] not in TERMINAL or row["lease_until"] is not None):
                    entry.update(action="blocked", reason="unfinished_job")
                elif purge is None and self.queue.now() < deadline:
                    entry["reason"] = "within_retention"
                else:
                    try:
                        with self.queue.transaction() as db:
                            if purge:
                                if len(purge["plan"]) > 16384:
                                    raise ValueError("삭제 계획 크기가 초과됐습니다")
                                saved = json.loads(purge["plan"])
                                plan = saved["plan"]
                                if sha256_json(plan) != saved["hash"]:
                                    raise ValueError("삭제 계획 해시가 일치하지 않습니다")
                            else:
                                plan = self._plan(db, row)
                            self._validate_plan(plan)
                            if plan["job_id"] != job:
                                raise ValueError("삭제 계획 작업 ID가 다릅니다")
                            if apply and purge is None:
                                db.execute("INSERT INTO purges VALUES (?,?,?,?,NULL)",
                                           (job, row["created_at"], deadline,
                                            json.dumps({"plan": plan, "hash": sha256_json(plan)})))
                        entry.update(action="would_purge", linked_runs=len(plan["runs"]))
                        if apply:
                            self._delete(plan)
                            with self.queue.transaction() as db:
                                db.execute("DELETE FROM attempts WHERE job_id=?", (job,))
                                db.execute("DELETE FROM jobs WHERE job_id=?", (job,))
                                db.execute("UPDATE purges SET completed_at=? WHERE job_id=?", (self.queue.now(), job))
                            entry["action"] = "purged"
                            entry["state"] = "purged"
                    except (ValueError, OSError, KeyError, TypeError):
                        entry.update(action="blocked", reason="unsafe_or_inconsistent_store")
                result.append(entry)
                last_scanned = (row["created_at"], job)
            selected = {row["job_id"] for row in rows}
            for job, purge in purges.items():
                if job not in selected and (job_id is None or job == job_id) and purge["completed_at"] is not None:
                    if len(result) >= 1000:
                        truncated = True
                        break
                    result.append({"job_id": job, "state": "purged", "action": "already_purged",
                                   "retention_deadline": utc(purge["deadline"]), "purged_at": utc(purge["completed_at"])})
        output = {"mode": "apply" if apply else "preview", "retention_days": 7,
                  "truncated": truncated, "entries": result}
        if batch_limit is not None:
            output.update(more_candidates=more, last_scanned=last_scanned)
        return output
