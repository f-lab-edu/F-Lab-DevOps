"""읽기 전용 큐 worker의 배포 실행·진행 검사·고정 지표를 제공한다."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import stat
import threading
import time

from ai_ops.b_contracts import load_b_settings
from ai_ops.contracts import canonical_json_bytes
from ai_ops.deployment_check import check_local
from ai_ops.incidents import _atomic_write
from ai_ops.job_queue import JobQueue, STATES
from ai_ops.normal_ci_contract import NormalCIBinding
from ai_ops.normal_ci_inbox import NormalCIInbox
from ai_ops.observability import Telemetry, log_event, metrics_server
from ai_ops.path_policy import cli_paths, permitted_path
from ai_ops.worker import Worker
from ai_ops.retention_schedule import RetentionSchedule

HEALTH_NAME = "worker-health.json"
MAX_PROGRESS_AGE = 120


def _private_file(path: Path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise ValueError("worker 상태 파일 권한이 안전하지 않습니다")


def check_worker(store: Path) -> dict:
    """자료·설정을 수정하지 않고 최근 루프 진행과 단일 worker 잠금을 확인한다."""
    root = permitted_path(store, "store")
    directory = root / "queue"
    for path in (root, directory):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("worker 상태 폴더가 안전하지 않습니다")
    path = directory / HEALTH_NAME
    _private_file(path)
    with path.open("rb") as stream:
        raw = stream.read(1025)
    if len(raw) > 1024:
        raise ValueError("worker 상태 크기가 유효하지 않습니다")
    value = json.loads(raw)
    if set(value) != {"pid", "monotonic", "timestamp"} or type(value["pid"]) is not int or value["pid"] < 1:
        raise ValueError("worker 상태 구조가 유효하지 않습니다")
    for key in ("monotonic", "timestamp"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] < 0:
            raise ValueError("worker 상태 시각이 유효하지 않습니다")
    if not 0 <= time.monotonic() - value["monotonic"] <= MAX_PROGRESS_AGE:
        raise ValueError("worker의 최근 진행을 확인할 수 없습니다")
    os.kill(value["pid"], 0)
    lock = directory / "worker.lock"
    _private_file(lock)
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return value
        raise ValueError("실행 중인 worker 잠금이 없습니다")
    finally:
        os.close(fd)


class WorkerTelemetry(Telemetry):
    """식별자·질문·본문을 제외한 고정 상태 수와 현재 루프 진행만 노출한다."""

    def __init__(self, queue: JobQueue):
        super().__init__(queue.root)
        self.queue = queue
        self.last_write = None
        self.retention = None

    def progress(self):
        # 별도 생존 스레드 대신 실제 처리 루프가 돌아왔을 때만 갱신한다.
        now = time.monotonic()
        if self.last_write is not None and now - self.last_write < 10:
            return
        path = self.queue.directory / HEALTH_NAME
        if path.exists() or path.is_symlink():
            _private_file(path)
        _atomic_write(path, canonical_json_bytes({"pid": os.getpid(), "monotonic": now, "timestamp": time.time()}))
        self.last_write = now

    def render(self) -> bytes:
        base = super().render().decode("ascii")
        self.queue._safe_files()
        # 쓰기 트랜잭션·초기화 없이 WAL 스냅샷만 읽는다.
        db = sqlite3.connect(self.queue.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            db.execute("BEGIN")
            rows = db.execute("SELECT state,count(*),min(created_at) FROM jobs GROUP BY state").fetchall()
            expired = db.execute("SELECT count(*) FROM jobs WHERE state IN ('collecting','analyzing') AND lease_until<=?", (time.time(),)).fetchone()[0]
            attempts = db.execute("SELECT count(*) FROM attempts").fetchone()[0]
        finally:
            db.close()
        if any(row[0] not in STATES for row in rows):
            raise ValueError("알 수 없는 큐 상태입니다")
        counts = {row[0]: row[1] for row in rows}
        oldest = next((max(0, time.time() - row[2]) for row in rows if row[0] == "queued"), 0)
        lines = ["# HELP ai_ops_queue_jobs Number of retained jobs by fixed state.", "# TYPE ai_ops_queue_jobs gauge"]
        lines.extend(f'ai_ops_queue_jobs{{state="{state}"}} {counts.get(state, 0)}' for state in STATES)
        progress = check_worker(self.queue.root)
        for name, value in (("queue_oldest_waiting_age_seconds", oldest), ("queue_expired_leases", expired),
                            ("queue_retained_attempts", attempts), ("worker_last_progress_timestamp_seconds", progress["timestamp"])):
            lines.extend((f"# TYPE ai_ops_{name} gauge", f"ai_ops_{name} {value}"))
        if self.retention is not None:
            for name, value in self.retention.metrics().items():
                lines.extend((f"# TYPE {name} gauge", f"{name} {value}"))
        return (base + "\n".join(lines) + "\n").encode("ascii")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="수동 접수 큐의 읽기 전용 수집·규칙 보고서 worker")
    parser.add_argument("--config", type=Path, default=Path("/etc/ai-ops"))
    parser.add_argument("--store", type=Path, default=Path("/data/incidents"))
    parser.add_argument("--probe", action="store_true", help="큐 내용을 노출하지 않고 최근 worker 진행만 확인한다")
    parser.add_argument("--metrics", action="store_true", help="제한된 9091 포트에 고정 상태 지표를 제공한다")
    parser.add_argument("--retention", action="store_true", help="작업 사이에 7일 만료 자료를 예약 정리한다")
    args = cli_paths(parser, parser.parse_args(argv), {"config": "directory", "store": "store"})
    if args.probe and (args.metrics or args.retention):
        parser.error("상태 검사에서는 지표 서버·예약 정리를 실행할 수 없습니다")
    os.umask(0o077)
    stopped = threading.Event()
    telemetry = None
    result = "success"
    try:
        if args.probe:
            check_worker(args.store)
            return 0
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        check_local(args.config, args.store)
        settings = load_b_settings(args.config / "bindings.json", args.config / "runtime.json", args.config / "metric-queries.json")
        queue = JobQueue(args.store)
        normal_binding_path = args.config / "normal-ci-binding.json"
        normal_inbox = (NormalCIInbox(queue, NormalCIBinding.model_validate_json(
            normal_binding_path.read_bytes())) if normal_binding_path.is_file() else None)
        telemetry = WorkerTelemetry(queue)
        if args.retention:
            telemetry.retention = RetentionSchedule(queue, stopped=stopped.is_set, progress=telemetry.progress)
        model_bridge = None
        bridge_enabled = os.environ.get("AI_MODEL_BRIDGE_ENABLED", "false")
        if bridge_enabled not in {"false", "true"}:
            raise ValueError("모델 연결 설정이 유효하지 않습니다")
        if bridge_enabled == "true":
            from ai_ops.continuous_ci_bridge import ContinuousCIBridge
            from ai_ops.model_gateway_client import ModelGatewayClient
            model_bridge = ContinuousCIBridge(
                ModelGatewayClient(), os.environ["AI_MODEL_CAMPAIGN_ID"],
                cluster_fingerprint=os.environ["AI_MODEL_CLUSTER_FINGERPRINT"],
                exporter_sha256=os.environ["AI_MODEL_EXPORTER_SHA256"],
                policy=os.environ.get("AI_MODEL_POLICY", "captured-practice-projection-v1"))
        worker = Worker(queue, settings, stopped=stopped, model_bridge=model_bridge)
        log_event("process_started")
        with metrics_server(telemetry) if args.metrics else nullcontext():
            def maintenance():
                if telemetry.retention:
                    telemetry.retention.tick()
                if normal_inbox:
                    normal_inbox.prune()
                    normal_inbox.process_one()
            worker.serve(progress=telemetry.progress, maintenance=maintenance)
        return 0
    except Exception:
        # 경로·질문·예외 원문은 상태 로그와 지표에 남기지 않는다.
        result = "failure"
        if args.probe or telemetry is None:
            log_event("process_stopped", result=result)
        return 1
    finally:
        if telemetry is not None:
            # 종료 후에는 잠금이 해제되어 상태 검사가 실패한다. 다른 worker가
            # 이미 새 상태를 썼을 수 있으므로 여기서 공유 상태 파일을 지우지 않는다.
            log_event("process_stopped", result=result)


if __name__ == "__main__":
    raise SystemExit(main())
