"""API 키의 대화형 입력·비공개 저장·비노출 상태 확인을 시험한다."""

import contextlib
import io
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ops.cli import main
from ai_ops.incidents import IncidentStore
from ai_ops.openai_engine import AIAnalysisError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evals/fixtures/dev/s1-cache-error"
REGISTRY = ROOT / "config/targets.example.json"


class APIKeySetupTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "store"

    def test_interactive_setup_saves_key_privately_without_api_call_or_output_leak(self):
        fake_key = "sk-test-THIS-IS-NOT-A-REAL-KEY-1234567890"
        output = io.StringIO()
        with (patch("ai_ops.cli.sys.stdin.isatty", return_value=True),
              patch("ai_ops.cli.getpass.getpass", return_value=fake_key),
              patch("ai_ops.openai_engine.urllib.request.urlopen", side_effect=AssertionError("API 호출 금지")),
              contextlib.redirect_stdout(output)):
            self.assertEqual(main(["configure-api-key", "--store", str(self.root)]), 0)
        key_path = self.root / ".openai-api-key"
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        self.assertEqual(key_path.read_text(), fake_key)
        self.assertNotIn(fake_key, output.getvalue())
        self.assertEqual(IncidentStore(self.root).load_api_key(), fake_key)

        output = io.StringIO()
        with patch("ai_ops.cli.api_key_from_environment", return_value=""), contextlib.redirect_stdout(output):
            self.assertEqual(main(["api-key-status", "--store", str(self.root)]), 0)
        self.assertIn("개인 저장소", output.getvalue())
        self.assertNotIn(fake_key, output.getvalue())

        output = io.StringIO()
        with patch("ai_ops.cli.api_key_from_environment", return_value="sk-other-test-key-1234567890"), contextlib.redirect_stdout(output):
            self.assertEqual(main(["api-key-status", "--store", str(self.root)]), 0)
        self.assertIn("환경변수", output.getvalue())
        self.assertNotIn(fake_key, output.getvalue())

        received = []

        def capture_key(*arguments):
            received.append(arguments[-1])
            raise AIAnalysisError("model_credit_balance_exhausted")

        arguments = [
            "investigate", "--mode", "ai", "--store", str(self.root),
            "--bundle", str(FIXTURE), "--registry", str(REGISTRY),
            "--question", "캐시 오류를 조사해줘", "--request-id", "key-route-1",
        ]
        output = io.StringIO()
        error = io.StringIO()
        with (patch("ai_ops.cli.api_key_from_environment", return_value=""),
              patch("ai_ops.cli.analyze_ai", side_effect=capture_key),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(error)):
            self.assertEqual(main(arguments), 1)
        self.assertEqual(received, [fake_key])
        self.assertNotIn(fake_key, output.getvalue())
        self.assertIn("model_credit_balance_exhausted", output.getvalue())
        self.assertIn("partial", output.getvalue())
        self.assertNotIn(fake_key, error.getvalue())
        lines = output.getvalue().splitlines()
        incident_id = next(line.split(": ", 1)[1] for line in lines if line.startswith("사건 ID:"))
        run_id = next(line.split(": ", 1)[1] for line in lines if line.startswith("실행 ID:"))
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            self.assertEqual(main(["show", "--store", str(self.root), "--incident", incident_id,
                                   "--run", run_id, "--format", "json"]), 1)
        self.assertIn('"failure_reason": "model_credit_balance_exhausted"', shown.getvalue())
        self.assertIn('"analysis_status": "partial"', shown.getvalue())

    def test_noninteractive_input_is_rejected_and_unsafe_file_is_not_read(self):
        error = io.StringIO()
        with patch("ai_ops.cli.sys.stdin.isatty", return_value=False), contextlib.redirect_stderr(error):
            self.assertEqual(main(["configure-api-key", "--store", str(self.root)]), 2)
        self.assertIn("터미널", error.getvalue())
        self.assertFalse((self.root / ".openai-api-key").exists())

        store = IncidentStore(self.root)
        key_path = self.root / ".openai-api-key"
        key_path.write_text("sk-test-THIS-IS-NOT-A-REAL-KEY-1234567890")
        os.chmod(key_path, 0o644)
        with self.assertRaisesRegex(ValueError, "0600"):
            store.load_api_key()
        key_path.unlink()
        key_path.symlink_to(self.root / ".fingerprint-key")
        with self.assertRaises(OSError):
            store.load_api_key()


if __name__ == "__main__":
    unittest.main()
