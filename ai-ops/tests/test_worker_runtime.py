"""worker 배포 루프의 관측·단일 실행·진행 검사·정상 종료 경계를 검증한다."""

import contextlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from ai_ops.job_queue import QueueBusy, STATES
from ai_ops.worker import Worker
from ai_ops.worker_runtime import HEALTH_NAME, WorkerTelemetry, check_worker, main
import test_job_queue as queue_fixtures

ROOT = queue_fixtures.ROOT


class WorkerRuntimeTest(unittest.TestCase):
    def setUp(self):
        # 기존 합성 수집 자료를 공유하되 기존 시험을 중복 실행하지 않는다.
        self.fixture = queue_fixtures.JobQueueTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.queue = self.fixture.queue
        self.settings = self.fixture.settings
        self.telemetry = WorkerTelemetry(self.queue)

    def test_fixed_metrics_readonly_and_private_progress(self):
        job, _ = self.fixture.submit()
        with self.queue.worker_lock():
            self.telemetry.progress()
            self.assertEqual(check_worker(self.queue.root)["pid"], os.getpid())
            before = self.queue.show(job)
            rendered = self.telemetry.render().decode()
            self.assertEqual(self.queue.show(job), before)
        for state in STATES:
            self.assertIn(f'ai_ops_queue_jobs{{state="{state}"}}', rendered)
        self.assertIn('ai_ops_queue_jobs{state="queued"} 1', rendered)
        self.assertLess(len([line for line in rendered.splitlines() if not line.startswith("#")]), 50)
        for value in (job, "private-secret-value", "request-1", "question", "현재 상태"):
            self.assertNotIn(value, rendered)
        self.assertEqual((self.queue.directory / HEALTH_NAME).stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            check_worker(self.queue.root)

    def test_stale_future_and_invalid_progress_fail_closed(self):
        with self.queue.worker_lock():
            self.telemetry.progress()
            path = self.queue.directory / HEALTH_NAME
            original = json.loads(path.read_text())
            for changed in ({**original, "monotonic": time.monotonic() - 121},
                            {**original, "monotonic": time.monotonic() + 60},
                            {**original, "pid": True}, {**original, "timestamp": float("nan")},
                            {**original, "question": "secret"}):
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    check_worker(self.queue.root)
            path.unlink(); path.symlink_to(self.queue.path)
            with self.assertRaises(ValueError):
                check_worker(self.queue.root)

    def test_metrics_are_gauges_including_expired_lease(self):
        self.fixture.submit()
        self.queue.claim()
        with self.queue.transaction() as db:
            db.execute("UPDATE jobs SET lease_until=0")
        with self.queue.worker_lock():
            self.telemetry.progress()
            raw = self.telemetry.render().decode()
        self.assertIn("ai_ops_queue_expired_leases 1", raw)
        self.assertIn("ai_ops_queue_retained_attempts 1", raw)
        self.assertIn("# TYPE ai_ops_queue_retained_attempts gauge", raw)

    def test_unexpected_start_failure_does_not_print_exception_body(self):
        output = io.StringIO()
        with patch("ai_ops.worker_runtime.check_local", side_effect=RuntimeError("private-upstream-value")), \
                contextlib.redirect_stderr(output):
            self.assertEqual(main(["--config", str(ROOT / "config"), "--store", str(self.queue.root)]), 1)
        self.assertNotIn("private-upstream-value", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_idle_serve_keeps_exclusive_lock_and_progress(self):
        stopped = threading.Event()
        worker = Worker(self.queue, self.settings, stopped=stopped)
        seen = threading.Event()
        def progress():
            self.telemetry.progress()
            seen.set()
        thread = threading.Thread(target=worker.serve, kwargs={"progress": progress})
        thread.start()
        try:
            self.assertTrue(seen.wait(5))
            with self.assertRaises(QueueBusy):
                with self.queue.worker_lock():
                    pass
            self.assertEqual(check_worker(self.queue.root)["pid"], os.getpid())
        finally:
            stopped.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        with self.assertRaises(ValueError):
            check_worker(self.queue.root)

    def test_stop_after_collection_keeps_snapshot_for_next_attempt(self):
        job, _ = self.fixture.submit()
        stopped = threading.Event()
        def collector(*args, **kwargs):
            stopped.set()
            return self.fixture.snapshot
        Worker(self.queue, self.settings, stopped=stopped, collector=collector).serve(progress=self.telemetry.progress)
        value = self.queue.show(job)
        self.assertEqual(value["state"], "queued")
        self.assertEqual(value["reason"], "operator_stopped")
        self.fixture.time += 30
        result = self.fixture.worker(collector=lambda *a, **kw: self.fail("고정 입력을 다시 수집했습니다")).run_once()
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["attempts"], 2)

    def test_real_process_sigterm_and_probe_does_not_initialize_queue(self):
        code = '''
import sys
from pathlib import Path
from unittest.mock import patch
from ai_ops.b_contracts import load_b_settings
from ai_ops.worker_runtime import main
root=Path(sys.argv[1]); config=Path(sys.argv[2])
settings=load_b_settings(config/'targets.practice.example.json',config/'b-runtime.example.json',config/'metric-queries.json')
with patch('ai_ops.worker_runtime.check_local'), patch('ai_ops.worker_runtime.load_b_settings',return_value=settings):
    raise SystemExit(main(['--config',str(config),'--store',str(root)]))
'''
        with subprocess.Popen([sys.executable, "-c", code, str(self.queue.root), str(ROOT / "config")],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        if check_worker(self.queue.root)["pid"] == child.pid:
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.05)
                else:
                    self.fail("worker 프로세스가 준비되지 않았습니다")
                child.send_signal(signal.SIGTERM)
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr.decode())
                self.assertNotIn(b"Traceback", stderr)
                self.assertEqual(stdout, b"")
            finally:
                if child.poll() is None:
                    child.kill(); child.wait()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--probe", "--config", str(ROOT / "config"), "--store", str(self.queue.root)]), 1)
            missing = self.fixture.root / "missing-store"
            self.assertEqual(main(["--probe", "--config", str(ROOT / "config"), "--store", str(missing)]), 1)
            self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
