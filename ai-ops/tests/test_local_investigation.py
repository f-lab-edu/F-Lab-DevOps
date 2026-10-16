"""A3 CLI·사건 저장·규칙 보고서의 로컬 전체 흐름 시험."""

import contextlib
import io
import json
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from ai_ops.cli import main
from ai_ops.contracts import SuggestedAction, load_target_registry
from ai_ops.incidents import IncidentStore, RunStopped
from ai_ops.reports import format_kst, render_markdown, validate_report
from ai_ops.rules import analyze_rules


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/fixtures/dev"
REGISTRY = ROOT / "config/targets.example.json"


class LocalInvestigationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store_root = Path(self.temporary.name) / "store"
        self.store = IncidentStore(self.store_root)
        self.registry = load_target_registry(REGISTRY)

    def investigate(self, case="s1-cache-error", request_id="request-1", question="캐시 상태를 조사해줘", analyzer=analyze_rules):
        return self.store.investigate(
            bundle_root=FIXTURES / case, registry=self.registry,
            question=question, request_id=request_id, analyzer=analyzer,
        )

    def test_eight_cases_have_distinct_rule_results_and_existing_citations(self):
        expected = {
            "s1-cache-error": ("suspected_cause", "unknown"),
            "s2-normal-miss": ("no_incident", "unknown"),
            "s3-image-tag": ("suspected_cause", "unknown"),
            "s4-db-latency": ("insufficient_evidence", "unknown"),
            "s5-insufficient-data": ("insufficient_evidence", "unknown"),
            "s6-healthy": ("no_incident", "healthy"),
            "s9-primary-consistency": ("no_incident", "unknown"),
            "s10-rollback-recovered": ("suspected_cause", "recovered"),
        }
        records = {}
        for case, result in expected.items():
            with self.subTest(case=case):
                record = self.investigate(case=case, request_id=case)
                records[case] = record
                self.assertEqual((record.report.diagnosis_status, record.report.current_state), result)
                self.assertEqual(record.report.execution.mode, "rules")
                validate_report(record.report, record.request, record.bundle, record.run_id)
                self.assertIn("AI 분석·실시간 클러스터 확인", record.markdown)
                self.assertNotIn("DEMO_SECRET_SENTINEL", record.markdown)
        self.assertEqual(len(records["s5-insufficient-data"].report.missing_evidence), 2)
        self.assertEqual(len(records["s5-insufficient-data"].report.facts), 0)

    def test_replay_reuses_result_and_changed_content_is_rejected(self):
        first = self.investigate(question="조사해줘 token=DEMO_SECRET_SENTINEL")
        second = self.investigate(question="조사해줘 token=DEMO_SECRET_SENTINEL")
        self.assertEqual((first.incident_id, first.run_id), (second.incident_id, second.run_id))
        self.assertNotIn("DEMO_SECRET_SENTINEL", first.request.question)
        with self.assertRaisesRegex(ValueError, "different input"):
            self.investigate(question="다른 질문")
        with self.assertRaisesRegex(ValueError, "different input"):
            self.investigate(question="조사해줘 token=다른_가짜_값")
        with self.assertRaisesRegex(ValueError, "different input"):
            self.investigate(case="s2-normal-miss")

    def test_private_storage_utc_json_and_kst_markdown(self):
        record = self.investigate()
        run_dir = self.store_root / record.incident_id / record.run_id
        for name in ("input.json", "metadata.json", "report.json", "report.md"):
            self.assertTrue((run_dir / name).exists())
            self.assertEqual(stat.S_IMODE((run_dir / name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.store_root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.store_root / ".fingerprint-key").stat().st_mode), 0o600)
        report_json = json.loads((run_dir / "report.json").read_text())
        input_json = json.loads((run_dir / "input.json").read_text())
        metadata_json = json.loads((run_dir / "metadata.json").read_text())
        self.assertEqual(metadata_json["input_hash"], record.bundle.bundle_hash)
        self.assertEqual(report_json["window"]["start"], "2026-09-28T00:00:00Z")
        self.assertEqual(input_json["bundle"]["evidence"][0]["observed_at"], "2026-09-28T00:20:00Z")
        self.assertNotIn("DEMO_SECRET_SENTINEL", (run_dir / "input.json").read_text())
        self.assertIn("2026-09-28 09:20:00 KST (UTC+09:00)", record.markdown)
        self.assertEqual(self.store.show(record.incident_id, record.run_id).report, record.report)
        utc = datetime(2026, 9, 28, 0, 20, tzinfo=timezone.utc)
        kst = utc.astimezone(ZoneInfo("Asia/Seoul"))
        self.assertEqual(kst.hour, 9)
        self.assertEqual(kst.astimezone(timezone.utc), utc)
        self.assertIn("09:20:00 KST", format_kst(utc))

    def test_markdown_omits_untrusted_external_link(self):
        record = self.investigate()
        report = record.report.model_copy(deep=True)
        report.missing_evidence[0].reason = "추가 확인: [비밀 보내기](https://example.invalid/collect)"
        rendered = render_markdown(report, record.request, record.bundle)
        self.assertNotIn("https://example.invalid", rendered)
        self.assertIn("외부 링크 생략", rendered)

    def test_report_identity_and_action_contract_reject_tampering(self):
        record = self.investigate()
        for field, value in (("incident_id", "inc-" + "0" * 32),
                             ("run_id", "run-" + "0" * 32),
                             ("input_hash", "0" * 64)):
            with self.subTest(field=field):
                altered = record.report.model_copy(deep=True)
                setattr(altered, field, value)
                with self.assertRaises(ValueError):
                    validate_report(altered, record.request, record.bundle, record.run_id)
        altered = record.report.model_copy(deep=True)
        altered.suggested_actions = [SuggestedAction(description="Pod 재시작 완료",
                                                     execution_status="not_executed")]
        with self.assertRaises(ValueError):
            validate_report(altered, record.request, record.bundle, record.run_id)
        altered.suggested_actions = [SuggestedAction(description="Pod 재시작 완료 여부를 확인한다.",
                                                     execution_status="not_executed")]
        validate_report(altered, record.request, record.bundle, record.run_id)

    def test_markdown_removes_control_characters_and_unsafe_schemes(self):
        record = self.investigate()
        report = record.report.model_copy(deep=True)
        report.limitations.append("확인\x1b[31m\u202e javascript:alert(1) data:text/html,a file:///tmp/key")
        rendered = render_markdown(report, record.request, record.bundle)
        for unsafe in ("\x1b", "\u202e", "javascript:", "data:text", "file:///tmp"):
            self.assertNotIn(unsafe, rendered)
        self.assertIn("외부 링크 생략", rendered)

    def test_markdown_converts_explicit_utc_times_in_narrative_to_kst(self):
        record = self.investigate()
        report = record.report.model_copy(deep=True)
        report.facts[0].statement = "2026-09-28 00:20 UTC와 2026-09-28T00:20:05Z에 오류가 관측됐다."
        report.limitations.append("00:20 UTC 부근의 저장된 관측이다.")
        rendered = render_markdown(report, record.request, record.bundle)
        self.assertIn("2026-09-28 09:20:00 KST", rendered)
        self.assertIn("2026-09-28 09:20:05 KST", rendered)
        self.assertNotIn("00:20 UTC", rendered)

    def test_interrupted_run_becomes_failed_and_keeps_input(self):
        record = self.investigate()
        run_dir = self.store_root / record.incident_id / record.run_id
        metadata_path = run_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["status"] = "analyzing"
        metadata_path.write_text(json.dumps(metadata))
        resumed = self.store.show(record.incident_id, record.run_id)
        self.assertEqual(resumed.metadata["status"], "failed")
        self.assertEqual(resumed.metadata["failure_reason"], "interrupted")
        self.assertTrue((run_dir / "input.json").exists())
        self.assertEqual(self.investigate().metadata["status"], "failed")

    def test_cancellation_and_invalid_report_are_preserved(self):
        def cancel(*_):
            raise KeyboardInterrupt()

        with self.assertRaises(RunStopped) as cancelled:
            self.investigate(request_id="cancel-1", analyzer=cancel)
        self.assertEqual(cancelled.exception.status, "cancelled")
        self.assertEqual(self.store.show(cancelled.exception.incident_id, cancelled.exception.run_id).metadata["status"], "cancelled")

        def bad_report(request, bundle, run_id):
            report = analyze_rules(request, bundle, run_id)
            report.facts[0].evidence_ids = ["ev-unknown"]
            return report

        with self.assertRaises(RunStopped) as failed:
            self.investigate(request_id="bad-1", analyzer=bad_report)
        self.assertEqual(failed.exception.status, "failed")
        result = self.store.show(failed.exception.incident_id, failed.exception.run_id)
        self.assertEqual(result.metadata["failure_reason"], "rules_error")

    def test_cli_validate_investigate_show_and_ai_requires_configuration(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main(["validate-bundle", "--bundle", str(FIXTURES / "s1-cache-error"), "--registry", str(REGISTRY)])
        self.assertEqual(status, 0)
        self.assertIn("KST", output.getvalue())

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = main([
                "investigate", "--bundle", str(FIXTURES / "s1-cache-error"),
                "--registry", str(REGISTRY), "--store", str(self.store_root),
                "--question", "캐시 오류를 조사해줘", "--request-id", "cli-1",
            ])
        self.assertEqual(status, 0)
        lines = output.getvalue().splitlines()
        incident_id = next(line.split(": ", 1)[1] for line in lines if line.startswith("사건 ID:"))
        run_id = next(line.split(": ", 1)[1] for line in lines if line.startswith("실행 ID:"))
        for format_name, expected in (("json", "2026-09-28T00:00:00Z"), ("markdown", "2026-09-28 09:00:00 KST")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["show", "--store", str(self.store_root), "--incident", incident_id,
                               "--run", run_id, "--format", format_name])
            self.assertEqual(status, 0)
            self.assertIn(expected, output.getvalue())
        error = io.StringIO()
        with contextlib.redirect_stderr(error), patch("ai_ops.cli.api_key_from_environment", return_value=""):
            status = main(["investigate", "--bundle", str(FIXTURES / "s1-cache-error"),
                           "--registry", str(REGISTRY), "--store", str(self.store_root),
                           "--question", "조사해줘", "--request-id", "cli-ai", "--mode", "ai"])
        self.assertEqual(status, 2)
        self.assertIn("configure-api-key", error.getvalue())

    def test_store_inside_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            IncidentStore(ROOT / "runtime")


if __name__ == "__main__":
    unittest.main()
