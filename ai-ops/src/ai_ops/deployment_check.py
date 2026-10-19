"""B-03 진단 Pod의 로컬 설정·런북·저장소 검사. 네트워크·모델은 호출하지 않는다."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import time
import os
import signal
import sqlite3
import ssl
import tempfile
import threading
from pathlib import Path
from ai_ops.path_policy import cli_paths

from ai_ops.b_contracts import load_b_settings
from ai_ops.replay_tools import _runbook
from ai_ops.observability import Telemetry, log_event, metrics_server


def check_local(config: Path, store: Path) -> None:
    """키 내용을 출력하지 않고 설정·CA·토큰 가독성과 SQLite WAL 저장을 확인한다."""
    settings = load_b_settings(config / "bindings.json", config / "runtime.json", config / "metric-queries.json")
    for connection in settings.runtime.connections.values():
        if connection.base_url is None:
            raise ValueError("unconfigured connection")
        ssl.create_default_context(cafile=connection.ca_file)
        if connection.token_file:
            if not connection.base_url.startswith("https://"):
                raise ValueError("credentials require TLS")
            # projected volume의 심볼릭 링크를 허용하되 값은 로그에 남기지 않는다.
            with Path(connection.token_file).open("rb") as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise ValueError("credential reference exceeds limit")
            token = raw.decode("ascii").rstrip("\r\n")
            if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError("invalid credential reference")
    for name in ("cache", "database", "deployment"):
        _runbook(name)
    if store.is_symlink():
        raise ValueError("store cannot be a symlink")
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(store, 0o700)
    # 임시 DB만 쓰며 기존 사건·예산·큐 기록은 열거나 변경하지 않는다.
    with tempfile.TemporaryDirectory(prefix=".deployment-check-", dir=store) as directory:
        path = Path(directory) / "check.sqlite3"
        with sqlite3.connect(path) as db:
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise ValueError("WAL is not supported")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE check_record (value INTEGER NOT NULL)")
            db.execute("INSERT INTO check_record VALUES (1)")
            db.commit()
        with sqlite3.connect(path) as db:
            if db.execute("SELECT value FROM check_record").fetchone() != (1,):
                raise ValueError("storage round trip failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="네트워크 호출 없는 배포 로컬 검사")
    parser.add_argument("--config", type=Path, default=Path("/etc/ai-ops"))
    parser.add_argument("--store", type=Path, default=Path("/data/incidents"))
    parser.add_argument("--serve", action="store_true", help="진단 상태만 주기적으로 확인하며 대기한다")
    parser.add_argument("--metrics", action="store_true", help="진단 대기 중 9091 포트에서 고정 지표만 제공한다")
    args = cli_paths(parser, parser.parse_args(argv), {'config': 'directory', 'store': 'store'})
    os.umask(0o077)
    stopped = threading.Event()
    if args.serve:
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
    if args.metrics and not args.serve:
        parser.error("--metrics는 --serve와 함께 사용해야 합니다")
    telemetry = None
    try:
        if args.metrics:
            telemetry = Telemetry(args.store)
        with metrics_server(telemetry) if telemetry else nullcontext():
            if args.serve:
                log_event("process_started")
            while True:
                started = time.monotonic()
                try:
                    check_local(args.config, args.store)
                    if telemetry:
                        telemetry.record("diagnostic", "success", time.monotonic() - started)
                except Exception:
                    # CA·파일 경로·인증값·예외 원문을 출력하지 않는다.
                    if telemetry:
                        try:
                            telemetry.record("diagnostic", "failure", time.monotonic() - started)
                        except Exception:
                            log_event("telemetry_failed", result="failure")
                    log_event("operation_finished", result="failure", duration=time.monotonic() - started)
                    print("배포 로컬 검사 실패: 설정·런북·저장소를 확인하세요.", flush=True)
                    return 1
                if not args.serve:
                    print("배포 로컬 검사 통과: 실제 API·권한·통신·PVC 재연결은 미검증입니다.")
                    return 0
                log_event("operation_finished", duration=time.monotonic() - started)
                if stopped.wait(30):
                    return 0
    except (OSError, ValueError, sqlite3.Error):
        log_event("telemetry_failed", result="failure")
        return 1
    finally:
        if args.serve:
            log_event("process_stopped")


if __name__ == "__main__":
    raise SystemExit(main())
