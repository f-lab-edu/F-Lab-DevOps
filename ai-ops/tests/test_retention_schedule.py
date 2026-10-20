"""예약 정리의 안전 지점·영속 재개·묶음 순회·상태 지표를 검증한다."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from ai_ops.job_queue import JobQueue, QueueBusy, RETENTION_SECONDS
from ai_ops.retention_schedule import RetentionSchedule
from ai_ops.worker import Worker
from ai_ops.worker_runtime import WorkerTelemetry, check_worker
import test_queue_retention as retention_tests


class RetentionScheduleTest(unittest.TestCase):
    def setUp(self):
        self.fixture = retention_tests.QueueRetentionTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.queue = self.fixture.queue
        self.schedule = RetentionSchedule(self.queue)

    def tick(self, schedule=None):
        with self.queue.worker_lock():
            return (schedule or self.schedule).tick()

    def state(self):
        with self.queue.transaction() as db:
            return dict(db.execute('SELECT * FROM retention_schedule').fetchone())

    def test_first_start_catchup_and_next_run_survive_reopen(self):
        job = self.fixture.complete()
        self.assertEqual(self.tick(), {'status':'success','purged':0,'blocked':0})
        self.assertEqual(self.queue.show(job)['state'], 'partial')
        self.assertEqual(self.state()['next_at'], self.fixture.time + 60)
        restarted = RetentionSchedule(self.queue)
        self.fixture.time += 59
        self.assertIsNone(self.tick(restarted))
        self.assertEqual(self.state()['runs'], 1)
        self.fixture.expire()
        self.assertEqual(self.tick(restarted)['purged'], 1)
        self.assertEqual(self.queue.show(job)['state'], 'purged')

    def test_unfinished_and_fresh_jobs_remain_and_overdue_is_visible(self):
        old, _ = self.fixture.submit('waiting')
        self.fixture.expire()
        current, _ = self.fixture.submit('fresh')
        self.assertEqual(self.tick()['purged'], 0)
        self.assertEqual(self.queue.show(old)['state'], 'queued')
        self.assertEqual(self.queue.show(current)['state'], 'queued')
        self.assertEqual(self.schedule.metrics()['ai_ops_retention_overdue_jobs'], 1)

    def test_busy_engine_defers_without_deleting_or_waiting(self):
        job = self.fixture.complete()
        self.fixture.expire()
        with self.queue.engine._locked():
            self.assertEqual(self.tick()['status'], 'deferred')
        self.assertEqual(self.queue.show(job)['state'], 'partial')
        self.assertEqual(self.state()['last_failure'], 0)
        self.assertGreater(self.state()['next_at'], self.fixture.time)

    def test_small_batches_rotate_past_unsafe_job(self):
        jobs = []
        for index in range(12):
            job, _ = self.fixture.submit('cancel-' + str(index))
            self.queue.cancel(job)
            jobs.append(job)
        unsafe = sorted(jobs)[0]
        path = self.queue.directory / (unsafe + '.json')
        path.write_text('{}'); path.chmod(0o644)
        self.fixture.expire()
        first = self.tick()
        self.assertEqual(first, {'status':'partial','purged':9,'blocked':1})
        self.assertEqual(self.state()['next_at'], self.fixture.time + 5)
        self.fixture.time += 5
        self.assertEqual(self.tick()['purged'], 2)
        self.assertEqual(self.queue.show(unsafe)['state'], 'cancelled')
        for job in set(jobs) - {unsafe}:
            self.assertEqual(self.queue.show(job)['state'], 'purged')
        self.fixture.time += 60
        self.assertEqual(self.tick()['blocked'], 1)
        self.assertEqual(self.schedule.metrics()['ai_ops_retention_overdue_jobs'], 1)

    def test_stop_between_deletions_preserves_remaining_batch(self):
        stopped = threading.Event()
        schedule = RetentionSchedule(self.queue, stopped=stopped.is_set)
        jobs = []
        for index in range(2):
            job, _ = self.fixture.submit('stop-' + str(index));self.queue.cancel(job);jobs.append(job)
        self.fixture.expire()
        original = schedule.retention._delete
        def delete(plan):
            original(plan)
            stopped.set()
        with patch.object(schedule.retention, '_delete', side_effect=delete):
            self.assertEqual(self.tick(schedule)['purged'], 1)
        self.assertEqual(sum(self.queue.show(job)['state']=='purged' for job in jobs), 1)
        stopped.clear(); self.fixture.time += 5
        self.assertEqual(self.tick(schedule)['purged'], 1)

    def test_worker_maintenance_owns_lock_and_stops_before_claim(self):
        stopped = threading.Event()
        worker = Worker(self.queue, self.fixture.settings, stopped=stopped)
        def maintenance():
            with self.assertRaises(QueueBusy):
                with self.queue.worker_lock():
                    pass
            self.schedule.tick()
            stopped.set()
        with patch.object(worker, '_run_once', side_effect=AssertionError('정리 중 신규 claim 금지')):
            worker.serve(maintenance=maintenance)
        self.assertEqual(self.state()['status'], 'success')

    def test_metrics_fixed_38_samples_no_originals_and_readonly(self):
        self.fixture.submit()
        telemetry = WorkerTelemetry(self.queue)
        telemetry.retention = self.schedule
        with self.queue.worker_lock():
            telemetry.progress()
            self.schedule.tick()
            before = self.state()
            raw = telemetry.render().decode()
            self.assertEqual(self.state(), before)
        samples = [line for line in raw.splitlines() if line and not line.startswith('#')]
        self.assertEqual(len(samples), 38)
        for word in ('private-secret', 'request-1', 'job-', 'question', 'cursor_job'):
            self.assertNotIn(word, raw)
        self.assertIn('ai_ops_retention_enabled 1', raw)

    def test_real_process_crash_mid_delete_resumes_pending_plan(self):
        job = self.fixture.complete()
        self.fixture.expire()
        code = '''import os,sys
from pathlib import Path
from ai_ops.job_queue import JobQueue
from ai_ops.retention_schedule import RetentionSchedule
import ai_ops.retention as retention
q=JobQueue(Path(sys.argv[1]),clock=lambda:float(sys.argv[2]))
s=RetentionSchedule(q)
original=retention.unlink_private
count=0
def stop(path):
 global count
 original(path);count+=1
 if count==4:os._exit(31)
retention.unlink_private=stop
with q.worker_lock():s.tick()
'''
        result = subprocess.run([sys.executable,'-c',code,str(self.queue.root),str(self.fixture.time)],capture_output=True)
        self.assertEqual(result.returncode, 31, result.stderr)
        self.assertEqual(self.state()['status'], 'running')
        self.assertEqual(self.tick(RetentionSchedule(self.queue))['purged'], 1)
        self.assertEqual(self.queue.show(job)['state'], 'purged')
        with self.assertRaises(ValueError):
            self.fixture.submit()

    def test_crash_after_delete_before_schedule_commit_does_not_repeat_delete(self):
        job = self.fixture.complete()
        self.fixture.expire()
        code = '''import os,sys
from pathlib import Path
from ai_ops.job_queue import JobQueue
from ai_ops.retention_schedule import RetentionSchedule
q=JobQueue(Path(sys.argv[1]),clock=lambda:float(sys.argv[2]));s=RetentionSchedule(q)
original=s.retention._run_locked
def stop(**kw):
 original(**kw);os._exit(32)
s.retention._run_locked=stop
with q.worker_lock():s.tick()
'''
        result = subprocess.run([sys.executable,'-c',code,str(self.queue.root),str(self.fixture.time)],capture_output=True)
        self.assertEqual(result.returncode, 32, result.stderr)
        self.assertEqual(self.queue.show(job)['state'], 'purged')
        self.assertEqual(self.state()['status'], 'running')
        restarted = RetentionSchedule(self.queue)
        with patch.object(restarted.retention, '_delete', side_effect=AssertionError('완료된 자료를 다시 삭제했습니다')):
            self.assertEqual(self.tick(restarted), {'status':'success','purged':0,'blocked':0})

    def test_runtime_flag_automatically_purges_and_restart_keeps_due_time(self):
        old, _ = self.fixture.submit('old-runtime');self.queue.cancel(old)
        self.fixture.time = time.time()
        fresh, _ = self.fixture.submit('fresh-runtime');self.queue.cancel(fresh)
        config = Path(retention_tests.queue_tests.ROOT) / 'config'
        code = '''import sys
from pathlib import Path
from unittest.mock import patch
from ai_ops.b_contracts import load_b_settings
from ai_ops.worker_runtime import main
config=Path(sys.argv[2])
s=load_b_settings(config/'targets.practice.example.json',config/'b-runtime.example.json',config/'metric-queries.json')
with patch('ai_ops.worker_runtime.check_local'),patch('ai_ops.worker_runtime.load_b_settings',return_value=s):
 raise SystemExit(main(['--config',str(config),'--store',sys.argv[1],'--retention']))
'''
        previous_due = None
        for _ in range(2):
            with subprocess.Popen([sys.executable,'-c',code,str(self.queue.root),str(config)],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE) as child:
                try:
                    until = time.monotonic()+10
                    while time.monotonic()<until:
                        try:
                            if (check_worker(self.queue.root)['pid']==child.pid
                                    and self.queue.show(old)['state']=='purged'):
                                break
                        except (OSError,ValueError):
                            pass
                        time.sleep(0.05)
                    else:
                        self.fail('예약 정리 worker가 준비되지 않았습니다')
                    state = self.state()
                    self.assertEqual(state['runs'],1)
                    self.assertEqual(self.queue.show(fresh)['state'],'cancelled')
                    if previous_due is not None:
                        self.assertEqual(state['next_at'],previous_due)
                    previous_due = state['next_at']
                    child.terminate()
                    _, error = child.communicate(timeout=5)
                    self.assertEqual(child.returncode,0,error)
                finally:
                    if child.poll() is None:
                        child.kill();child.wait()


if __name__ == '__main__':
    unittest.main()
