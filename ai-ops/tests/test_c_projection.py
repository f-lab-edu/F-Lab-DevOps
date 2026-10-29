"""실습 CI worker의 사건 조회 TLS 경로와 로컬 사용자 경계를 확인한다."""

from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_ops.c_operator import OperatorServer, _client
from ai_ops.c_projection_client import ProjectionClient
from ai_ops.c_projection_server import projection_server
import test_ci_queue as queue_fixtures
from test_c_recovery import projection


class CProjectionTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.identity = self.root / "identity"
        self.identity.mkdir(mode=0o700)
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=ci-worker.ai-ci.svc",
            "-addext", "subjectAltName=DNS:ci-worker.ai-ci.svc",
            "-keyout", str(self.identity / "tls.key"), "-out", str(self.identity / "tls.crt"),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.token = "projection-token-" + "b" * 48
        (self.identity / "token").write_text(self.token)
        for name in ("token", "tls.crt", "tls.key"):
            (self.identity / name).chmod(0o600)
        self.local_token = self.root / "local-token"
        self.local_token.write_text(self.token)
        self.local_token.chmod(0o600)
        self.store = self.root / "incidents"
        self.store.mkdir(mode=0o700)

    def client(self, port, *, token=None, ca=None):
        return ProjectionClient(token or self.local_token, ca or self.identity / "tls.crt", port=port)

    def test_tls_projection_returns_only_allowlisted_fields(self):
        with patch("ai_ops.c_projection_server.read_incident_projection", return_value=projection()) as read:
            with projection_server(self.store, self.identity, address=("127.0.0.1", 0)) as server:
                result = self.client(server.server_port).fetch(projection().incident_id, projection().run_id)
        self.assertEqual(result, projection())
        self.assertNotIn("raw", result.model_dump_json())
        read.assert_called_once_with(self.store, projection().incident_id, projection().run_id)

    def test_auth_missing_case_and_unsafe_local_file_fail_closed(self):
        wrong = self.root / "wrong-token"
        wrong.write_text("wrong-" + "x" * 48)
        wrong.chmod(0o600)
        with projection_server(self.store, self.identity, address=("127.0.0.1", 0)) as server:
            with self.assertRaises(ValueError):
                self.client(server.server_port, token=wrong).fetch(projection().incident_id, projection().run_id)
        with patch("ai_ops.c_projection_server.read_incident_projection", side_effect=ValueError("private report")):
            with projection_server(self.store, self.identity, address=("127.0.0.1", 0)) as server:
                with self.assertRaises(ValueError):
                    self.client(server.server_port).fetch(projection().incident_id, projection().run_id)
        self.local_token.chmod(0o644)
        with self.assertRaises(ValueError):
            self.client(9444).fetch(projection().incident_id, projection().run_id)
        with self.assertRaises(ValueError):
            self.client(9444).fetch("inc-INVALID", projection().run_id)

    def test_local_operator_reads_remote_worker_projection(self):
        runner = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "12345\n", "")
        with patch("ai_ops.c_projection_server.read_incident_projection", return_value=projection()):
            with projection_server(self.store, self.identity, address=("127.0.0.1", 0)) as remote:
                with OperatorServer(self.root / "state", None, 12345, github_runner=runner,
                                    projection_client=self.client(remote.server_port)) as local:
                    thread = threading.Thread(target=local.handle_request)
                    thread.start()
                    try:
                        reply = _client(self.root / "state/operator.sock", "show",
                                        projection().incident_id, projection().run_id)
                    finally:
                        thread.join(timeout=5)
        self.assertEqual(reply["status"], "ok")
        self.assertEqual(reply["projection"]["bundle_hash"], projection().bundle_hash)
        self.assertNotIn("raw", str(reply))

    def test_real_ci_worker_record_is_projected_without_report_body(self):
        fixture = queue_fixtures.CIQueueTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.queue.submit_ci(fixture.settings, fixture.history)
        result = fixture.worker().run_once()
        self.assertEqual(result["state"], "completed")
        with projection_server(fixture.queue.root, self.identity, address=("127.0.0.1", 0)) as server:
            projected = self.client(server.server_port).fetch(result["incident_id"], result["run_id"])
        self.assertEqual(projected.incident_id, result["incident_id"])
        self.assertEqual(projected.run_id, result["run_id"])
        self.assertEqual(projected.target.namespace, "ai-ops-lab")
        self.assertEqual(len(projected.bundle_hash), 64)
        self.assertIsNone(projected.image_failure)
        self.assertNotIn("자동 원복 단계", projected.model_dump_json())


if __name__ == "__main__":
    unittest.main()
