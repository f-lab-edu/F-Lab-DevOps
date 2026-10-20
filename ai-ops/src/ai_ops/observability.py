"""본문을 남기지 않는 고정 상태 로그와 PVC 기반 누적 지표를 제공한다."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import sys
import threading
import time

from ai_ops.path_policy import permitted_path

STAGES = ("diagnostic", "collection", "report")
RESULTS = ("success", "partial", "failure")
EVENTS = ("operation_finished", "process_started", "process_stopped", "telemetry_failed", "retention_finished", "retention_deferred", "alert_received", "alert_rejected")


def log_event(event: str, *, stage: str = "diagnostic", result: str = "success", duration: float = 0) -> None:
    """자유 텍스트·질문·식별자·예외 원문을 받지 않고 UTC 구조화 로그만 쓴다."""
    if event not in EVENTS or stage not in (*STAGES, "retention", "alert") or result not in RESULTS:
        raise ValueError("관측 코드가 허용 목록과 다릅니다")
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("소요 시간이 유효하지 않습니다")
    print(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                      "event": event, "stage": stage, "result": result,
                      "duration_seconds": round(duration, 6)}, separators=(",", ":")), file=sys.stderr, flush=True)


class Telemetry:
    """작업 호출을 아홉 개 고정 행으로 집계한다. 재사용 요청도 호출 횟수에 포함한다."""

    def __init__(self, store: Path):
        self.root = permitted_path(store, "store")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.stat().st_uid != os.geteuid() or self.root.stat().st_mode & 0o077:
            raise ValueError("관측 저장소는 현재 사용자 전용이어야 합니다")
        self.path = self.root / ".telemetry.sqlite3"
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        self._validate_files()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations (stage TEXT NOT NULL CHECK(stage IN ('diagnostic','collection','report')), result TEXT NOT NULL CHECK(result IN ('success','partial','failure')), count INTEGER NOT NULL CHECK(count>=0), seconds REAL NOT NULL CHECK(seconds>=0), last_at REAL NOT NULL CHECK(last_at>=0), PRIMARY KEY(stage,result))")
            db.executemany("INSERT OR IGNORE INTO operations VALUES (?,?,0,0,0)",
                           ((stage, result) for stage in STAGES for result in RESULTS))

    def _validate_files(self) -> None:
        # 같은 UID만 쓸 수 있는 폴더에서도 SQLite 부속 파일의 링크는 거부한다.
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = Path(str(self.path) + suffix)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise ValueError("관측 파일 권한 또는 링크가 유효하지 않습니다")

    @contextmanager
    def _connect(self, *, readonly: bool = False):
        self._validate_files()
        db = sqlite3.connect(self.path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, stage: str, result: str, duration: float) -> None:
        if stage not in STAGES or result not in RESULTS or not math.isfinite(duration) or duration < 0:
            raise ValueError("관측 작업 값이 유효하지 않습니다")
        with self._connect() as db:
            db.execute("UPDATE operations SET count=count+1, seconds=seconds+?, last_at=? WHERE stage=? AND result=?",
                       (duration, time.time(), stage, result))

    def render(self) -> bytes:
        with self._connect(readonly=True) as db:
            rows = db.execute("SELECT stage,result,count,seconds,last_at FROM operations ORDER BY stage,result").fetchall()
        if {(r[0], r[1]) for r in rows} != {(s, r) for s in STAGES for r in RESULTS}:
            raise ValueError("관측 집계 구조가 유효하지 않습니다")
        lines = ["# HELP ai_ops_operation_duration_seconds Completed operation calls and total duration.",
                 "# TYPE ai_ops_operation_duration_seconds summary"]
        for stage, result, count, seconds, _ in rows:
            labels = f'stage="{stage}",result="{result}"'
            lines.extend((f"ai_ops_operation_duration_seconds_count{{{labels}}} {count}",
                          f"ai_ops_operation_duration_seconds_sum{{{labels}}} {seconds}"))
        last_success = next(r[4] for r in rows if r[:2] == ("diagnostic", "success"))
        lines.extend(("# HELP ai_ops_diagnostic_last_success_timestamp_seconds Last local diagnostic success in Unix seconds.",
                      "# TYPE ai_ops_diagnostic_last_success_timestamp_seconds gauge",
                      f"ai_ops_diagnostic_last_success_timestamp_seconds {last_success}"))
        return ("\n".join(lines) + "\n").encode("ascii")


@contextmanager
def observe(store: Path, stage: str):
    """원래 작업의 성공·실패를 바꾸지 않으며 관측 실패는 고정 코드로 알린다."""
    started = time.monotonic()
    state = {"result": "failure"}
    try:
        yield state
    finally:
        duration = time.monotonic() - started
        log_event("operation_finished", stage=stage, result=state["result"], duration=duration)
        try:
            Telemetry(store).record(stage, state["result"], duration)
        except (OSError, ValueError, sqlite3.Error):
            log_event("telemetry_failed", stage=stage, result="failure")


class MetricsServer(HTTPServer):
    """동시 실행·관리 API 없이 크기가 고정된 읽기 전용 메트릭만 노출한다."""

    def __init__(self, telemetry: Telemetry, address=("0.0.0.0", 9091)):
        self.telemetry = telemetry
        super().__init__(address, MetricsHandler)

    def handle_error(self, *_):
        # 연결 종료 등 HTTP 오류도 예외 원문 없이 알린다.
        log_event("telemetry_failed", result="failure")

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address


class MetricsHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        # 요청 경로·헤더·클라이언트 주소는 로그에 남기지 않는다.
        pass

    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        try:
            payload = self.server.telemetry.render()
        except (OSError, ValueError, sqlite3.Error):
            log_event("telemetry_failed", result="failure")
            self.send_error(503)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def metrics_server(telemetry: Telemetry):
    server = MetricsServer(telemetry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
