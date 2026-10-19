"""관측 정보의 영속성·유출 방지·읽기 전용 HTTP 경계를 검증한다."""

import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_ops.observability import Telemetry, MetricsServer, log_event, observe


class ObservabilityTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        env = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        env.start(); self.addCleanup(env.stop)
        self.store = self.root / "store"

    def test_cross_process_persistence_and_bounded_series(self):
        telemetry = Telemetry(self.store)
        telemetry.record("collection", "partial", 2.5)
        result = subprocess.run([sys.executable, "-c",
            "from ai_ops.observability import Telemetry; from pathlib import Path; import sys; Telemetry(Path(sys.argv[1])).record('collection','partial',1.25)",
            str(self.store)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = Telemetry(self.store).render().decode()
        self.assertIn('ai_ops_operation_duration_seconds_count{stage="collection",result="partial"} 2', text)
        self.assertIn('ai_ops_operation_duration_seconds_sum{stage="collection",result="partial"} 3.75', text)
        self.assertEqual(len([line for line in text.splitlines() if not line.startswith("#")]), 19)
        self.assertEqual(telemetry.path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(telemetry.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM operations").fetchone()[0], 9)
        for stage, result, duration in (("request-secret", "success", 1), ("report", "secret", 1),
                                        ("report", "success", float("nan")), ("report", "failure", -1)):
            with self.assertRaises(ValueError):
                telemetry.record(stage, result, duration)

    def test_operation_failure_remains_original_and_has_no_exception_body(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            with self.assertRaisesRegex(RuntimeError, "sensitive-question-token"):
                with observe(self.store, "collection"):
                    raise RuntimeError("sensitive-question-token")
            with patch.object(Telemetry, "record", side_effect=sqlite3.OperationalError("private-path")):
                with observe(self.store, "report") as observation:
                    observation["result"] = "partial"
        self.assertNotIn("sensitive", output.getvalue())
        self.assertNotIn("private-path", output.getvalue())
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["event"] for event in events], ["operation_finished", "operation_finished", "telemetry_failed"])
        self.assertTrue(all(event["timestamp"].endswith("Z") for event in events))
        self.assertIn('stage="collection",result="failure"} 1', Telemetry(self.store).render().decode())

    def test_links_and_nonprivate_files_rejected(self):
        telemetry = Telemetry(self.store)
        outside = self.root / "outside"
        outside.write_text("preserve")
        telemetry.path.unlink()
        telemetry.path.symlink_to(outside)
        with self.assertRaises(ValueError):
            Telemetry(self.store)
        self.assertEqual(outside.read_text(), "preserve")
        telemetry.path.unlink()
        telemetry = Telemetry(self.store)
        journal = Path(str(telemetry.path) + "-journal")
        journal.symlink_to(outside)
        with self.assertRaises(ValueError):
            telemetry.record("report", "success", 1)
        journal.unlink()
        telemetry.path.chmod(0o644)
        with self.assertRaises(ValueError):
            Telemetry(self.store)

    def test_metrics_http_has_no_write_api_and_storage_error_is_503(self):
        telemetry = Telemetry(self.store)
        telemetry.record("diagnostic", "success", 0.5)
        server = MetricsServer(telemetry, ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for method, path, status in (("GET", "/metrics", 200), ("POST", "/metrics", 501),
                                         ("GET", "/metrics?secret=1", 404), ("GET", "/stop", 404)):
                client = http.client.HTTPConnection(*server.server_address, timeout=3)
                client.request(method, path)
                response = client.getresponse()
                self.assertEqual(response.status, status)
                response.read(); client.close()
            with patch.object(telemetry, "render", side_effect=sqlite3.OperationalError("private-error")), contextlib.redirect_stderr(io.StringIO()) as output:
                client = http.client.HTTPConnection(*server.server_address, timeout=3)
                client.request("GET", "/metrics")
                response = client.getresponse()
                self.assertEqual(response.status, 503)
                self.assertNotIn(b"private-error", response.read())
                client.close()
            self.assertNotIn("private-error", output.getvalue())
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_log_rejects_arbitrary_event_or_status(self):
        with self.assertRaises(ValueError):
            log_event("secret")
        with self.assertRaises(ValueError):
            log_event("process_started", result="private")


if __name__ == "__main__":
    unittest.main()
