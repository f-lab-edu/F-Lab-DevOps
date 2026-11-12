"""실제 읽기 수집과 판정의 연결, 대상 교체·오래된 시계열·포트 정리를 검증한다."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import http.server
import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from ai_ops.c_verification import verify_recovery
from ai_ops.c_verification_collect import (
    AlertDefinition, CollectionPlan, collect, metric_value, valid_body,
)
from ai_ops.c_verification_transport import CollectionError, KubectlReader, CONTEXT


AT = datetime(2026, 11, 11, 4, tzinfo=timezone.utc)
APP_UID, DEP_UID, RS_UID = ("11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333")
POD = "lab-api-abcde-abcde"
DIGEST = "sha256:"+"d"*64


def plan():
    return CollectionPlan(execution={"binding": {"application_uid": APP_UID, "target_uid": DEP_UID},
        "proposal_id": "cprop-"+"a"*32, "proposal_hash": "b"*64, "merge_sha": "c"*40,
        "desired_digest": DIGEST, "sync_verified": True, "service_resolved": False,
        "verified_at": AT-timedelta(seconds=1), "source_hash": "e"*64},
        repository="https://github.com/f-lab-edu/F-Lab-DevOps.git",
        alert=AlertDefinition(name="KubeDeploymentReplicasMismatch", query="synthetic_test_only",
            labels={"severity": "warning"}, duration=900.0, group="synthetic", file="synthetic.rules"))


def objects():
    return {
        "application": {"metadata": {"uid": APP_UID, "name": "lab-target", "namespace": "argocd"},
            "spec": {"source": {"repoURL": plan().repository, "path": "ai-ops/lab", "targetRevision": "c"*40},
                "destination": {"namespace": "ai-ops-lab", "server": "https://kubernetes.default.svc"}},
            "status": {"sync": {"revision": "c"*40, "status": "Synced"}, "health": {"status": "Healthy"},
                "operationState": {"phase": "Succeeded", "syncResult": {"revision": "c"*40}}}},
        "deployment": {"metadata": {"uid": DEP_UID, "name": "lab-api", "namespace": "ai-ops-lab", "generation": 3},
            "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "api", "image": DIGEST}]}}},
            "status": {"observedGeneration": 3, "readyReplicas": 1}},
        "replicasets": {"items": [{"metadata": {"uid": RS_UID,
            "ownerReferences": [{"kind": "Deployment", "uid": DEP_UID, "controller": True}]}}]},
        "pods": {"items": [{"metadata": {"name": POD, "uid": "pod-1",
            "ownerReferences": [{"kind": "ReplicaSet", "uid": RS_UID, "controller": True}]},
            "status": {"conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{"name": "api", "ready": True, "imageID": "repo@"+DIGEST,
                                       "containerID": "container-1", "restartCount": 0}]}}]},
        "prometheus": {"metadata": {"uid": "prom-1", "name": "prometheus-kube-prometheus-stack-prometheus-0",
            "namespace": "monitoring"}, "status": {"conditions": [{"type": "Ready", "status": "True"}]}},
    }


class Reader:
    """합성 응답만 제공하며 fake 시계로 긴 대기 없이 실제 수집 루프를 실행한다."""

    def __init__(self):
        self.at = AT
        self.elapsed = 0
        self.data = objects()
        self.calls = []
        self.http_requests = self.forwards_started = self.closed = 0
        self.stale = False
        self.metric_empty = False
        self.active = False
        self.rule_missing = self.rule_changed = self.incomplete = False
        self.http_status = 200

    def sleep(self, seconds):
        self.elapsed += seconds
        self.at += timedelta(seconds=seconds)

    def get(self, name):
        self.calls.append(name)
        return deepcopy(self.data[name])

    @contextmanager
    def forward(self, namespace, pod, port):
        self.forwards_started += 1
        try:
            yield port
        finally:
            self.closed += 1

    def http(self, port, path):
        self.http_requests += 1
        if path in ("/readyz", "/items"):
            return self.http_status, {"status": "ready"} if path == "/readyz" else [], "f"*64
        if path.startswith("/api/v1/query?"):
            query = parse_qs(urlsplit(path).query)["query"][0]
            labels = {"namespace": "ai-ops-lab", "deployment": "lab-api"}
            if 'service="lab-api"' in query:
                labels = {"namespace": "ai-ops-lab", "service": "lab-api", "pod": POD}
            value = 3.0 if "generation{" in query else 1.0
            if query.startswith("count_over_time("):
                value = 3.0
            if query.startswith("timestamp("):
                value = self.at.timestamp()-(300 if self.stale else 1)
            data = {"resultType": "vector", "result": [] if self.metric_empty else [
                {"metric": labels, "value": [self.at.timestamp(), str(value)]}]}
        else:
            rule = plan().alert.model_dump(exclude={"group", "file"})
            rule.update(type="alerting", health="ok", lastEvaluation=(self.at-timedelta(seconds=1)).isoformat(),
                alerts=[{"labels": {"namespace": "ai-ops-lab", "deployment": "lab-api"}, "state": "firing"}]
                       if self.active else [])
            if self.rule_changed:
                rule["query"] = "different_rule"
            data = {"groups": [{"name": "synthetic", "file": "synthetic.rules", "rules": [] if self.rule_missing else [rule]}]}
            if self.incomplete:
                data["groupNextToken"] = "more"
        return 200, {"status": "success", "data": data}, "f"*64


def run(reader, target=None, enabled=True):
    request, audit = collect(target or plan(), reader, enabled=enabled,
        clock=lambda: reader.at, monotonic=lambda: reader.elapsed, sleep=reader.sleep)
    return verify_recovery(request), audit, request


class CollectorTests(unittest.TestCase):
    def test_complete_reads_resolve_and_close_every_forward(self):
        reader = Reader()
        report, audit, request = run(reader)
        self.assertEqual(report["status"], "resolved")
        self.assertEqual(audit["rounds"], 5)
        self.assertEqual(reader.closed, reader.forwards_started)
        self.assertEqual(request.provenance, "operator_supplied")
        self.assertEqual(audit["workload_changes"], 0)
        self.assertEqual(audit["external_model_calls"], 0)
        self.assertNotIn("created_at", request.model_dump_json())

    def test_preflight_never_forwards_or_reads_prometheus(self):
        reader = Reader()
        report, audit, _ = run(reader, enabled=False)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(reader.calls, ["application", "deployment"])
        self.assertEqual(audit["forwards_started"], 0)

    def test_old_identity_and_missing_time_stop_before_http(self):
        for field in ("uid", "time", "repository"):
            reader = Reader(); target = plan()
            if field == "uid": reader.data["deployment"]["metadata"]["uid"] = "new-deployment"
            if field == "time": target.execution.verified_at = None
            if field == "repository": reader.data["application"]["spec"]["source"]["repoURL"] = "https://example.com/other"
            report, audit, _ = run(reader, target)
            self.assertEqual(report["status"], "inconclusive")
            self.assertEqual(audit["http_requests"], 0)
            self.assertEqual(reader.calls, ["application", "deployment"])

    def test_unconfigured_alert_is_not_automatically_invented(self):
        reader = Reader(); target = plan(); target.alert = None
        report, audit, _ = run(reader, target)
        self.assertEqual(report["status"], "inconclusive")
        self.assertIn("alert_definition_required", audit["reasons"])
        self.assertEqual(reader.forwards_started, 0)

    def test_http_failure_and_firing_alert_are_unresolved(self):
        for kind in ("http", "alert"):
            reader = Reader()
            if kind == "http": reader.http_status = 503
            else: reader.active = True
            report, _, _ = run(reader)
            self.assertEqual(report["status"], "unresolved")

    def test_missing_stale_changed_and_partial_prometheus_data_are_unknown(self):
        for field in ("stale", "metric_empty", "rule_missing", "rule_changed", "incomplete"):
            reader = Reader(); setattr(reader, field, True)
            report, _, _ = run(reader)
            self.assertEqual(report["status"], "inconclusive", field)

    def test_pod_rotation_discards_preceding_successes(self):
        reader = Reader(); original = reader.sleep
        def rotate(seconds):
            original(seconds)
            reader.data["pods"]["items"][0]["metadata"]["uid"] = "new-pod"
        reader.sleep = rotate
        report, audit, request = run(reader)
        self.assertEqual(report["status"], "inconclusive")
        self.assertIn("observation_identity_changed", audit["reasons"])
        self.assertTrue(all(not x.samples for x in request.series))

    def test_foreign_pod_is_not_counted_as_our_healthy_pod(self):
        reader = Reader(); reader.data["replicasets"]["items"] = []
        report, _, _ = run(reader)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(reader.forwards_started, 0)

    def test_revision_mismatch_is_a_measured_failure(self):
        reader = Reader(); reader.data["application"]["status"]["sync"]["revision"] = "f"*40
        self.assertEqual(run(reader)[0]["status"], "unresolved")

    def test_http_body_contract(self):
        self.assertTrue(valid_body("/readyz", {"status": "ready"}))
        self.assertFalse(valid_body("/readyz", {"status": "ok"}))
        row = {"id": 1, "name": "test", "description": None, "created_at": "2026-11-11T00:00:00Z"}
        self.assertTrue(valid_body("/items", [row]))
        row["id"] = True
        self.assertFalse(valid_body("/items", [row]))
        self.assertFalse(valid_body("/items", {"items": []}))

    def test_no_ready_pods_are_a_known_failure_even_when_http_is_missing(self):
        reader = Reader(); reader.data["pods"]["items"] = []
        reader.data["deployment"]["status"]["readyReplicas"] = 0
        self.assertEqual(run(reader)[0]["status"], "unresolved")

    def test_partial_kubernetes_list_is_not_success(self):
        reader = Reader(); reader.data["pods"]["metadata"] = {"continue": "more"}
        report, audit, _ = run(reader)
        self.assertEqual(report["status"], "inconclusive")
        self.assertIn("kubernetes_list_incomplete", audit["reasons"])

    def test_prometheus_recreation_keeps_result_unknown(self):
        reader = Reader(); original = reader.sleep
        def rotate(seconds):
            original(seconds); reader.data["prometheus"]["metadata"]["uid"] = "prom-2"
        reader.sleep = rotate
        self.assertEqual(run(reader)[0]["status"], "inconclusive")

    def test_ambiguous_nonfinite_and_wrong_target_metrics_are_rejected(self):
        class Changed(Reader):
            def http(self, port, path):
                status, value, digest = super().http(port, path)
                if path.startswith("/api/v1/query?") and value["data"]["result"]:
                    points = value["data"]["result"]
                    if self.change == "duplicate": points.append(deepcopy(points[0]))
                    elif self.change == "nonfinite": points[0]["value"][1] = "NaN"
                    else: points[0]["metric"]["namespace"] = "default"
                return status, value, digest
        for change in ("duplicate", "nonfinite", "wrong_target"):
            reader = Changed(); reader.change = change
            self.assertEqual(run(reader)[0]["status"], "inconclusive")

    def test_stale_or_error_rule_evaluation_is_not_clear(self):
        class Changed(Reader):
            def http(self, port, path):
                status, value, digest = super().http(port, path)
                if path.startswith("/api/v1/rules?"):
                    rule = value["data"]["groups"][0]["rules"][0]
                    rule[self.field] = self.changed
                return status, value, digest
        for field, value in (("health", "err"), ("lastEvaluation", (AT-timedelta(hours=1)).isoformat())):
            reader = Changed(); reader.field = field; reader.changed = value
            self.assertEqual(run(reader)[0]["status"], "inconclusive")

    def test_recent_data_before_c03_execution_is_still_unusable(self):
        reader = Reader(); target = plan(); target.execution.verified_at = AT
        report, _, _ = run(reader, target)
        self.assertEqual(report["status"], "inconclusive")

    def test_query_time_is_truncated_before_prometheus_millisecond_conversion(self):
        class Rounded(Reader):
            def http(self, port, path):
                status, value, digest = super().http(port, path)
                if path.startswith("/api/v1/query?"):
                    raw = parse_qs(urlsplit(path).query)["time"][0]
                    query_time = datetime.fromisoformat(raw)
                    self.asserted_time = query_time
                    value["data"]["result"][0]["value"][0] = round(query_time.timestamp()*1000)/1000
                return status, value, digest
        reader = Rounded(); reader.at += timedelta(microseconds=700)
        value = metric_value(reader, 9090, 'up{namespace="ai-ops-lab",service="lab-api"}',
            {"namespace": "ai-ops-lab", "service": "lab-api", "pod": POD}, reader.at, 60)
        self.assertEqual(value, 1)
        self.assertEqual(reader.asserted_time.microsecond, 0)

    def test_missing_updated_history_does_not_clear_the_real_alert(self):
        class Missing(Reader):
            def http(self, port, path):
                status, value, digest = super().http(port, path)
                if path.startswith("/api/v1/query?") and "count_over_time" in path:
                    value["data"]["result"] = []
                return status, value, digest
        self.assertEqual(run(Missing())[0]["status"], "inconclusive")

    def test_collection_has_a_deadline_if_wall_clock_does_not_progress(self):
        reader = Reader()
        request, audit = collect(plan(), reader, enabled=True, clock=lambda: AT,
            monotonic=lambda: reader.elapsed, sleep=reader.sleep)
        self.assertEqual(verify_recovery(request)["status"], "inconclusive")
        self.assertIn("collection_deadline_exceeded", audit["reasons"])


class TransportTests(unittest.TestCase):
    def test_fixed_context_and_get_only(self):
        reader = KubectlReader()
        self.assertEqual(reader._command("get", "pods")[:3], ["kubectl", "--context", CONTEXT])
        with self.assertRaises(KeyError): reader.get("secrets")
        with self.assertRaises(CollectionError):
            with reader.forward("default", "lab-api-any", 8000): pass
        with self.assertRaises(CollectionError): reader.http(8000, "/admin")

    def test_forward_cleanup_after_client_exception(self):
        process = unittest.mock.Mock()
        process.poll.return_value = None
        process.stdout = unittest.mock.Mock()
        reader = KubectlReader()
        with patch("subprocess.Popen", return_value=process), patch(
                "ai_ops.c_verification_transport._chunks", return_value=iter([b"Forwarding from 127.0.0.1:12345 -> 8000\n"])):
            with self.assertRaises(RuntimeError):
                with reader.forward("ai-ops-lab", POD, 8000) as port:
                    self.assertEqual(port, 12345)
                    raise RuntimeError("중간 수집 실패")
        process.terminate.assert_called_once()
        process.stdout.close.assert_called_once()

    def test_loopback_get_does_not_follow_redirect_or_use_environment_proxy(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.paths.append(self.path)
                body = b'{"status":"ready"}'
                self.send_response(302 if self.path == "/items" else 200)
                self.send_header("Location", "http://example.com/forbidden")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)

            def log_message(self, *args): pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.paths = []
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1"}):
                reader = KubectlReader()
                self.assertEqual(reader.http(server.server_port, "/readyz")[:2], (200, {"status": "ready"}))
                self.assertEqual(reader.http(server.server_port, "/items")[0], 302)
            self.assertEqual(server.paths, ["/readyz", "/items"])
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
