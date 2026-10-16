"""배포 진단이 모델·네트워크 없이 설정·런북·저장소만 검사하는지 확인한다."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ops.deployment_check import check_local, main
from ai_ops import replay_tools


ROOT = Path(__file__).resolve().parents[1]


class DeploymentCheckTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "config"
        self.config.mkdir()
        shutil.copy(ROOT / "config/targets.practice.example.json", self.config / "bindings.json")
        shutil.copy(ROOT / "config/metric-queries.json", self.config / "metric-queries.json")
        self.runtime = json.loads((ROOT / "config/b-runtime.example.json").read_text())
        self.token = self.root / "projected-token"
        self.token.write_text("diagnostic-test-token\n")
        self.runtime["connections"]["practice-kubernetes"].update(base_url="https://kubernetes.default.svc:443", token_file=str(self.token))
        self.runtime["connections"]["practice-prometheus"]["base_url"] = "http://prometheus.validation.invalid:9090"
        self.write_runtime()
        self.store = self.root / "data" / "incidents"

    def write_runtime(self):
        (self.config / "runtime.json").write_text(json.dumps(self.runtime))

    def test_local_wal_and_existing_records_survive_without_network(self):
        self.store.mkdir(parents=True)
        record = self.store / "existing-report.json"
        record.write_text('{"preserve":true}')
        with patch("socket.create_connection", side_effect=AssertionError("통신하면 안 된다")):
            check_local(self.config, self.store)
            check_local(self.config, self.store)
        self.assertEqual(record.read_text(), '{"preserve":true}')
        self.assertEqual([p.name for p in self.store.iterdir()], [record.name])
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o700)

    def test_missing_endpoint_and_invalid_model_flag_are_rejected(self):
        self.runtime["connections"]["practice-prometheus"]["base_url"] = None
        self.write_runtime()
        with self.assertRaises(ValueError):
            check_local(self.config, self.store)
        self.runtime["llm_enabled"] = True
        self.write_runtime()
        with self.assertRaises(ValueError):
            check_local(self.config, self.store)

    def test_projected_token_rotation_and_invalid_value(self):
        target = self.root / "token-generation-1"
        target.write_text("first-token")
        self.token.unlink(); self.token.symlink_to(target)
        check_local(self.config, self.store)
        target.write_text("rotated-token")
        check_local(self.config, self.store)
        target.write_text("invalid token")
        with self.assertRaises(ValueError):
            check_local(self.config, self.store)
        target.write_text("x" * 8192 + "\n")
        with self.assertRaises(ValueError):
            check_local(self.config, self.store)
        target.write_text("safe-token")
        self.runtime["connections"]["practice-prometheus"]["token_file"] = str(self.token)
        self.write_runtime()
        with self.assertRaises(ValueError):
            check_local(self.config, self.store)

    def test_failure_message_does_not_expose_exception_or_key(self):
        output = io.StringIO()
        with patch("ai_ops.deployment_check.check_local", side_effect=RuntimeError("secret-key-value")), contextlib.redirect_stdout(output):
            code = main(["--config", str(self.config), "--store", str(self.store)])
        self.assertEqual(code, 1)
        self.assertNotIn("secret-key-value", output.getvalue())
        self.assertNotIn(str(self.config), output.getvalue())

    def test_runbook_tamper_and_unwritable_storage_fail(self):
        books = self.root / "runbooks"
        shutil.copytree(ROOT / "runbooks", books)
        with patch.object(replay_tools, "RUNBOOK_ROOT", books):
            check_local(self.config, self.store)
            (books / "cache.md").write_text("변조된 런북")
            with self.assertRaises(ValueError):
                check_local(self.config, self.store)
        with patch("ai_ops.deployment_check.sqlite3.connect", side_effect=OSError("저장소 쓰기 실패")):
            with self.assertRaises(OSError):
                check_local(self.config, self.store)


if __name__ == "__main__":
    unittest.main()
