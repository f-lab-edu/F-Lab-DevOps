"""고정 Pod 수집·시각 결합·로그 제한과 실패 시 자기 데이터 정리를 확인한다."""

from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import Mock, patch

from ai_ops.b6_sequence_capture import (RequestObservation, SequenceLabClient,
                                       bind_reads, capture_sequence)
from test_live_collectors import AT, stamp


class FakeSequenceClient:
    def __init__(self, fail=None, recovery_fail=False):
        self.command = Mock()
        self.fail = fail
        self.recovery_fail = recovery_fail
        self.actions = []
        self.counters = {}
        self.lines = []

    def guard(self):
        if self.recovery_fail and "cleanup" in self.actions:
            raise ValueError("baseline changed")
        return {"app": {"pod_uid": "pod-1"}, "production": "unchanged"}

    def app(self, action, run_id):
        self.actions.append(action)
        if action == self.fail:
            raise ValueError("response lost")
        if action == "create":
            return {"created": True, "redis_ping": True, "item_id": 42}
        if action == "cleanup":
            return {"owned_data_removed": True, "redis_ping": True}
        n = len(self.lines)
        start = AT + timedelta(seconds=n)
        outcome = "miss" if action == "cold" else "hit"
        before = deepcopy(self.counters)
        self.counters[outcome] = self.counters.get(outcome, 0) + 1
        self.lines.append(stamp(start+timedelta(milliseconds=15)) +
                          f" INFO:app.api.routes.item:cache_{outcome} endpoint=get_item item_id=42\n")
        return {"started_at": stamp(start), "request_started_at": stamp(start+timedelta(milliseconds=10)),
                "request_finished_at": stamp(start+timedelta(milliseconds=20)),
                "finished_at": stamp(start+timedelta(milliseconds=30)), "http_status": 200,
                "response_item_id": 42, "counters_before": before, "counters_after": deepcopy(self.counters)}

    def logs(self, since):
        self.actions.append("logs")
        if self.fail == "logs":
            return ("".join(self.lines)+self.lines[0]).encode()
        return "".join(self.lines).encode()

    def collect(self, run_id):
        self.actions.append("collect")
        if self.fail == "collect":
            raise ValueError("collection failed")
        return {"incident_id": "inc-"+"1"*32, "run_id": "run-"+"2"*32,
                "bundle_hash": "a"*64, "external_model_blocked": True, "fact_citations_available": True}


class SequenceCaptureTest(unittest.TestCase):
    def execute(self, client, **kwargs):
        lease = Mock()
        lease.acquire.return_value = {"uid": "lease-1"}
        lease.release.return_value = {"released": True}
        result = capture_sequence(client, run_id="3"*32, sleep=lambda _: None,
                                  lease_factory=lambda *args: lease, **kwargs)
        return result, lease

    def test_four_read_source_is_persistable_before_investigation_and_recovered(self):
        client = FakeSequenceClient()
        saved = []
        def checkpoint(value):
            saved.append(deepcopy(value))
            self.assertNotIn("collect", client.actions)
        result, lease = self.execute(client, checkpoint=checkpoint)
        self.assertEqual(result["status"], "captured")
        self.assertEqual([r["log_message"].split()[0] for r in result["reads"]],
                         ["cache_miss", "cache_hit", "cache_miss", "cache_hit"])
        self.assertEqual(len(saved), 2)
        self.assertIn("raw_log_sha256", saved[-1])
        self.assertTrue(all(result["recovery"].values()))
        self.assertEqual(result["external_model_calls"], 0)
        lease.release.assert_called_once_with(recovery_verified=True, scenario_started=True)

    def test_lost_creation_response_logs_failure_and_collection_failure_still_cleanup(self):
        for failure in ("create", "logs", "collect"):
            client = FakeSequenceClient(fail=failure)
            result, lease = self.execute(client)
            with self.subTest(failure=failure):
                self.assertEqual(result["status"], "failed")
                self.assertIn("cleanup", client.actions)
                self.assertTrue(result["recovery"]["owned_data_removed"])
                lease.release.assert_called_once()
                if failure != "collect":
                    self.assertNotIn("collect", client.actions)

    def test_recovery_failure_retains_shared_lock(self):
        result, lease = self.execute(FakeSequenceClient(recovery_fail=True))
        self.assertEqual(result["status"], "recovery_failed")
        lease.release.assert_not_called()

    def test_checkpoint_failure_before_creation_releases_without_cooldown(self):
        client = FakeSequenceClient()
        result, lease = self.execute(client, checkpoint=Mock(side_effect=OSError()))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(client.actions, [])
        lease.release.assert_called_once_with(recovery_verified=True, scenario_started=False)

    def test_legacy_get_record_without_request_timestamps_is_rejected(self):
        client = FakeSequenceClient()
        value = client.app("cold", "3"*32)
        value.pop("request_started_at")
        with self.assertRaises(ValueError):
            RequestObservation.model_validate(value)

    def test_get_and_measurement_times_are_distinct_and_wrong_item_is_rejected(self):
        client = FakeSequenceClient()
        observations = [RequestObservation.model_validate(client.app(action, "3"*32)) for action in ("cold", "hit")]
        raw = "".join(client.lines).encode()
        self.assertEqual([r.outcome for r in bind_reads(observations, raw, 42)], ["miss", "hit"])
        with self.assertRaises(ValueError):
            bind_reads(observations, raw, 43)
        data = observations[0].model_dump(mode="json")
        data["request_finished_at"] = stamp(AT-timedelta(seconds=1))
        with self.assertRaises(ValueError):
            RequestObservation.model_validate(data)

    def test_exec_is_pinned_to_pod_and_replacement_is_rejected(self):
        client = SequenceLabClient()
        client.pinned = {"pod_name": "lab-api-safe", "pod_uid": "pod-1"}
        with patch.object(client, "pin_app", side_effect=[client.pinned, {**client.pinned, "pod_uid": "other"}]), \
                patch.object(client, "command", return_value="{}") as command:
            with self.assertRaises(ValueError):
                client.app("hit", "3"*32)
        self.assertIn("lab-api-safe", command.call_args.args)
        self.assertNotIn("deployment/lab-api", command.call_args.args)

    def test_logs_are_bounded_and_cannot_silently_accept_tail_truncation(self):
        client = SequenceLabClient()
        client.pinned = {"pod_name": "lab-api-safe", "pod_uid": "pod-1"}
        for raw in ("x"*65536, "line\n"*256):
            with self.subTest(size=len(raw)), patch.object(client, "pin_app", return_value=client.pinned), \
                    patch.object(client, "command", return_value=raw) as command:
                with self.assertRaises(ValueError):
                    client.logs(stamp())
                self.assertIn("--tail=257", command.call_args.args)
                self.assertIn("--limit-bytes=65537", command.call_args.args)
                self.assertIn("--timestamps=true", command.call_args.args)


if __name__ == "__main__":
    unittest.main()
