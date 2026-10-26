"""v3의 입력별 승인·중복 차단·기존 장부 공유·보존 호환성을 확인한다."""

from datetime import timedelta
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.model_comparison import ComparisonStore
from ai_ops.projected_sequence_model_engine import rules_sequence_projection
from ai_ops.sequence_comparison import SequenceComparisonStore
from test_live_collectors import AT
from test_model_comparison import FakeTransport
from test_model_projection import example_plan, example_projection
from test_model_sequence_projection import case, project


class SequenceComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)})
        environment.start(); self.addCleanup(environment.stop)
        self.now = AT
        self.store = SequenceComparisonStore(self.root/"v3", budget_root=self.root/"legacy", now=lambda: self.now)
        snapshot, settings, base, probe, logs = case()
        self.projection = project(snapshot, settings, base, probe, logs)
        self.plan = example_plan()
        self.identifier = self.store.prepare(self.projection, self.plan)
        self.transport = FakeTransport(rules_sequence_projection(self.projection.view).model_dump(mode="json"))

    def approve(self):
        self.store.approve(self.identifier, binding_hash=self.store.preview(self.identifier)["binding_hash"], actor="operator")

    def test_preview_is_private_and_unapproved_run_never_sends(self):
        preview = self.store.preview(self.identifier)
        self.assertEqual(preview["state"], "prepared")
        body = preview["external_requests"]["/v1/responses"]
        self.assertIn("cache_probe_sequence", body["input"][0]["content"])
        self.assertNotIn("log_message", body["input"][0]["content"])
        path = self.store.write_preview(self.identifier)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            self.store.run(self.identifier, transport=self.transport)
        self.assertFalse(self.transport.calls)
        self.assertFalse((self.root/"legacy/budget.json").exists())

    def test_approved_mock_is_one_use_and_reserves_existing_budget(self):
        self.approve()
        result = self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(result["state"], "needs_review")
        self.assertEqual(result["model_report"]["input_hash"], self.projection.view_hash)
        self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(len(self.transport.calls), 2)
        ledger = self.store.budget_store._read(self.root/"legacy/budget.json")
        self.assertEqual(ledger["days"][AT.date().isoformat()], 10)
        self.assertFalse((self.root/"v3/budget.json").exists())

    def test_legacy_and_v3_share_daily_limit_without_reset(self):
        budget = self.store.budget_store
        with budget.locked():
            for _ in range(10): budget._reserve(self.now)
        self.approve()
        with self.assertRaisesRegex(ValueError, "daily_budget"):
            self.store.run(self.identifier, transport=self.transport)
        self.assertFalse(self.transport.calls)

    def test_expired_or_changed_code_approval_cannot_send(self):
        self.approve()
        self.now += timedelta(minutes=31)
        with self.assertRaises(ValueError): self.store.run(self.identifier, transport=self.transport)
        self.now = AT
        with patch("ai_ops.sequence_comparison.implementation_hash", return_value="0"*64), self.assertRaises(ValueError):
            self.store.run(self.identifier, transport=self.transport)
        self.assertFalse(self.transport.calls)

    def test_mock_approval_cannot_use_live_transport(self):
        self.approve()
        self.transport.comparison_mode = "live"
        with self.assertRaises(ValueError): self.store.run(self.identifier, transport=self.transport)
        self.assertFalse(self.transport.calls)

    def test_live_plan_requires_both_retention_and_account_confirmation(self):
        data = self.plan.model_dump(mode="json")
        data.update(execution_mode="live", credential_fingerprint="a"*64)
        data["price"].update(cache_write_per_million=2.5, cached_input_per_million=0.2)
        from ai_ops.projected_model_engine import CallPlan
        identifier = self.store.prepare(self.projection, CallPlan.model_validate(data))
        binding = self.store.preview(identifier)["binding_hash"]
        for kwargs in ({}, {"acknowledge_provider_retention": True},
                       {"confirm_optional_data_sharing_off": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.approve(identifier, binding_hash=binding, actor="operator", **kwargs)
        self.assertEqual(self.store.show(identifier)["state"], "prepared")
        self.assertFalse(self.transport.calls)

    def test_legacy_record_is_unchanged_and_expiry_purge_keeps_shared_budget(self):
        legacy = self.store.budget_store
        old_id = legacy.prepare(example_projection(), self.plan)
        original = (legacy.root/(old_id+".json")).read_bytes()
        self.approve(); self.store.run(self.identifier, transport=self.transport)
        self.store.write_preview(self.identifier)
        self.now += timedelta(days=8)
        self.store.purge(apply=True)
        self.assertFalse((self.store.root/("preview-"+self.identifier+".html")).exists())
        self.assertEqual((legacy.root/(old_id+".json")).read_bytes(), original)
        self.assertEqual(legacy.show(old_id)["state"], "prepared")
        self.assertTrue((legacy.root/"budget.json").exists())


if __name__ == "__main__":
    unittest.main()
