"""실제 API·모델 없이 응답 형식과 소유 관계·누락·정제 경계를 검증한다."""

import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from ai_ops.b_contracts import BRuntime, ReadRequest, ReadResponse, load_b_settings
from ai_ops.collection import SnapshotBuilder
from ai_ops.contracts import parse_evidence_bundle_json
from ai_ops.live_collectors import collect_snapshot
from ai_ops.live_collectors.kubernetes import collect_kubernetes
from ai_ops.live_collectors.prometheus import collect_prometheus
from ai_ops.live_collectors.transport import HTTPReadTransport


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 10, 16, 1, 0, tzinfo=timezone.utc)
SENTINEL = "PRIVATE_SOURCE_SENTINEL"


def stamp(at=AT):
    return at.isoformat().replace("+00:00", "Z")


class Clock:
    def __init__(self):
        self.elapsed = 0.0

    def now(self):
        return AT + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed


def meta(name, uid, namespace="url-shortener"):
    return {"name": name, "uid": uid, "namespace": namespace, "resourceVersion": "101",
            "labels": {"app": "url-shortener-api"}, "generation": 2}


def owner(kind, name, uid):
    return {"apiVersion": "apps/v1", "kind": kind, "name": name, "uid": uid, "controller": True}


def documents():
    container = {"name": "api", "image": "registry.invalid/app:sha-abc", "env": [
        {"name": "REDIS_URL", "valueFrom": {"secretKeyRef": {"name": "app-secret", "key": "REDIS_URL"}}},
        {"name": "DATABASE_URL", "value": "postgresql://user:" + SENTINEL + "@private.invalid/db"},
    ]}
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta("url-shortener-api", "dep-uid"),
                  "spec": {"selector": {"matchLabels": {"app": "url-shortener-api"}},
                           "template": {"spec": {"containers": [container]}}}}
    service = {"apiVersion": "v1", "kind": "Service", "metadata": meta("url-shortener-svc", "svc-uid"),
               "spec": {"selector": {"app": "url-shortener-api"}}}
    rs = {"apiVersion": "apps/v1", "kind": "ReplicaSet", "metadata": meta("url-shortener-api-123", "rs-uid")}
    rs["metadata"]["ownerReferences"] = [owner("Deployment", "url-shortener-api", "dep-uid")]
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": meta("url-shortener-api-123-abc", "pod-uid"),
           "spec": {"containers": [container]}, "status": {"phase": "Running",
           "conditions": [{"type": "Ready", "status": "True"}],
           "containerStatuses": [{"name": "api", "ready": True, "restartCount": 1, "image": container["image"]}]}}
    pod["metadata"]["ownerReferences"] = [owner("ReplicaSet", "url-shortener-api-123", "rs-uid")]
    event = {"apiVersion": "v1", "kind": "Event", "metadata": meta("pod-event", "event-uid"),
             "involvedObject": {"namespace": "url-shortener", "kind": "Pod", "name": pod["metadata"]["name"], "uid": "pod-uid"},
             "type": "Warning", "reason": "BackOff", "message": "ImagePullBackOff https://private.invalid/" + SENTINEL,
             "eventTime": "2026-10-16T09:59:59+09:00"}
    source = {"repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git", "path": "url-shortener/url-shortener-chart"}
    destination = {"server": "https://kubernetes.default.svc", "namespace": "url-shortener"}
    app = {"kind": "Application", "metadata": meta("url-shortener", "app-uid", "argocd"),
           "spec": {"source": source, "destination": destination}, "status": {
               "reconciledAt": stamp(AT - timedelta(seconds=10)), "health": {"status": "Healthy"},
               "sync": {"status": "Synced", "revision": "a" * 40, "comparedTo": {"source": source, "destination": destination}}}}
    return {"deployment": deployment, "service": service, "rs": rs, "pod": pod, "event": event, "app": app}


class API:
    def __init__(self, settings, clock):
        self.settings = settings
        self.clock = clock
        self.docs = documents()
        self.calls = []
        self.overrides = {}
        self.metric_overrides = {}
        self.pod_continue = ""
        self.pod_count = 1
        self.revision_change = False
        self.prom_warning = False
        self.quality_down = False
        self.quality_past_failure = False
        self.quality_age = 0
        self.quality_samples = 10
        self.quality_other_scope = False
        self.quality_missing_instance = False
        self.expression_values = {}
        binding = settings.bindings.bindings[0]
        for query in settings.metrics.queries:
            for dimension in ([None, *binding.routes] if query.dimension == "route" else
                              [None, *binding.operations] if query.dimension == "operation" else [None]):
                for outcome, expression in settings.metrics.render(query.query_id, binding, dimension=dimension).items():
                    self.expression_values[expression] = {"ratio": 0.1, "requests": 10, "count": 10,
                                                         "p95": 0.2, "p99": 0.3, "hit": 8.25,
                                                         "miss": 1.75, "error": 0, "unavailable": 0,
                                                         "bypass": 2, "consistency_primary": 3}[outcome]

    def get(self, request, **limits):
        self.calls.append((request, limits))
        self.clock.elapsed += 0.01
        value = self.overrides.get(request.path)
        if value is not None:
            value = value(request) if callable(value) else value
            if isinstance(value, Exception):
                raise value
            if isinstance(value, ReadResponse):
                return value
            return self.response(value)
        path = request.path
        if request.connection == "practice-prometheus":
            return self.prometheus(request)
        if "/deployments/" in path:
            value = copy.deepcopy(self.docs["deployment"])
            if self.revision_change and sum("/deployments/" in call.path for call, _ in self.calls) > 1:
                value["metadata"]["generation"] += 1
            return self.response(value)
        if "/services/" in path:
            return self.response(self.docs["service"])
        if path.endswith("/replicasets"):
            return self.response({"kind": "ReplicaSetList", "metadata": {}, "items": [self.docs["rs"]]})
        if path.endswith("/pods"):
            return self.response({"kind": "PodList", "metadata": {"continue": self.pod_continue},
                                  "items": [copy.deepcopy(self.docs["pod"]) for _ in range(self.pod_count)]})
        if path.endswith("/log"):
            return ReadResponse(200, ("2026-10-16T10:00:00+09:00 RedisError db_route=primary name=" + SENTINEL + " item_id=9282\n").encode())
        if "/pods/" in path:
            return self.response(self.docs["pod"])
        if path.endswith("/events"):
            items = [self.docs["event"]] if dict(request.params)["fieldSelector"] == "involvedObject.uid=pod-uid" else []
            return self.response({"kind": "EventList", "metadata": {}, "items": items})
        if "/applications/" in path:
            return self.response(self.docs["app"])
        raise AssertionError("unexpected API path")

    def response(self, value):
        return ReadResponse(200, json.dumps(value).encode())

    def vector(self, values):
        return self.response({"status": "success", "data": {"resultType": "vector", "result": [
            {"metric": labels, "value": [AT.timestamp(), str(value)]} for labels, value in values]}})

    def prometheus(self, request):
        expression = dict(request.params)["query"]
        if expression in self.metric_overrides:
            return self.response(self.metric_overrides[expression])
        scope = {"namespace": "other" if self.quality_other_scope else "url-shortener", "service": "url-shortener-svc", "instance": "pod:8000"}
        if expression.startswith("up{"):
            return self.vector([(scope, 0 if self.quality_down else 1)])
        if expression.startswith("count_over_time"):
            if self.quality_missing_instance:
                scope["instance"] = "other:8000"
            return self.vector([(scope, self.quality_samples)])
        if expression.startswith("timestamp(up"):
            return self.vector([(scope, AT.timestamp() - self.quality_age)])
        if expression.startswith("min_over_time"):
            return self.vector([(scope, 0 if self.quality_past_failure else 1)])
        value = self.vector([({}, self.expression_values[expression])])
        if self.prom_warning:
            obj = json.loads(value.body)
            obj["warnings"] = [SENTINEL]
            return self.response(obj)
        return value


class LiveCollectorsTest(unittest.TestCase):
    def setUp(self):
        config = ROOT / "config"
        self.settings = load_b_settings(config / "targets.practice.example.json", config / "b-runtime.example.json", config / "metric-queries.json")
        self.target = self.settings.bindings.bindings[0].target
        self.clock = Clock()
        self.api = API(self.settings, self.clock)

    def snapshot(self, **options):
        return collect_snapshot(self.settings, self.target, transport=self.api, clock=self.clock, **options)

    def item(self, snapshot, key):
        return next(item for item in snapshot.bundle.evidence if item.evidence_id == "ev-" + key)

    def test_explicit_empty_redis_env_remains_disabled_when_api_omits_value(self):
        for entry in ({"name":"REDIS_URL"},{"name":"REDIS_URL","value":""}):
            with self.subTest(entry=entry):
                self.api.docs["pod"]["spec"]["containers"][0]["env"]=[entry]
                snapshot=self.snapshot()
                self.assertIs(self.item(snapshot,"pod-0-config").payload["redis_url_configured"],False)

    def test_redis_reference_and_env_from_are_not_assumed_disabled(self):
        for source in ({"secretKeyRef":{"name":"app-secret","key":"REDIS_URL"}},
                       {"configMapKeyRef":{"name":"app-config","key":"REDIS_URL"}}):
            with self.subTest(source=source):
                container=self.api.docs["pod"]["spec"]["containers"][0]
                container["env"]=[{"name":"REDIS_URL","valueFrom":source}]
                self.assertNotIn("redis_url_configured",self.item(self.snapshot(),"pod-0-config").payload)
        container["env"]=[]; container["envFrom"]=[{"secretRef":{"name":"app-secret"}}]
        self.assertNotIn("redis_url_configured",self.item(self.snapshot(),"pod-0-config").payload)

    def test_full_scoped_collection_preserves_meaning_time_and_privacy(self):
        snapshot = self.snapshot(route="/items/{item_id}", operation="select_one")
        self.assertEqual(parse_evidence_bundle_json(snapshot.bundle.model_dump_json().encode()), snapshot.bundle)
        self.assertTrue(self.item(snapshot, "pod-0-status").payload["ready"])
        status_audit = next(entry for entry in snapshot.audit.entries if entry.evidence_id == "ev-pod-0-status")
        self.assertEqual((status_audit.deployment_uid, status_audit.replicaset_uid), ("dep-uid", "rs-uid"))
        self.assertGreater(self.item(snapshot, "pod-0-status").observed_at, AT)
        self.assertLess(self.item(snapshot, "pod-0-status").observed_at, snapshot.bundle.created_at)
        self.assertEqual(self.item(snapshot, "pod-0-log").payload["line_times"][0]["source_offset"], "+09:00")
        self.assertEqual(self.item(snapshot, "event-0").observed_at, AT - timedelta(seconds=1))
        config = self.item(snapshot, "pod-0-config").payload
        self.assertEqual(config["read_engine_role"], "unknown")
        self.assertNotIn("redis_url_configured", config)
        self.assertTrue(next(entry for entry in snapshot.audit.entries if entry.evidence_id == "ev-pod-0-config").redis_secret_reference)
        samples = self.item(snapshot, "metric-cache_outcomes").payload["samples"]
        self.assertEqual(next(sample["value"] for sample in samples if sample["outcome"] == "hit"), 8.25)
        self.assertEqual(next(sample["value"] for sample in samples if sample["outcome"] == "error"), 0)
        self.assertEqual(self.item(snapshot, "deployment-argo").payload["status"], "unknown")
        self.assertEqual(self.item(snapshot, "deployment-history").missing_reason, "missing_history")
        text = snapshot.bundle.model_dump_json() + snapshot.audit.model_dump_json()
        for forbidden in (SENTINEL, "private.invalid", "DATABASE_URL", "9282"):
            self.assertNotIn(forbidden, text)
        logs = [request for request, _ in self.api.calls if request.path.endswith("/log")]
        self.assertEqual([dict(request.params)["previous"] for request in logs], ["false", "true"])
        self.assertTrue(all("/secrets" not in request.path for request, _ in self.api.calls))

    def test_typed_list_items_can_omit_kind_without_weakening_scope(self):
        for key in ("rs", "pod", "event"):
            self.api.docs[key].pop("kind")
            self.api.docs[key].pop("apiVersion")
        snapshot = self.snapshot()
        self.assertEqual(self.item(snapshot, "pod-0-status").status, "available")
        self.assertEqual(self.item(snapshot, "event-0").status, "available")
        self.assertTrue(self.item(snapshot, "pod-0-status").payload["ready"])
        self.assertNotIn("kind", self.api.docs["pod"])
        for key, mutation in (("pod", {"kind": "Secret"}),
                              ("rs", {"kind": "Deployment"})):
            with self.subTest(key=key):
                self.api = API(self.settings, self.clock)
                self.api.docs[key].update(mutation)
                rejected = self.snapshot()
                self.assertEqual(self.item(rejected, "workload-scope").missing_reason, "scope_mismatch")
                self.assertFalse(any(request.path.endswith("/log") for request, _ in self.api.calls))

    def test_foreign_replicaset_pod_and_service_are_rejected_before_logs(self):
        mutations = [lambda api: api.docs["rs"]["metadata"]["ownerReferences"][0].update(uid="other-dep"),
                     lambda api: api.docs["pod"]["metadata"]["ownerReferences"][0].update(uid="other-rs"),
                     lambda api: api.docs["service"]["spec"].update(selector={"app": "other"}),
                     lambda api: api.docs["pod"]["metadata"].update(namespace="other")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.api = API(self.settings, self.clock)
                mutate(self.api)
                builder = SnapshotBuilder(self.settings, self.target, clock=self.clock)
                result = collect_kubernetes(builder, self.api)
                snapshot = builder.finish()
                self.assertFalse(result.trusted)
                self.assertEqual(self.item(snapshot, "workload-scope").missing_reason, "scope_mismatch")
                self.assertFalse(any(request.path.endswith("/log") for request, _ in self.api.calls))

    def test_truncated_or_excess_pods_cannot_be_healthy_evidence(self):
        for continuation, count in (("next-page", 1), ("", 13)):
            self.clock = Clock()
            self.api = API(self.settings, self.clock)
            self.api.pod_continue, self.api.pod_count = continuation, count
            snapshot = self.snapshot()
            self.assertEqual(self.item(snapshot, "workload-scope").missing_reason, "truncated")
            self.assertFalse(any(item.kind == "workload_status" and item.status == "available" for item in snapshot.bundle.evidence))

    def test_rollout_change_invalidates_current_pod_snapshot(self):
        self.api.revision_change = True
        snapshot = self.snapshot()
        self.assertEqual(self.item(snapshot, "workload-scope").missing_reason, "revision_changed")
        self.assertFalse(any(request.path.endswith("/log") for request, _ in self.api.calls))

    def test_pod_not_ready_deleting_and_no_restart_are_preserved(self):
        self.api.docs["pod"]["metadata"]["deletionTimestamp"] = stamp()
        self.api.docs["pod"]["status"]["containerStatuses"][0]["restartCount"] = 0
        snapshot = self.snapshot()
        self.assertFalse(self.item(snapshot, "pod-0-status").payload["ready"])
        self.assertEqual(len([request for request, _ in self.api.calls if request.path.endswith("/log")]), 1)

    def test_log_denied_empty_oversize_and_wrong_timestamp_never_store_body(self):
        path = "/api/v1/namespaces/url-shortener/pods/url-shortener-api-123-abc/log"
        for response, reason in ((ReadResponse(403, SENTINEL.encode()), "permission_denied"),
                                 (ReadResponse(200, b""), "empty_response"),
                                 (ReadResponse(200, b"x" * 65537), "response_too_large"),
                                 (ReadResponse(200, b"no-time RedisError"), "invalid_log_timestamp")):
            with self.subTest(reason=reason):
                self.clock = Clock()
                self.api = API(self.settings, self.clock)
                self.api.overrides[path] = response
                snapshot = self.snapshot()
                self.assertEqual(self.item(snapshot, "pod-0-log").missing_reason, reason)
                self.assertIsNone(self.item(snapshot, "pod-0-log").payload)

    def test_valid_log_without_diagnostic_fields_is_missing_not_invalid(self):
        path = "/api/v1/namespaces/url-shortener/pods/url-shortener-api-123-abc/log"
        self.api.overrides[path] = ReadResponse(200, (stamp() + " INFO user=" + SENTINEL).encode())
        snapshot = self.snapshot()
        item = self.item(snapshot, "pod-0-log")
        self.assertEqual((item.status, item.missing_reason), ("missing", "no_diagnostic_fields"))
        self.assertIsNone(item.payload)
        self.assertNotIn(SENTINEL, snapshot.bundle.model_dump_json())

    def test_non_utf8_logs_have_distinct_reason_and_never_store_body(self):
        path = "/api/v1/namespaces/url-shortener/pods/url-shortener-api-123-abc/log"
        self.api.overrides[path] = ReadResponse(200, (stamp() + " RedisError " + SENTINEL).encode() + bytes([255]))
        snapshot = self.snapshot()
        item = self.item(snapshot, "pod-0-log")
        self.assertEqual((item.status, item.missing_reason), ("invalid", "invalid_log_encoding"))
        self.assertIsNone(item.payload)
        self.assertNotIn(SENTINEL, snapshot.bundle.model_dump_json())

    def test_event_uid_mismatch_and_missing_event_time_are_recorded(self):
        for field in ("owner", "time"):
            self.clock = Clock()
            self.api = API(self.settings, self.clock)
            if field == "owner":
                self.api.docs["event"]["involvedObject"]["uid"] = "foreign-uid"
            else:
                self.api.docs["event"].pop("eventTime")
            snapshot = self.snapshot()
            event_items = [item for item in snapshot.bundle.evidence if item.kind == "event"]
            self.assertTrue(any(item.status == "invalid" for item in event_items))
            self.assertFalse(any(item.status == "available" for item in event_items))

    def test_event_and_log_truncation_are_explicit_and_never_follow_next_page(self):
        path = "/api/v1/namespaces/url-shortener/events"
        self.api.overrides[path] = {"kind": "EventList", "metadata": {"continue": "opaque-next-page"}, "items": []}
        log_path = "/api/v1/namespaces/url-shortener/pods/url-shortener-api-123-abc/log"
        self.api.overrides[log_path] = ReadResponse(200, ("2026-10-16T01:00:00Z RedisError\n" * 200).encode())
        snapshot = self.snapshot()
        self.assertEqual(self.item(snapshot, "pod-0-log").missing_reason, "truncated")
        self.assertEqual(self.item(snapshot, "events-truncated").missing_reason, "truncated")
        self.assertFalse(any("continue" in dict(request.params) for request, _ in self.api.calls))
        self.assertTrue(any(entry.truncated for entry in snapshot.audit.entries))
        self.assertEqual(len([request for request, _ in self.api.calls if request.path.endswith("/log")]), 1)

    def test_reused_pod_name_does_not_receive_previous_uid_logs(self):
        pod = copy.deepcopy(self.api.docs["pod"])
        pod["metadata"]["uid"] = "new-pod-uid"
        self.api.overrides["/api/v1/namespaces/url-shortener/pods/url-shortener-api-123-abc"] = pod
        snapshot = self.snapshot()
        self.assertEqual(self.item(snapshot, "pod-0-log").missing_reason, "revision_changed")
        self.assertIsNone(self.item(snapshot, "pod-0-log").payload)

    def test_scoped_argo_history_keeps_deployment_time_without_inventing_rollback(self):
        app = self.api.docs["app"]
        app["status"]["history"] = [{"source": copy.deepcopy(app["spec"]["source"]),
            "revision": "b" * 40, "deployedAt": "2026-10-16T09:55:00+09:00"}]
        snapshot = self.snapshot()
        item = self.item(snapshot, "deployment-history-0")
        self.assertEqual(item.observed_at, AT - timedelta(minutes=5))
        self.assertEqual(item.payload["status"], "unknown")
        self.assertNotIn("image", item.payload)
        self.assertEqual(item.payload["source_time"]["observed_offset"], "+09:00")
        self.assertEqual(self.item(snapshot, "deployment-ci-history").missing_reason, "missing_history")

    def test_malformed_api_json_and_empty_pod_list_produce_missing_sources(self):
        path = "/apis/apps/v1/namespaces/url-shortener/deployments/url-shortener-api"
        self.api.overrides[path] = ReadResponse(200, b'{"kind":"Deployment","kind":"Secret"}')
        snapshot = self.snapshot()
        self.assertEqual(self.item(snapshot, "workload-scope").status, "invalid")
        self.clock = Clock()
        self.api = API(self.settings, self.clock)
        self.api.pod_count = 0
        snapshot = self.snapshot()
        self.assertEqual(self.item(snapshot, "workload-scope").missing_reason, "empty_response")

    def test_scrape_quality_down_old_sparse_scope_and_series_mismatch_block_metrics(self):
        for option, value in (("quality_down", True), ("quality_past_failure", True), ("quality_age", 61), ("quality_samples", 7),
                              ("quality_other_scope", True), ("quality_missing_instance", True)):
            with self.subTest(option=option):
                self.clock = Clock()
                self.api = API(self.settings, self.clock)
                setattr(self.api, option, value)
                snapshot = self.snapshot()
                metrics = [item for item in snapshot.bundle.evidence if item.kind == "metric"]
                self.assertEqual(len(metrics), 5)
                self.assertTrue(all(item.status != "available" for item in metrics))

    def test_partial_warnings_nan_labels_empty_and_range_are_not_zero(self):
        expression = self.settings.metrics.render("cache_outcomes", self.settings.bindings.bindings[0])["hit"]
        base = {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [AT.timestamp(), "1"]}]}}
        cases = []
        for mutation in (lambda obj: obj.update(warnings=[SENTINEL]),
                         lambda obj: obj["data"]["result"][0]["value"].__setitem__(1, "NaN"),
                         lambda obj: obj["data"]["result"][0].update(metric={"namespace": "foreign"}),
                         lambda obj: obj["data"].update(result=[]),
                         lambda obj: obj["data"].update(resultType="matrix"),
                         lambda obj: obj["data"]["result"][0]["value"].__setitem__(0, AT.timestamp()-1)):
            value = copy.deepcopy(base)
            mutation(value)
            cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                self.clock = Clock()
                self.api = API(self.settings, self.clock)
                self.api.metric_overrides[expression] = value
                snapshot = self.snapshot()
                if value["data"]["result"] == []:
                    item = self.item(snapshot, "metric-cache_outcomes")
                    self.assertFalse(any(sample["outcome"] == "hit" for sample in item.payload["samples"]))
                    self.assertEqual(self.item(snapshot, "metric-cache-outcome-gaps").status, "missing")
                else:
                    self.assertIsNone(self.item(snapshot, "metric-cache_outcomes").payload)
                self.assertNotIn(SENTINEL, snapshot.audit.model_dump_json())

    def test_zero_traffic_is_missing_and_ratio_query_is_skipped(self):
        binding = self.settings.bindings.bindings[0]
        expressions = self.settings.metrics.render("http_error_ratio", binding)
        self.api.expression_values[expressions["requests"]] = 0
        snapshot = self.snapshot()
        self.assertEqual(self.item(snapshot, "metric-http_error_ratio").missing_reason, "no_traffic")
        self.assertFalse(any(dict(request.params).get("query") == expressions["ratio"] for request, _ in self.api.calls))

    def test_argo_mismatch_missing_revision_and_old_reconcile_do_not_claim_recovery(self):
        for mutate in (lambda api: api.docs["app"]["spec"]["destination"].update(namespace="foreign"),
                       lambda api: api.docs["app"]["status"]["sync"].update(revision="main"),
                       lambda api: api.docs["app"]["status"].update(reconciledAt=stamp(AT-timedelta(hours=1)))):
            self.clock = Clock()
            self.api = API(self.settings, self.clock)
            mutate(self.api)
            snapshot = self.snapshot()
            item = self.item(snapshot, "deployment-argo")
            self.assertNotEqual(item.status, "available")
            self.assertFalse(any(item.payload and item.payload.get("status") == "healthy_after_rollback" for item in snapshot.bundle.evidence))

    def test_all_sources_denied_and_bad_dimensions_never_dispatch_model(self):
        self.api.get = lambda request, **limits: ReadResponse(403, SENTINEL.encode())
        snapshot = self.snapshot()
        self.assertEqual(snapshot.audit.collection_status, "unavailable")
        self.assertNotIn(SENTINEL, snapshot.bundle.model_dump_json())
        with self.assertRaises(ValueError):
            self.snapshot(operation='select_one"} or vector(1)')
        with self.assertRaisesRegex(ValueError, "configured explicitly"):
            collect_snapshot(self.settings, self.target)


class FakeHTTPResponse:
    def __init__(self, body=b"{}", headers=None):
        self.status = 200
        self.headers = headers or {}
        self.stream = io.BytesIO(body)
        self.limits = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def read1(self, size):
        self.limits.append(size)
        return self.stream.read(size)


class TransportTest(unittest.TestCase):
    def runtime(self, token_file=None):
        return BRuntime(schema_version=1, connections={"kube": {"kind": "kubernetes", "base_url": "https://cluster.invalid", "token_file": token_file}})

    def test_get_tls_credentials_encoding_and_bounded_read_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text(SENTINEL + "\n")
            transport = HTTPReadTransport(self.runtime(str(token)))
            response = FakeHTTPResponse(b"1234567")
            with patch("ai_ops.live_collectors.transport.build_opener") as build:
                build.return_value.open.return_value = response
                result = transport.get(ReadRequest("kube", "/api/v1/pods", (("labelSelector", "app=api"),)), timeout_seconds=2, max_bytes=4)
                req = build.return_value.open.call_args.args[0]
                self.assertEqual(req.get_method(), "GET")
                self.assertEqual(req.headers["Authorization"], "Bearer " + SENTINEL)
                self.assertIn("app%3Dapi", req.full_url)
                self.assertEqual(len(result.body), 5)
                self.assertNotIn(SENTINEL, repr(result))
                self.assertLessEqual(sum(response.limits), 5)
                self.assertEqual(build.call_args.args[0].proxies, {})
                self.assertIsNone(build.call_args.args[-1].redirect_request(None, None, None, None, None, "https://evil.invalid"))

    def test_redirect_error_body_and_oversize_declared_response_are_not_read(self):
        transport = HTTPReadTransport(self.runtime())
        error_body = io.BytesIO(SENTINEL.encode())
        with patch("ai_ops.live_collectors.transport.build_opener") as build:
            build.return_value.open.side_effect = HTTPError("https://cluster.invalid", 302, "redirect", {}, error_body)
            response = transport.get(ReadRequest("kube", "/api/v1/pods"), timeout_seconds=1, max_bytes=10)
            self.assertEqual(response, ReadResponse(302, b""))
            self.assertTrue(error_body.closed)
        with patch("ai_ops.live_collectors.transport.build_opener") as build:
            http_response = FakeHTTPResponse(SENTINEL.encode(), {"Content-Length": "1000"})
            build.return_value.open.return_value = http_response
            result = transport.get(ReadRequest("kube", "/api/v1/pods"), timeout_seconds=1, max_bytes=10)
            self.assertEqual(len(result.body), 11)
            self.assertFalse(http_response.limits)

    def test_token_newline_injection_and_compressed_or_short_response_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text("first\nsecond")
            with self.assertRaisesRegex(ValueError, "credential"):
                HTTPReadTransport(self.runtime(str(token))).get(ReadRequest("kube", "/api/v1/pods"), timeout_seconds=1, max_bytes=10)
        for headers in ({"Content-Encoding": "gzip"}, {"Content-Length": "9"}):
            with patch("ai_ops.live_collectors.transport.build_opener") as build:
                build.return_value.open.return_value = FakeHTTPResponse(b"{}", headers)
                with self.assertRaises(ValueError):
                    HTTPReadTransport(self.runtime()).get(ReadRequest("kube", "/api/v1/pods"), timeout_seconds=1, max_bytes=10)


if __name__ == "__main__":
    unittest.main()
