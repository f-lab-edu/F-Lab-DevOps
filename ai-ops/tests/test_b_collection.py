"""B 첫 묶음의 대상·시간·크기·개인정보·실패 경계를 모의 읽기로 확인한다."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from ai_ops.b_contracts import (
    BRuntime, BindingRegistry, Connection, MetricCatalog, ReadRequest, ReadResponse, load_b_settings,
)
from ai_ops.collection import CapturedSource, ReadFailure, SnapshotBuilder
from ai_ops.contracts import parse_evidence_bundle_json, sha256_json
from ai_ops.reports import format_kst


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
SENTINEL = "PRIVATE_USER_AND_SECRET_SENTINEL"


class FakeClock:
    def __init__(self):
        self.elapsed = 0

    def now(self):
        return AT + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return float(self.elapsed)


class FakeTransport:
    def __init__(self, response=None, error=None, advance=None):
        self.response = response or ReadResponse(200, b"{}")
        self.error = error
        self.advance = advance
        self.calls = []

    def get(self, request, **limits):
        self.calls.append((request, limits))
        if self.advance:
            self.advance()
        if self.error:
            raise self.error
        return self.response


class BCollectionTest(unittest.TestCase):
    def setUp(self):
        config = ROOT / "config"
        self.settings = load_b_settings(config / "targets.practice.example.json",
                                        config / "b-runtime.example.json", config / "metric-queries.json")
        self.binding = self.settings.bindings.bindings[0]
        self.clock = FakeClock()
        self.builder = SnapshotBuilder(self.settings, self.binding.target, clock=self.clock)

    def source(self, kind="metric", suffix="one", **patch):
        fields = {"metric": "evaluation_time", "log": "log_timestamp", "event": "eventTime",
                  "deployment": "response_received_at", "config_summary": "response_received_at",
                  "workload_status": "response_received_at"}
        return CapturedSource.model_validate({
            "evidence_id": f"ev-{suffix}", "kind": kind,
            "source_ref": f"prometheus/{suffix}.json" if kind == "metric" else f"kubernetes/{suffix}.json",
            "target": self.binding.target, "time_field": fields[kind], **patch,
        })

    def metric(self, **patch):
        return {"query_id": "cache_outcomes", "unit": "count", "samples": [
            {"at": "2026-10-16T10:00:00+09:00", "outcome": "error", "value": 0.25},
            {"at": "2026-10-16T01:00:00Z", "outcome": "miss", "value": 0},
        ], **patch}

    def request(self):
        query = self.settings.metrics.render("cache_outcomes", self.binding)["error"]
        return ReadRequest(self.binding.prometheus_connection, "/api/v1/query",
                           (("query", query), ("time", "2026-10-16T01:00:00Z")))

    def test_examples_are_disabled_bounded_and_reference_only(self):
        self.assertFalse(self.settings.runtime.llm_enabled)
        self.assertEqual(self.settings.runtime.daily_budget_usd, 1.0)
        self.assertTrue(all(connection.base_url is None for connection in self.settings.runtime.connections.values()))
        base = self.settings.runtime.model_dump()
        for patch in ({"llm_enabled": True}, {"daily_budget_usd": 30.0},
                      {"limits": {"max_pods": 13}}, {"limits": {"deadline_seconds": 61}},
                      {"openai_api_key": SENTINEL}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                BRuntime.model_validate({**base, **patch})

    def test_exact_target_and_trusted_connection_scope(self):
        for field, value in (("namespace", "other"), ("environment", "production"), ("service", "other")):
            other = self.binding.target.model_copy(update={field: value})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "not registered"):
                SnapshotBuilder(self.settings, other, clock=self.clock)
        base = self.settings.bindings.model_dump()
        with self.assertRaises(ValidationError):
            BindingRegistry.model_validate({**base, "bindings": base["bindings"] * 2})
        for address in ("https://user:pass@host", "https://host/path", "http://host", "https://host?token=x"):
            with self.subTest(address=address), self.assertRaises(ValidationError):
                Connection(kind="kubernetes", base_url=address)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            runtime = self.settings.runtime.model_dump()
            runtime["connections"].pop(self.binding.kubernetes_connection)
            path.write_text(json.dumps(runtime))
            with self.assertRaisesRegex(ValueError, "absent or wrong-kind"):
                load_b_settings(ROOT / "config/targets.practice.example.json", path, ROOT / "config/metric-queries.json")

    def test_catalog_tampering_and_query_dimension_injection_are_rejected(self):
        raw = self.settings.metrics.model_dump()
        raw["queries"][0]["expressions"]["hit"] += " + vector(0)"
        with self.assertRaisesRegex(ValidationError, "hash mismatch"):
            MetricCatalog.model_validate(raw)
        for query, dimension in (("db_latency", "delete"), ("api_latency", "/items/{item_id}")):
            rendered = self.settings.metrics.render(query, self.binding, dimension=dimension)
            self.assertTrue(all("@" not in value for value in rendered.values()))
            self.assertTrue(all('namespace="url-shortener"' in value for value in rendered.values()))
        for query, dimension in (("arbitrary_promql", None), ("cache_outcomes", "delete"),
                                 ("db_latency", 'select_one"} or vector(1)'), ("api_latency", "/users/private")):
            with self.subTest(query=query, dimension=dimension), self.assertRaises(ValueError):
                self.settings.metrics.render(query, self.binding, dimension=dimension)
        raw = self.settings.metrics.model_dump()
        raw["queries"][0]["expressions"]["hit"] = 'up{namespace="@namespace@",service="@service@",token="@secret@"}'
        raw["content_hash"] = sha256_json({key: value for key, value in raw.items() if key != "content_hash"})
        with self.assertRaises(ValidationError):
            MetricCatalog.model_validate(raw)

    def test_untrusted_url_namespace_resource_and_promql_never_reach_transport(self):
        fake = FakeTransport()
        requests = [ReadRequest("other", "/api/v1/query"),
                    ReadRequest(self.binding.kubernetes_connection, "/api/v1/namespaces/other/pods"),
                    ReadRequest(self.binding.kubernetes_connection, "/api/v1/namespaces/url-shortener/secrets"),
                    ReadRequest(self.binding.prometheus_connection, "/api/v1/query_range"),
                    ReadRequest(self.binding.prometheus_connection, "/api/v1/query", (("query", "up"), ("time", "now")))]
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.builder.read(request, fake)
        for path in ("https://evil.invalid/api/v1/query", "/api/v1/../secrets"):
            with self.assertRaises(ValueError):
                ReadRequest("practice-kubernetes", path)
        self.assertFalse(fake.calls)

    def test_kubernetes_reads_require_bounded_lists_and_non_streaming_logs(self):
        fake = FakeTransport()
        base = "/api/v1/namespaces/url-shortener"
        accepted = [ReadRequest(self.binding.kubernetes_connection, base + "/pods",
                                (("labelSelector", "app=url-shortener-api"), ("limit", "13"))),
                    ReadRequest(self.binding.kubernetes_connection, base + "/pods/url-shortener-api-123/log",
                                (("container", "api"), ("timestamps", "true"), ("follow", "false"),
                                 ("sinceTime", "2026-10-16T00:45:00Z"), ("tailLines", "200"), ("limitBytes", "65536")))]
        for request in accepted:
            self.assertIsInstance(self.builder.read(request, fake), ReadResponse)
        bad_logs = dict(accepted[1].params)
        bad_logs["follow"] = "true"
        for request in (ReadRequest(self.binding.kubernetes_connection, base + "/pods"),
                        ReadRequest(self.binding.kubernetes_connection, accepted[1].path, tuple(bad_logs.items()))):
            with self.assertRaises(ValueError):
                self.builder.read(request, fake)
        self.assertEqual(len(fake.calls), 2)

    def test_permission_timeout_and_error_bodies_are_not_evidence(self):
        for index, (response, error, status, reason) in enumerate([
            (ReadResponse(403, SENTINEL.encode()), None, "permission_denied", "permission_denied"),
            (ReadResponse(404, SENTINEL.encode()), None, "missing", "not_found"),
            (ReadResponse(500, SENTINEL.encode()), None, "missing", "upstream_error"),
            (None, TimeoutError(SENTINEL), "missing", "timeout"),
            (None, RuntimeError(SENTINEL), "missing", "upstream_error"),
        ]):
            fake = FakeTransport(response, error)
            failure = self.builder.read(self.request(), fake)
            self.assertEqual((failure.status, failure.reason), (status, reason))
            item = self.builder.add(self.source(suffix=f"failure-{index}"), observed_at=None, failure=failure)
            self.assertIsNone(item.payload)
            self.assertNotIn(SENTINEL, item.model_dump_json())
            self.assertEqual(fake.calls[0][1], {"timeout_seconds": 5, "max_bytes": 262144})
        snapshot = self.builder.finish()
        self.assertEqual(snapshot.audit.collection_status, "unavailable")
        self.assertNotIn(SENTINEL, snapshot.audit.model_dump_json())
        with self.assertRaises(ValueError):
            ReadFailure("missing", SENTINEL)

    def test_kst_utc_fractional_counts_and_missing_are_preserved(self):
        source = self.source()
        response = ReadResponse(200, json.dumps(self.metric()).encode())
        received = self.builder.read(self.request(), FakeTransport(response))
        self.clock.elapsed = 2
        item = self.builder.add(source, observed_at="2026-10-16T10:00:00+09:00", payload=json.loads(received.body))
        self.builder.add(self.source(suffix="denied"), observed_at=None,
                         failure=ReadFailure("permission_denied", "permission_denied"))
        self.clock.elapsed = 4
        snapshot = self.builder.finish()
        parsed = parse_evidence_bundle_json(snapshot.bundle.model_dump_json().encode())
        self.assertEqual(parsed.evidence[0].observed_at, AT)
        self.assertEqual(parsed.window.end, AT + timedelta(seconds=4))
        self.assertEqual(parsed.evidence[0].time_range, parsed.window)
        self.assertEqual(item.payload["samples"][0]["value"], 0.25)
        self.assertEqual(item.payload["samples"][1]["value"], 0)
        self.assertEqual(item.payload["samples"][0]["source_offset"], "+09:00")
        self.assertIn("10:00:00", format_kst(item.observed_at))
        self.assertEqual(snapshot.audit.collection_status, "partial")
        self.assertEqual(item.provenance.kind, "captured")
        self.assertIsNone(item.provenance.fixture_version)
        self.assertIn(self.settings.config_hash, item.provenance.origin)
        with self.assertRaisesRegex(ValueError, "already frozen"):
            self.builder.add(source, observed_at=None)

    def test_ambiguous_future_stale_and_metric_time_disagreement_drop_payload(self):
        for index, (at, expected) in enumerate([
            (None, "invalid_time"), ("2026-10-16T10:00:00", "invalid_time"),
            ("2026-10-16T01:00:10Z", "future_observation"),
            ("2026-10-16T00:40:00Z", "outside_window"),
        ]):
            item = self.builder.add(self.source(suffix=f"time-{index}"), observed_at=at, payload=self.metric())
            self.assertIsNone(item.payload)
            self.assertEqual(item.missing_reason, expected)
        self.assertTrue(self.builder.entries[2].future_within_tolerance)
        payload = self.metric(samples=[{"at": "2026-10-16T00:59:59Z", "outcome": "error", "value": 1}])
        item = self.builder.add(self.source(suffix="time-mismatch"), observed_at="2026-10-16T01:00:00Z", payload=payload)
        self.assertEqual(item.status, "invalid")

    def test_captured_logs_and_events_keep_only_diagnostic_tokens(self):
        self.clock.elapsed = 1
        item = self.builder.add(self.source("log"), observed_at="2026-10-16T01:00:00Z", payload={
            "container": "api", "lines": ["2026-10-16T10:00:00+09:00 db_route=primary operation=insert name=" + SENTINEL +
                                           " item_id=9182 url=https://private.invalid/user RedisError token=" + SENTINEL],
            "password": SENTINEL,
        })
        event = self.builder.add(self.source("event", "event"), observed_at="2026-10-16T01:00:00Z", payload={
            "reason": "BackOff", "type": "Warning", "involved_uid": "pod-123",
            "message": "ImagePullBackOff https://private.invalid/" + SENTINEL,
        })
        self.assertEqual(item.status, "available")
        self.assertIn("RedisError", item.payload["lines"][0])
        self.assertIn("db_route=primary", item.payload["lines"][0])
        self.assertEqual(item.payload["line_times"][0]["source_offset"], "+09:00")
        self.assertIn("ImagePullBackOff", event.payload["message"])
        snapshot = self.builder.finish()
        for forbidden in (SENTINEL, "private.invalid", "9182", "name=", "item_id="):
            self.assertNotIn(forbidden, snapshot.bundle.model_dump_json())
        self.assertTrue(all(entry.diagnostic_projection for entry in snapshot.audit.entries))

    def test_unknown_logs_and_bad_metrics_are_unavailable_instead_of_healthy_zero(self):
        bad = [self.metric(samples=[]), self.metric(query_id="arbitrary_promql"),
               self.metric(samples=[{"at": "2026-10-16T01:00:00Z", "outcome": "error", "value": float("nan")}]),
               self.metric(samples=self.metric()["samples"] * 2)]
        for index, payload in enumerate(bad):
            item = self.builder.add(self.source(suffix=f"bad-{index}"), observed_at="2026-10-16T01:00:00Z", payload=payload)
            self.assertEqual(item.status, "invalid")
            self.assertIsNone(item.payload)
        item = self.builder.add(self.source("log", "unknown-log"), observed_at="2026-10-16T01:00:00Z",
                                payload={"container": "api", "lines": ["2026-10-16T01:00:00Z name=" + SENTINEL]})
        self.assertEqual((item.status, item.missing_reason), ("missing", "no_diagnostic_fields"))
        self.assertIsNone(item.payload)
        self.assertNotIn(SENTINEL, self.builder.finish().bundle.model_dump_json())

    def test_response_raw_log_payload_bundle_and_deadline_limits(self):
        limits = self.settings.runtime.limits
        limits.response_bytes = 100
        failure = self.builder.read(self.request(), FakeTransport(ReadResponse(200, b"x" * 101)))
        self.assertEqual(failure.reason, "response_too_large")
        limits.total_response_bytes = 101
        self.assertEqual(self.builder.read(self.request(), FakeTransport()).reason, "raw_budget_exceeded")
        limits.response_bytes = 262144
        limits.raw_log_bytes = 10
        item = self.builder.add(self.source("log"), observed_at="2026-10-16T01:00:00Z",
                                payload={"container": "api", "lines": ["2026-10-16T01:00:00Z RedisError"]})
        self.assertEqual(item.missing_reason, "log_budget_exceeded")
        limits.payload_bytes = 10
        item = self.builder.add(self.source(suffix="payload-size"), observed_at="2026-10-16T01:00:00Z", payload=self.metric())
        self.assertEqual(item.missing_reason, "payload_too_large")
        limits.bundle_bytes = 10
        with self.assertRaisesRegex(ValueError, "configured size"):
            self.builder.finish()
        self.clock.elapsed = 60
        fake = FakeTransport()
        self.assertEqual(self.builder.read(self.request(), fake).reason, "deadline_exceeded")
        self.assertFalse(fake.calls)

    def test_late_response_is_discarded_and_source_cannot_change_target_or_path(self):
        fake = FakeTransport(ReadResponse(200, SENTINEL.encode()), advance=lambda: setattr(self.clock, "elapsed", 61))
        failure = self.builder.read(self.request(), fake)
        self.assertEqual(failure.reason, "deadline_exceeded")
        item = self.builder.add(self.source(), observed_at=None, failure=failure)
        self.assertIsNone(item.payload)
        self.assertNotIn(SENTINEL, self.builder.finish().bundle.model_dump_json())
        builder = SnapshotBuilder(self.settings, self.binding.target, clock=FakeClock())
        for patch in ({"source_ref": "../escape.json"},
                      {"target": self.binding.target.model_copy(update={"service": "other"})},
                      {"time_field": "response_received_at"}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                builder.add(self.source(**patch), observed_at=None)

    def test_finishing_after_deadline_preserves_valid_evidence_as_partial(self):
        self.builder.add(self.source(), observed_at="2026-10-16T01:00:00Z", payload=self.metric())
        self.clock.elapsed = 61
        snapshot = self.builder.finish()
        self.assertEqual(snapshot.bundle.evidence[0].status, "available")
        self.assertEqual(snapshot.audit.collection_status, "partial")
        self.assertTrue(snapshot.audit.deadline_exceeded)


if __name__ == "__main__":
    unittest.main()
