"""승인 전 외부 호출 차단·일회 사용·실패 비용·보관·인용 경계를 검증한다."""

import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.contracts import canonical_json_bytes
from ai_ops.model_comparison import ComparisonStore, rules_projection
from ai_ops.projected_model_engine import CallPlan, validate_projected_report
from test_model_projection import example_plan, example_projection
from test_live_collectors import AT


class FakeTransport:
    comparison_mode = "mock"

    def __init__(self, report, *, failure=None, tokens=100, changed_model=None):
        self.report = report; self.failure = failure; self.tokens = tokens
        self.changed_model = changed_model; self.calls = []

    def __call__(self, path, body, *, timeout):
        self.calls.append((path, body, timeout))
        if path.endswith("input_tokens"):
            return {"object": "response.input_tokens", "input_tokens": self.tokens}
        if self.failure:
            raise self.failure
        return {"status": "completed", "model": self.changed_model or "gpt-6-sol",
            "usage": {"input_tokens": 100, "output_tokens": 100},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(self.report)}]}]}


class ModelComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.temp_path = Path(self.temp.name).resolve()
        self.environment = patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.temp_path)})
        self.environment.start(); self.addCleanup(self.environment.stop)
        self.now = AT
        self.store = ComparisonStore(self.temp_path/"store", now=lambda: self.now)
        self.projection = example_projection(); self.plan = example_plan()
        self.identifier = self.store.prepare(self.projection, self.plan)
        self.transport = FakeTransport(rules_projection(self.projection.view).model_dump(mode="json"))

    def approve(self):
        preview = self.store.preview(self.identifier)
        self.store.approve(self.identifier, binding_hash=preview["binding_hash"], actor="operator")

    def test_prepare_preview_have_no_calls_and_approval_binds_both_exact_requests(self):
        preview = self.store.preview(self.identifier)
        self.assertEqual(set(preview["external_requests"]), {"/v1/responses/input_tokens", "/v1/responses"})
        generation = preview["external_requests"]["/v1/responses"]
        self.assertFalse(generation["store"]); self.assertNotIn("tools", generation)
        self.assertEqual(self.plan.execution_mode, "mock")
        with self.assertRaises(ValueError):
            self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(self.transport.calls, [])
        self.assertFalse((self.store.root/"budget.json").exists())
        with self.assertRaises(ValueError):
            self.store.approve(self.identifier, binding_hash="0"*64, actor="operator")
        self.approve()
        self.assertEqual(self.store.show(self.identifier)["state"], "approved")

    def test_one_use_replay_never_generates_twice_and_semantics_need_review(self):
        self.approve(); result = self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(result["state"], "needs_review")
        self.assertEqual(result["reason"], "semantic_review_required")
        self.assertEqual(result["external_model_calls"], 0)
        self.assertEqual(result["transport_kind"], "mock")
        self.assertEqual(result["rules"]["input_hash"], result["model_report"]["input_hash"])
        self.assertEqual([c[0] for c in self.transport.calls], ["/v1/responses/input_tokens", "/v1/responses"])
        self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.store.prepare(self.projection, self.plan), self.identifier)
        with self.assertRaises(ValueError): self.approve()
        self.assertEqual(self.store._read(self.store.root/"budget.json")["days"][AT.date().isoformat()], 10)

    def test_expired_approval_and_stale_price_never_call_transport(self):
        self.approve(); self.now += timedelta(minutes=31)
        with self.assertRaises(ValueError): self.store.run(self.identifier, transport=self.transport)
        self.assertFalse(self.transport.calls)
        self.now = AT+timedelta(days=8)
        other = self.store.prepare(self.projection, self.plan)
        with self.assertRaises(ValueError): self.approve()

    def test_tampered_signed_input_code_or_approval_rejected_before_calls(self):
        self.approve(); path = self.store._path(self.identifier)
        data = json.loads(path.read_text()); data["data"]["plan"]["account_label"] = "replaced"
        path.write_text(json.dumps(data)); path.chmod(0o600)
        with self.assertRaises(ValueError): self.store.run(self.identifier, transport=self.transport)
        self.assertFalse(self.transport.calls)

    def test_code_changed_after_preview_requires_new_approval(self):
        self.approve()
        with patch("ai_ops.model_comparison.implementation_hash", return_value="0"*64), self.assertRaises(ValueError):
            self.store.run(self.identifier, transport=self.transport)
        self.assertFalse(self.transport.calls)

    def test_token_count_budget_overrun_blocks_generation_and_keeps_reservation(self):
        self.approve(); self.transport.tokens = 10**7
        result = self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(result["reason"], "projection_case_budget_limit")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(result["reserved_usd"], 0.10)
        self.assertNotIn("model_report", result)

    def test_timeout_and_restart_keep_unknown_no_retry_no_raw_exception(self):
        self.approve(); self.transport.failure = TimeoutError("PRIVATE_SOURCE_SENTINEL")
        result = self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(result["reason"], "projection_generation_unknown")
        self.assertIsNone(result["usage"])
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", json.dumps(result))
        self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(len(self.transport.calls), 2)

    def test_process_interruption_preserves_sending_no_auto_repeat(self):
        self.approve()
        class Interrupted(FakeTransport):
            def __call__(self, path, body, *, timeout):
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.store.run(self.identifier, transport=Interrupted({}))
        self.assertEqual(self.store.show(self.identifier)["state"], "sending")
        reopened = ComparisonStore(self.store.root, now=lambda: self.now)
        with self.assertRaises(ValueError): reopened.run(self.identifier, transport=self.transport)
        self.assertEqual(reopened.show(self.identifier)["state"], "needs_review")
        self.assertFalse(self.transport.calls)

    def test_daily_limit_cannot_be_bypassed_by_new_case_or_account(self):
        for i in range(10):
            plan = CallPlan.model_validate_json(self.plan.model_dump_json()).model_copy(update={"account_label": f"test-{i}"})
            identifier = self.store.prepare(self.projection, plan)
            self.store.approve(identifier, binding_hash=self.store.preview(identifier)["binding_hash"], actor="operator")
            self.store.run(identifier, transport=self.transport)
        self.approve()
        with self.assertRaisesRegex(ValueError, "daily_budget"):
            self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(len(self.transport.calls), 20)

    def test_forbidden_citation_and_healthy_or_executed_report_rejected(self):
        base = rules_projection(self.projection.view).model_dump(mode="json")
        for change in ({"current_state": "healthy"}, {"suggested_actions": [{"description": "done", "execution_status": "executed"}]},
                       {"facts": [{"statement": "invented", "evidence_ids": ["ev-view-999"]}]}):
            with self.assertRaises(ValueError): validate_projected_report({**base, **change}, self.projection.view)
        self.approve(); self.transport.report = {**base, "current_state": "healthy"}
        result = self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(result["reason"], "projection_report_contract_rejected")
        self.assertIsNotNone(result["usage"])
        self.assertNotIn("model_report", result)

    def test_provider_incomplete_and_model_change_do_not_store_draft(self):
        self.approve(); self.transport.changed_model = "other-model"
        result = self.store.run(self.identifier, transport=self.transport)
        self.assertEqual(result["reason"], "projection_response_incomplete")
        self.assertNotIn("model_report", result)

    def test_default_and_unlabelled_transports_disabled_before_any_network(self):
        self.approve()
        for transport in (None, lambda *a, **k: self.fail("통신하면 안 된다")):
            with self.assertRaisesRegex(ValueError, "transport_not_enabled"):
                self.store.run(self.identifier, transport=transport)

    def test_retention_deletes_related_body_keeps_replay_tombstone_and_budget(self):
        self.approve(); self.store.run(self.identifier, transport=self.transport)
        self.now += timedelta(days=8)
        self.assertIn(self.identifier, self.store.purge()["expired"])
        self.assertIn("projection", self.store._read(self.store._path(self.identifier)))
        self.store.purge(apply=True)
        tombstone = self.store._read(self.store._path(self.identifier))
        self.assertEqual(tombstone["state"], "purged")
        self.assertNotIn("projection", tombstone)
        self.assertNotIn("approval", tombstone)
        self.assertNotIn("model_report", tombstone)
        self.assertIn(AT.date().isoformat(), self.store._read(self.store.root/"budget.json")["days"])
        self.assertEqual(self.store.prepare(self.projection, self.plan), self.identifier)
        with self.assertRaises(ValueError): self.store.preview(self.identifier)

    def test_private_paths_symlinks_and_changed_permissions_rejected(self):
        for p in self.store.root.iterdir():
            self.assertEqual(p.stat().st_mode & 0o077, 0)
        target = self.store.root/"alias"; target.symlink_to(self.store.root, target_is_directory=True)
        with self.assertRaises(ValueError): ComparisonStore(target)
        self.store._path(self.identifier).chmod(0o644)
        with self.assertRaises(ValueError): self.store.show(self.identifier)

    def test_parallel_runs_share_one_reservation_and_one_generation(self):
        from concurrent.futures import ThreadPoolExecutor
        self.approve()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.store.run(self.identifier, transport=self.transport), range(2)))
        self.assertEqual(len(self.transport.calls), 2)
        self.assertTrue(all(r["state"] == "needs_review" for r in results))
        self.assertEqual(self.store._read(self.store.root/"budget.json")["days"][AT.date().isoformat()], 10)

    def test_orphan_body_purge_and_ninety_day_ledger_retention(self):
        self.approve(); self.store.run(self.identifier, transport=self.transport)
        orphan = self.store.root/".pending-interrupted"
        orphan.write_text("PRIVATE_BODY"); orphan.chmod(0o600)
        os.utime(orphan, (AT.timestamp()-8*86400, AT.timestamp()-8*86400))
        self.assertIn(orphan.name, self.store.purge()["orphan_files"])
        self.store.purge(apply=True); self.assertFalse(orphan.exists())
        self.now += timedelta(days=91)
        self.store.purge(apply=True)
        self.assertEqual(self.store._read(self.store.root/"budget.json")["days"], {})

    def test_cli_mock_result_is_explicitly_not_a_live_model_call(self):
        import contextlib
        import importlib.util
        import io
        spec = importlib.util.spec_from_file_location("comparison_cli", Path(__file__).resolve().parents[1]/"scripts/compare_model_projection.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.approve(); output = io.StringIO()
        with patch.object(module, "ComparisonStore", return_value=self.store), contextlib.redirect_stdout(output), patch("socket.create_connection", side_effect=AssertionError("실제 연결 금지")):
            self.assertEqual(module.main(["run-mock", "--id", self.identifier]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["transport_kind"], "mock")
        self.assertEqual(result["external_model_calls"], 0)
        self.assertEqual(result["state"], "needs_review")


if __name__ == "__main__":
    unittest.main()
