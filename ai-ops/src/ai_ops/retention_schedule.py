"""worker 안전 지점에서만 실행하는 영속 보관 정리 예약을 관리한다."""

from __future__ import annotations

import math
import sqlite3
import time

from ai_ops.job_queue import JobQueue, RETENTION_SECONDS
from ai_ops.observability import log_event
from ai_ops.retention import QueueRetention

INTERVAL_SECONDS = 60
BATCH_LIMIT = 10


class RetentionSchedule:
    """삭제 계획은 기존 장부에 두고 다음 실행·결과·순회 위치만 저장한다."""

    def __init__(self, queue: JobQueue, *, stopped=lambda: False, progress=lambda: None):
        self.queue = queue
        self.retention = QueueRetention(queue)
        self.stopped = stopped
        self.progress = progress
        with queue.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS retention_schedule (id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL, last_started REAL NOT NULL, last_finished REAL NOT NULL, last_success REAL NOT NULL, last_failure REAL NOT NULL, purged INTEGER NOT NULL, blocked INTEGER NOT NULL, runs INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('idle','running','success','partial','failure','deferred')), cursor_created REAL, cursor_job TEXT)")
            db.execute("INSERT OR IGNORE INTO retention_schedule VALUES (1,0,0,0,0,0,0,0,0,'idle',NULL,NULL)")

    def tick(self):
        """호출자는 worker 잠금을 소유한다. 작업 사이에서만 호출한다."""
        if self.stopped():
            return None
        now = self.queue.now()
        with self.queue.transaction() as db:
            state = dict(db.execute("SELECT * FROM retention_schedule WHERE id=1").fetchone())
            if not math.isfinite(state['next_at']) or state['next_at'] < 0:
                raise ValueError("예약 시각이 유효하지 않습니다")
            if now < state['next_at']:
                return None
            # 완료를 기록하기 전 중단되면 due 상태를 유지해 다음 worker가 재개한다.
            db.execute("UPDATE retention_schedule SET status='running',last_started=? WHERE id=1", (now,))
        started = time.monotonic()
        cursor = None if state['cursor_job'] is None else (state['cursor_created'], state['cursor_job'])
        result = None
        status = 'failure'
        purged = blocked = 0
        delay = INTERVAL_SECONDS
        try:
            result = self.retention._run_locked(apply=True, batch_limit=BATCH_LIMIT,
                stopped=self.stopped, progress=self.progress, cursor=cursor)
            purged = sum(entry['action'] == 'purged' for entry in result['entries'])
            blocked = sum(entry['action'] == 'blocked' for entry in result['entries'])
            status = 'partial' if blocked else 'success'
            if result['more_candidates']:
                delay = 5
        except BlockingIOError:
            status = 'deferred'
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            # 본문·경로·원문 오류를 기록하지 않는다. 기존 삭제 계획을 다음 회차에 재개한다.
            status = 'failure'
        finished = self.queue.now()
        last_cursor = result['last_scanned'] if result else None
        with self.queue.transaction() as db:
            db.execute("UPDATE retention_schedule SET next_at=?,last_finished=?,last_success=?,last_failure=?,purged=?,blocked=?,runs=runs+1,status=?,cursor_created=?,cursor_job=? WHERE id=1",
                (finished + delay, finished, finished if status == 'success' else state['last_success'],
                 finished if status in ('failure','partial') else state['last_failure'], purged, blocked, status,
                 last_cursor[0] if last_cursor else state['cursor_created'],
                 last_cursor[1] if last_cursor else state['cursor_job']))
        log_event('retention_deferred' if status == 'deferred' else 'retention_finished', stage='retention',
                  result='success' if status == 'success' else 'partial' if status in ('partial','deferred') else 'failure',
                  duration=time.monotonic() - started)
        return {'status': status, 'purged': purged, 'blocked': blocked}

    def metrics(self) -> dict:
        """지표 조회는 예약을 시작하거나 DB를 수정하지 않는다."""
        self.queue._safe_files()
        db = sqlite3.connect(self.queue.path.as_uri()+'?mode=ro', uri=True, timeout=2)
        try:
            db.execute('BEGIN')
            state = db.execute('SELECT next_at,last_success,last_failure,purged,blocked FROM retention_schedule WHERE id=1').fetchone()
            if state is None:
                raise ValueError('예약 장부가 없습니다')
            overdue = db.execute('SELECT count(*) FROM jobs WHERE created_at<=?', (self.queue.now()-RETENTION_SECONDS,)).fetchone()[0]
        finally:
            db.close()
        names = ('next_run_timestamp_seconds','last_success_timestamp_seconds','last_failure_timestamp_seconds',
                 'last_run_purged_jobs','last_run_blocked_jobs')
        values = dict(zip(names, state))
        values.update(enabled=1, overdue_jobs=overdue)
        if any(type(value) not in (int,float) or not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError('예약 지표가 유효하지 않습니다')
        return {'ai_ops_retention_'+key: value for key,value in values.items()}
