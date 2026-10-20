"""영속 큐 한 건씩 읽어 수집·규칙 보고서·재시작 조정을 수행한다."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import stat
from pathlib import Path
import sqlite3
import threading

from ai_ops.b_contracts import BSettings
from ai_ops.collection import CapturedSnapshot, CollectionAudit
from ai_ops.contracts import canonical_json_bytes, parse_evidence_bundle_json, sha256_json
from ai_ops.data_policy import validate_snapshot
from ai_ops.incidents import _atomic_write, _read_json
from ai_ops.job_queue import JobQueue, LeaseLost
from ai_ops.live_collectors import collect_snapshot
from ai_ops.observability import observe


def snapshot_value(snapshot: CapturedSnapshot) -> dict:
    return {"bundle": snapshot.bundle.model_dump(mode="json"), "audit": snapshot.audit.model_dump(mode="json")}


class Worker:
    """외부 모델 없이 신뢰 설정에 고정한 수동 요청만 처리한다."""

    def __init__(self, queue: JobQueue, settings: BSettings, *, collector=collect_snapshot,
                 stopped: threading.Event | None = None):
        self.queue = queue
        self.settings = settings
        self.collector = collector
        self.stopped = stopped or threading.Event()

    def _payload(self, job: dict) -> dict:
        payload = json.loads(job["payload"])
        if not isinstance(payload, dict) or set(payload) != {"target", "question", "config_hash", "route", "operation", "mode"} or payload["mode"] != "rules":
            raise ValueError("큐 입력 구조가 유효하지 않습니다")
        if payload["config_hash"] != self.settings.config_hash:
            raise ValueError("config_changed")
        return payload

    def _snapshot_path(self, job: dict) -> Path:
        self.queue._id(job["job_id"])
        return self.queue.directory / (job["job_id"] + ".json")

    def _load_snapshot(self, job: dict) -> CapturedSnapshot:
        path = self._snapshot_path(job)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError("고정 입력 파일 권한이 안전하지 않습니다")
        value = _read_json(path, 8 * 1024 * 1024)
        if sha256_json(value) != job["snapshot_hash"] or set(value) != {"bundle", "audit"}:
            raise ValueError("큐 입력 해시가 일치하지 않습니다")
        snapshot = CapturedSnapshot(parse_evidence_bundle_json(canonical_json_bytes(value["bundle"])),
                                    CollectionAudit.model_validate(value["audit"]))
        snapshot = validate_snapshot(snapshot, self.settings)
        payload = self._payload(job)
        binding = next((b for b in self.settings.bindings.bindings if b.alias == payload["target"]), None)
        if binding is None or snapshot.bundle.target != binding.target:
            raise ValueError("수집 대상과 큐 대상이 일치하지 않습니다")
        return snapshot

    def _check_record(self, job: dict, record):
        payload = self._payload(job)
        snapshot = self._load_snapshot(job)
        if (record.request is None or record.bundle is None or record.report is None
                or record.metadata["request_id"] != self.queue.request_id(job)
                or record.request.question != payload["question"]
                or record.bundle.bundle_hash != snapshot.bundle.bundle_hash
                or record.collection_audit != snapshot.audit
                or record.report.execution.mode != "rules"):
            raise ValueError("완료 보고서와 큐 입력이 일치하지 않습니다")
        path = self.queue.root / record.incident_id / record.run_id / "report.md"
        if path.is_symlink() or path.read_text() != record.markdown:
            raise ValueError("저장된 보고서 파일이 일치하지 않습니다")

    def recover(self):
        """파일 완료·큐 미반영은 기존 결과로 마무리하고 불명확한 자료는 보류한다."""
        for job in self.queue.expired():
            try:
                self._payload(job)
                record = self.queue.engine.find_request(self.queue.request_id(job))
                if record and record.metadata["status"] in ("completed", "partial"):
                    self._check_record(job, record)
                    with self.queue.transaction() as db:
                        current = db.execute("SELECT state,generation,lease_until FROM jobs WHERE job_id=?", (job["job_id"],)).fetchone()
                        if current["state"] not in ("collecting", "analyzing") or current["generation"] != job["generation"]:
                            continue
                        if current["lease_until"] > self.queue.now():
                            raise LeaseLost("아직 유효한 시도입니다")
                        self.queue.finish(db, job, record)
                    continue
                self.queue.fail(job, "interrupted", recovery=True)
            except (ValueError, OSError, KeyError, TypeError):
                self.queue.fail(job, "integrity_error", recovery=True, review=True)

    @contextmanager
    def _heartbeat(self, job: dict):
        done = threading.Event()
        lost = threading.Event()
        def renew():
            while not done.wait(10):
                try:
                    self.queue.heartbeat(job)
                except (ValueError, OSError, sqlite3.Error):
                    lost.set()
                    return
        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield lost
        finally:
            done.set()
            thread.join(timeout=6)

    def run_once(self) -> dict | None:
        with self.queue.worker_lock():
            return self._run_once()

    def _run_once(self) -> dict | None:
        # 호출자는 저장소 전체 worker 잠금을 소유해야 한다.
        self.recover()
        if self.stopped.is_set():
            return None
        job = self.queue.claim()
        if job is None:
            return None
        phase = "validation"
        try:
            with self._heartbeat(job) as lost:
                payload = self._payload(job)
                binding = next((b for b in self.settings.bindings.bindings if b.alias == payload["target"]), None)
                if binding is None:
                    raise ValueError("config_changed")
                if job["snapshot_hash"]:
                    snapshot = self._load_snapshot(job)
                else:
                    phase = "collection"
                    with observe(self.queue.root, "collection") as observation:
                        snapshot = self.collector(self.settings, binding.target,
                                                  route=payload["route"], operation=payload["operation"])
                        snapshot = validate_snapshot(snapshot, self.settings)
                        if snapshot.bundle.target != binding.target:
                            raise ValueError("수집 대상과 큐 대상이 일치하지 않습니다")
                        observation["result"] = {"complete": "success", "partial": "partial", "unavailable": "failure"}[snapshot.audit.collection_status]
                    value = snapshot_value(snapshot)
                    # 취소·만료 시 수집한 임시 결과를 큐 입력으로 게시하지 않는다.
                    with self.queue.transaction() as db:
                        self.queue.assert_owner(db, job)
                        _atomic_write(self._snapshot_path(job), canonical_json_bytes(value))
                    job["snapshot_hash"] = sha256_json(value)
                if lost.is_set():
                    raise LeaseLost("lease 갱신에 실패했습니다")
                self.queue.checkpoint(job, job["snapshot_hash"])
                if self.stopped.is_set():
                    self.queue.fail(job, "operator_stopped")
                    return self.queue.show(job["job_id"])
                phase = "report"
                # 규칙 보고서를 저장하는 짧은 구간은 큐 쓰기 잠금 안에서 게시한다.
                # 취소/다른 generation은 이 구간 전후에만 상태를 바꿀 수 있다.
                with self.queue.transaction() as db:
                    self.queue.assert_owner(db, job)
                    with observe(self.queue.root, "report") as observation:
                        record = self.queue.engine.investigate_snapshot(snapshot=snapshot, settings=self.settings,
                            question=payload["question"], request_id=self.queue.request_id(job))
                        observation["result"] = "success" if record.metadata["status"] == "completed" else "partial"
                    self._check_record(job, record)
                    self.queue.assert_owner(db, job)
                    self.queue.finish(db, job, record)
        except LeaseLost:
            # 소유권을 잃은 시도는 큐 상태·완료 참조를 덮어쓰지 않는다.
            pass
        except KeyboardInterrupt:
            self.stopped.set()
            try:
                self.queue.fail(job, "operator_stopped")
            except LeaseLost:
                pass
        except Exception as error:
            # 완료 파일을 이미 쓴 뒤 큐 commit만 실패했다면 재시도하지 않는다.
            # lease 만료 후 recover가 같은 파일을 검증해 상태만 복구한다.
            if phase == "report":
                try:
                    saved = self.queue.engine.find_request(self.queue.request_id(job))
                    if saved and saved.metadata["status"] in ("completed", "partial"):
                        self._check_record(job, saved)
                        return self.queue.show(job["job_id"])
                except (ValueError, OSError, KeyError, TypeError):
                    self.queue.fail(job, "integrity_error", review=True)
                    return self.queue.show(job["job_id"])
            reason = "config_changed" if str(error) == "config_changed" else "integrity_error" if phase == "validation" or isinstance(error, ValueError) and phase == "report" else "collection_error" if phase == "collection" else "analysis_error"
            try:
                self.queue.fail(job, reason, review=reason in ("config_changed", "integrity_error"))
            except LeaseLost:
                pass
        return self.queue.show(job["job_id"])

    def serve(self, *, progress=lambda: None, maintenance=lambda: None):
        """SIGTERM 요청 시 새 작업을 시작하지 않고 현재 수집의 안전 지점에서 멈춘다."""
        # 유휴 시간에도 잠금을 유지해 두 worker와 보관 정리가 겹치지 않는다.
        with self.queue.worker_lock():
            while not self.stopped.is_set():
                progress()
                # 별도 작업자가 아닌 잠금 소유자가 새 claim 전에만 정리한다.
                maintenance()
                progress()
                if self.stopped.is_set():
                    break
                result = self._run_once()
                progress()
                if result is None or result["state"] in ("queued", "needs_review", "failed"):
                    self.stopped.wait(1)
