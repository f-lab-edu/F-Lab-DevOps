"""계약 실패의 안전한 분류·비공개 초안·기존 기록 호환성을 검증한다."""

import copy
from datetime import timedelta
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.model_comparison import ComparisonStore
from ai_ops.projected_sequence_model_engine import rules_sequence_projection
from ai_ops.sequence_comparison import SequenceComparisonStore
from ai_ops.sequence_diagnostics import DiagnosticSequenceStore, diagnose
from test_live_collectors import AT
from test_model_comparison import FakeTransport
from test_model_projection import example_plan, example_projection
from test_model_sequence_projection import case, project


class SequenceDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.now = AT
        self.store = DiagnosticSequenceStore(self.root/"diagnostic", budget_root=self.root/"legacy",
                                             now=lambda: self.now)
        snapshot, settings, base, probe, logs = case()
        self.projection = project(snapshot, settings, base, probe, logs)
        self.plan = example_plan()
        self.identifier = self.store.prepare(self.projection, self.plan)
        self.good = rules_sequence_projection(self.projection.view).model_dump(mode="json")

    def approve(self):
        self.store.approve(self.identifier, binding_hash=self.store.preview(self.identifier)["binding_hash"],
                           actor="operator")

    def run_report(self, report):
        self.approve()
        transport = FakeTransport(report)
        result = self.store.run(self.identifier, transport=transport)
        return result, transport

    def test_failure_classes_are_fixed_and_draft_never_enters_summary(self):
        examples = (
            ({"input_hash": "0"*64}, "input_hash_mismatch", []),
            ({"facts": [{"statement": "비공개 원문", "evidence_ids": ["ev-view-999"]}]},
             "unavailable_evidence_reference", []),
            ({"hypotheses": [{"cause": "미확인", "supporting_evidence_ids": [],
                              "contradicting_evidence_ids": []}]}, "hypothesis_without_support", []),
            ({"diagnosis_status": "suspected_cause"}, "diagnosis_hypothesis_conflict", []),
            ({"current_state": "healthy"}, "report_schema_invalid", ["current_state"]),
        )
        for changes, code, fields in examples:
            with self.subTest(code=code):
                # 한 입력은 일회만 실행하므로 사례마다 별도 저장 공간을 쓴다.
                store = DiagnosticSequenceStore(self.root/code, budget_root=self.root/"legacy",
                                                now=lambda: self.now)
                identifier = store.prepare(self.projection, self.plan)
                store.approve(identifier, binding_hash=store.preview(identifier)["binding_hash"],
                              actor="operator")
                draft = copy.deepcopy(self.good); draft.update(changes)
                transport = FakeTransport(draft)
                result = store.run(identifier, transport=transport)
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["failure_diagnostic"],
                                 {"code": code, "fields": fields, "draft_retained": True})
                self.assertEqual(result["rejected_draft"], draft)
                self.assertEqual(len(transport.calls), 2)
                self.assertEqual(store._path(identifier).stat().st_mode & 0o777, 0o600)
                summary = store.summary(identifier)
                self.assertNotIn("rejected_draft", summary)
                self.assertNotIn("비공개 원문", json.dumps(summary, ensure_ascii=False))
                store.run(identifier, transport=transport)
                self.assertEqual(len(transport.calls), 2)

    def test_unknown_schema_key_and_exception_message_are_not_exposed(self):
        invalid = {**self.good, "secret-field-PASSWORD": "SECRET_CONTENT_SENTINEL"}
        result, _ = self.run_report(invalid)
        self.assertEqual(result["failure_diagnostic"]["fields"], ["unrecognized"])
        self.assertNotIn("SECRET_CONTENT_SENTINEL", json.dumps(self.store.summary(self.identifier)))
        self.assertEqual(diagnose(ValueError("PRIVATE_ERROR_SENTINEL")),
                         {"code": "report_validation_internal_error", "fields": []})

    def test_unapproved_run_has_no_calls_or_reservation_and_success_keeps_no_draft(self):
        transport = FakeTransport(self.good)
        with self.assertRaises(ValueError): self.store.run(self.identifier, transport=transport)
        self.assertEqual(transport.calls, [])
        self.assertFalse((self.root/"legacy/budget.json").exists())
        self.approve()
        result = self.store.run(self.identifier, transport=transport)
        self.assertEqual(result["state"], "needs_review")
        self.assertNotIn("rejected_draft", result)
        self.assertNotIn("failure_diagnostic", result)

    def test_old_records_readable_and_same_budget_shared(self):
        old = SequenceComparisonStore(self.root/"v3", budget_root=self.root/"legacy",
                                      now=lambda: self.now)
        old_id = old.prepare(self.projection, self.plan)
        old_bytes = old._path(old_id).read_bytes()
        original = ComparisonStore(self.root/"legacy", now=lambda: self.now)
        original_id = original.prepare(example_projection(), self.plan)
        original_bytes = original._path(original_id).read_bytes()
        result, _ = self.run_report({**self.good, "input_hash": "0"*64})
        self.assertEqual(result["state"], "failed")
        self.assertEqual(old._path(old_id).read_bytes(), old_bytes)
        self.assertEqual(original._path(original_id).read_bytes(), original_bytes)
        self.assertEqual(old.show(old_id)["state"], "prepared")
        self.assertEqual(original.show(original_id)["state"], "prepared")
        ledger = original._read(self.root/"legacy/budget.json")
        self.assertEqual(ledger["days"][AT.date().isoformat()], 10)
        self.assertFalse((self.root/"diagnostic/budget.json").exists())

    def test_expiry_removes_rejected_draft_and_preview(self):
        result, _ = self.run_report({**self.good, "input_hash": "0"*64})
        self.assertTrue(result["failure_diagnostic"]["draft_retained"])
        preview = self.store.write_preview(self.identifier)
        self.now += timedelta(days=8)
        self.assertIn(self.identifier, self.store.purge(apply=True)["expired"])
        self.assertFalse(preview.exists())
        tombstone = self.store._read(self.store._path(self.identifier))
        self.assertEqual(tombstone["state"], "purged")
        self.assertNotIn("rejected_draft", tombstone)
        self.assertTrue((self.root/"legacy/budget.json").exists())

    def test_new_code_change_invalidates_approval_without_calls(self):
        self.approve(); transport = FakeTransport(self.good)
        with patch("ai_ops.sequence_diagnostics.implementation_hash", return_value="0"*64):
            with self.assertRaises(ValueError): self.store.run(self.identifier, transport=transport)
        self.assertFalse(transport.calls)

    def test_cli_show_outputs_diagnostics_without_private_draft(self):
        self.run_report({**self.good, "facts": [{"statement": "비공개 원문",
                                                "evidence_ids": ["ev-view-999"]}]})
        script = Path(__file__).resolve().parents[1]/"scripts/compare_sequence_diagnostics.py"
        spec = importlib.util.spec_from_file_location("compare_sequence_diagnostics_test", script)
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(script.parent), *sys.path]):
            spec.loader.exec_module(module)
        output = io.StringIO()
        with patch.object(module, "DiagnosticSequenceStore", return_value=self.store), redirect_stdout(output):
            self.assertEqual(module.main(["show", "--id", self.identifier]), 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data["failure_diagnostic"]["code"], "unavailable_evidence_reference")
        self.assertNotIn("비공개 원문", output.getvalue())


if __name__ == "__main__":
    unittest.main()
