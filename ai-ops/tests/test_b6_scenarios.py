"""실습 실패·부분 실행에서도 Redis와 자기 데이터가 복구되는지 검증한다."""

from copy import deepcopy
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from ai_ops.b6_scenarios import LabClient, advanced, check_delay, execute


class FakeLab:
    def __init__(self, fail=None, recovery_fail=False, hypotheses=0):
        self.fail = fail
        self.recovery_fail = recovery_fail
        self.hypotheses = hypotheses
        self.actions = []
        self.counters = {}
        self.replicas = 1
        self.quality = True
        self.db = {"count": 0, "seconds": 0}

    def production(self):
        return {"production": "unchanged"}

    def guard(self):
        if not self.quality:
            raise ValueError("scrape insufficient")
        return {"lab-redis": {"uid": "redis", "generation": 1, "image": "fixed"}}

    def app(self, action, run_id):
        self.actions.append(action)
        if action == self.fail or (action == "cleanup" and self.recovery_fail):
            raise ValueError("simulated failure")
        if action == "create":
            return {"item_id": 1, "http_calls": 1}
        if action == "cleanup":
            return {"owned_data_removed": True, "redis_ping": True, "http_calls": 4}
        if action == "ping":
            return {"redis_ping": True, "http_calls": 0}
        if action.startswith("delay-"):
            before, db_before = deepcopy(self.counters), deepcopy(self.db)
            delayed = action == "delay-read"
            self.counters["bypass"] = self.counters.get("bypass", 0) + 1
            self.db["count"] += 1
            self.db["seconds"] += 0.76 if delayed else 0.01
            return {"http_status": 200, "counters_before": before, "counters_after": deepcopy(self.counters),
                    "db_before": db_before, "db_after": deepcopy(self.db), "elapsed_seconds": 0.77 if delayed else 0.02,
                    "delay_requested_ms": 750 if delayed else 0, "delay_header_sent": delayed,
                    "bypass_header_sent": True, "http_calls": 3}
        before = deepcopy(self.counters)
        key = {"cold": "miss", "hit": "hit", "fault-read": "error"}[action]
        self.counters[key] = self.counters.get(key, 0) + 1
        return {"http_status": 200, "counters_before": before, "counters_after": deepcopy(self.counters), "http_calls": 3}

    def scale_redis(self, replicas):
        self.actions.append("scale-" + str(replicas))
        self.replicas = replicas

    def wait_redis(self, replicas):
        self.actions.append("wait-" + str(replicas))

    def collect(self, run_id):
        self.actions.append("collect")
        if self.fail == "collect":
            raise ValueError("failed collection")
        if "delay-read" in self.actions:
            return {"hypotheses": self.hypotheses, "current_state": "unknown",
                    "metric_samples": {"db_latency": [{"outcome": "p99", "value": 0.99}]},
                    "fact_evidence_ids": [["ev-metric-db_latency"]]}
        fault = self.counters.get("error", 0) > 0
        return {"hypotheses": 1 if fault else self.hypotheses,
                "diagnosis_status": "suspected_cause" if fault else "insufficient_evidence",
                "metric_samples": {"cache_outcomes": [{"outcome": "error", "value": 2}]} if fault else {},
                "hypothesis_evidence_ids": [["ev-metric-cache_outcomes"]] if fault else []}


class B6ScenariosTest(unittest.TestCase):
    def run_scenario(self, scenario, lab, **kwargs):
        return execute(scenario, lab, sleep=lambda _: None, run_id="1" * 32, **kwargs)

    def test_missing_counter_is_not_a_numeric_zero(self):
        self.assertFalse(advanced({}, {}, "miss"))
        self.assertFalse(advanced({}, {"miss": 0}, "miss"))
        self.assertTrue(advanced({}, {"miss": 1}, "miss"))
        self.assertTrue(advanced({"miss": 3}, {"miss": 4}, "miss"))

    def test_s2_uses_no_redis_scale_and_removes_owned_data(self):
        lab = FakeLab()
        checkpoint = Mock()
        result = self.run_scenario("S2", lab, checkpoint=checkpoint)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["http_calls"], 17)
        self.assertFalse(any(a.startswith("scale") for a in lab.actions))
        self.assertTrue(result["cleanup"]["owned_data_removed"])
        checkpoint.assert_called_once()

    def test_s1_restores_redis_and_data_when_collection_fails(self):
        lab = FakeLab(fail="collect")
        result = self.run_scenario("S1", lab)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(lab.replicas, 1)
        self.assertTrue(all(result["cleanup"].values()))
        self.assertLess(lab.actions.index("scale-1"), lab.actions.index("cleanup"))

    def test_cleanup_runs_even_when_creation_response_is_lost(self):
        lab = FakeLab(fail="create")
        result = self.run_scenario("S2", lab)
        self.assertEqual(result["status"], "failed")
        self.assertIn("cleanup", lab.actions)

    def test_s1_pass_has_bounded_requests_and_error_supported_report(self):
        result = self.run_scenario("S1", FakeLab())
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["http_calls"], 20)
        self.assertEqual(result["report_check"], "cache_error_supported")

    def test_recovery_failure_is_never_success(self):
        result = self.run_scenario("S2", FakeLab(recovery_fail=True))
        self.assertEqual(result["status"], "recovery_failed")

    def test_insufficient_preflight_never_creates_or_scales(self):
        lab = FakeLab()
        lab.quality = False
        result = self.run_scenario("S1", lab)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(lab.actions, [])

    def test_false_cause_is_needs_review_even_if_intervention_and_recovery_pass(self):
        result = self.run_scenario("S2", FakeLab(hypotheses=1))
        self.assertEqual(result["status"], "needs_review")

    def test_replaced_redis_uid_is_never_scaled(self):
        client = LabClient()
        client.redis_uid = "original"
        with patch.object(client, "get", return_value={"metadata": {"uid": "replaced"}}), \
                patch.object(client, "command") as command:
            with self.assertRaisesRegex(ValueError, "다른 Redis"):
                client.scale_redis(0)
        command.assert_not_called()

    def test_ground_truth_is_recorded_before_investigation(self):
        lab = FakeLab()
        recorded = []
        original_collect = lab.collect
        def collect(run_id):
            self.assertEqual(recorded, ["truth"])
            return original_collect(run_id)
        lab.collect = collect
        result = self.run_scenario("S2", lab, checkpoint=lambda _: recorded.append("truth"))
        self.assertEqual(result["status"], "passed")

    def test_s4_verifies_db_delay_and_recovers_without_redis_changes(self):
        lab = FakeLab()
        checkpoint = Mock()
        result = self.run_scenario("S4", lab, checkpoint=checkpoint)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["http_calls"], 20)
        self.assertTrue(result["cleanup"]["delay_disabled_and_latency_recovered"])
        self.assertFalse(result["recovery_observation"]["delay_header_sent"])
        self.assertFalse(any(a.startswith("scale") for a in lab.actions))
        self.assertLess(lab.actions.index("collect"), lab.actions.index("delay-recovered"))
        self.assertEqual(checkpoint.call_args.args[0]["status"], "observations_recorded")
        self.assertFalse(checkpoint.call_args.args[0]["execution_status_final"])

    def test_s4_collection_failure_still_checks_latency_recovery_and_deletes_data(self):
        lab = FakeLab(fail="collect")
        result = self.run_scenario("S4", lab)
        self.assertEqual(result["status"], "failed")
        self.assertIn("delay-recovered", lab.actions)
        self.assertIn("cleanup", lab.actions)
        self.assertTrue(all(result["cleanup"].values()))

    def test_s4_ignored_delay_is_not_success_and_still_recovers(self):
        lab = FakeLab()
        original = lab.app
        def app(action, run_id):
            read = original(action, run_id)
            if action == "delay-read":
                read["elapsed_seconds"] = 0.01
            return read
        lab.app = app
        result = self.run_scenario("S4", lab)
        self.assertEqual(result["status"], "failed")
        self.assertIn("delay-recovered", lab.actions)
        self.assertNotIn("collect", lab.actions)

    def test_s4_recovery_failure_blocks_success_even_when_report_passes(self):
        result = self.run_scenario("S4", FakeLab(fail="delay-recovered"))
        self.assertEqual(result["status"], "recovery_failed")
        self.assertTrue(result["cleanup"]["owned_data_removed"])

    def test_s4_missing_latency_fact_is_reviewed_even_with_actual_delay(self):
        lab = FakeLab()
        original = lab.collect
        def collect(run_id):
            report = original(run_id)
            report["fact_evidence_ids"] = []
            return report
        lab.collect = collect
        result = self.run_scenario("S4", lab)
        self.assertEqual(result["status"], "needs_review")

    def test_s4_cannot_claim_database_server_cause_from_app_latency(self):
        result = self.run_scenario("S4", FakeLab(hypotheses=1))
        self.assertEqual(result["status"], "needs_review")

    def test_s4_rejects_wrong_delay_header_and_absent_db_histogram(self):
        lab = FakeLab()
        baseline = lab.app("delay-baseline", "1"*32)
        read = lab.app("delay-read", "1"*32)
        for updates in ({"delay_requested_ms": 1500}, {"db_after": {}}, {"bypass_header_sent": False}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                check_delay({**read, **updates}, delayed=True, baseline=baseline)


class ScenarioControlTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.output = self.root / "result.json"
        script = Path(__file__).resolve().parents[1] / "scripts/run_b6_scenario.py"
        spec = importlib.util.spec_from_file_location("run_lab_scenario", script)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.args = [str(script), "--scenario", "S1", "--state-dir", str(self.state), "--output", str(self.output)]

    def assert_blocked(self):
        with patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)}), \
                patch("sys.argv", self.args), patch.object(self.module, "LabClient") as client, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.module.main()
        client.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_unrecovered_previous_run_blocks_without_cluster_call(self):
        (self.state / "control.json").write_text(json.dumps({"active": True, "recovery_complete": False}))
        self.assert_blocked()

    def test_previous_metric_window_blocks_without_cluster_call(self):
        (self.state / "control.json").write_text(json.dumps({"active": False, "recovery_complete": True, "not_before": time.time()+300}))
        self.assert_blocked()

    def test_concurrent_run_is_rejected(self):
        with (self.state / ".scenario-lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_blocked()

    def test_control_symlink_cannot_overwrite_another_file(self):
        target = self.root / "untouched.json"
        target.write_text('{"untouched":true}')
        (self.state / "control.json").symlink_to(target)
        self.assert_blocked()
        self.assertEqual(target.read_text(), '{"untouched":true}')
