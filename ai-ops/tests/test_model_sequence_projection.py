"""실습 조회 순서의 출처 결합·익명화·모의 전송 경계를 확인한다."""

import copy
from datetime import timedelta
import hashlib
import unittest

from ai_ops.contracts import build_evidence_bundle, canonical_json_bytes, sha256_json
from ai_ops.collection import CapturedSnapshot
from ai_ops.model_projection import ModelView, Projection, project_snapshot
from ai_ops.model_sequence_projection import (ProbeRead, ProbeRecord,
    SequenceProjection, parse_sequence_projection, probe_log_hash, project_probe)
from ai_ops.projected_model_engine import generate_once
from ai_ops.projected_sequence_model_engine import (request_sequence_templates,
    rules_sequence_projection, validate_sequence_report)
from test_live_collectors import AT, stamp
from test_model_projection import SENTINEL, example_plan, example_snapshot


def case():
    snapshot, settings, identity = example_snapshot()
    base = project_snapshot(snapshot, settings, identity)
    reads = []
    for n, outcome in enumerate(("miss", "hit")):
        start = AT + timedelta(milliseconds=100 + n*300)
        reads.append(ProbeRead(started_at=start, log_at=start+timedelta(milliseconds=20),
            finished_at=start+timedelta(milliseconds=40), http_status=200,
            response_item_id=42, counters_before={} if n == 0 else {"miss": 1},
            counters_after={"miss": 1} if n == 0 else {"miss": 1, "hit": 1},
            log_message=f"cache_{outcome} endpoint=get_item item_id=42"))
    raw = ("\n".join(stamp(read.log_at) + " INFO:app.api.routes.item:" + read.log_message
                     for read in reads) + "\n").encode()
    probe = ProbeRecord(source_bundle_hash=base.source_bundle_hash,
        source_audit_hash=base.source_audit_hash, config_hash=identity.config_hash,
        deployment_uid=identity.deployment_uid, pod_uid="pod-1",
        image_reference_hash=sha256_json(SENTINEL+":v1"), window=base.view.window,
        recorded_at=AT+timedelta(milliseconds=800), item_id=42, reads=reads,
        log_source_hash=probe_log_hash(reads), raw_log_sha256=hashlib.sha256(raw).hexdigest())
    return snapshot, settings, base, probe, raw


def project(snapshot, settings, base, probe, raw):
    return project_probe(base, probe, snapshot, settings, raw_logs=raw,
                         log_pod_uid="pod-1", logs_truncated=False)


class SequenceProjectionTest(unittest.TestCase):
    def test_fixed_view_redacts_private_source_and_retains_old_contract(self):
        snapshot, settings, base, probe, raw = case()
        original = canonical_json_bytes(base.model_dump(mode="json"))
        result = project(snapshot, settings, base, probe, raw)
        view = result.view.model_dump(mode="json")
        sent = canonical_json_bytes(request_sequence_templates(result.view, example_plan()))
        self.assertEqual(view["schema_version"], 3)
        self.assertEqual(view["items"][-1]["payload"]["reads"], [
            {"order": 1, "cache_outcome": "miss", "http_status": 200},
            {"order": 2, "cache_outcome": "hit", "http_status": 200}])
        for private in ("item_id", "pod_uid", "deployment_uid", "source_bundle_hash",
                        "log_message", "cache_miss endpoint", SENTINEL):
            self.assertNotIn(private.encode(), sent)
        self.assertEqual(parse_sequence_projection(canonical_json_bytes(result.model_dump(mode="json"))), result)
        self.assertEqual(canonical_json_bytes(base.model_dump(mode="json")), original)
        with self.assertRaises(ValueError):
            ModelView.model_validate(view)
        self.assertFalse(request_sequence_templates(result.view, example_plan())["/v1/responses"]["store"])

    def test_rejects_mismatched_identity_logs_counters_and_reordering(self):
        snapshot, settings, base, probe, logs = case()
        for change in (
            lambda p: p.update(pod_uid="other-pod"),
            lambda p: p.update(image_reference_hash="0"*64),
            lambda p: p.update(source_bundle_hash="0"*64),
            lambda p: p["reads"][0].update(log_message="cache_miss endpoint=get_item item_id=43"),
            lambda p: p["reads"][0]["counters_after"].update(hit=1),
            lambda p: p["reads"][1].update(response_item_id=43),
            lambda p: p["reads"][1]["counters_before"].update(miss=2),
            lambda p: p["reads"][1].update(log_at=p["reads"][0]["log_at"]),
            lambda p: p["reads"].reverse(),
        ):
            raw = copy.deepcopy(probe.model_dump(mode="json")); change(raw)
            with self.subTest(change=change), self.assertRaises(ValueError):
                project(snapshot, settings, base, ProbeRecord.model_validate(raw), logs)

    def test_missing_and_false_zero_cannot_become_sequence_evidence(self):
        _, _, _, probe, _ = case()
        for change in (
            lambda p: p["reads"][0]["counters_after"].clear(),
            lambda p: p["reads"][0]["counters_before"].update(miss=3),
            lambda p: p["reads"][0]["counters_after"].update(error=0),
            lambda p: p["reads"][0].update(http_status=500),
            lambda p: p["reads"][0].update(http_status=200.0),
            lambda p: p["reads"][0].update(log_message="cache_hit endpoint=get_item item_id=42"),
        ):
            raw = copy.deepcopy(probe.model_dump(mode="json")); change(raw)
            with self.subTest(change=change), self.assertRaises(ValueError):
                ProbeRecord.model_validate(raw)

    def test_bound_view_rejects_private_or_tampered_payload(self):
        snapshot, settings, base, probe, logs = case()
        result = project(snapshot, settings, base, probe, logs)
        raw = result.model_dump(mode="json")
        for change in (
            lambda p: p["view"]["items"][-1]["payload"].update(item_id=42),
            lambda p: p["view"]["items"][-1]["payload"]["reads"][0].update(cache_outcome="hit"),
            lambda p: p.update(probe_hash="0"*64),
            lambda p: p["mapping"][-1].update(source_content_hash="0"*64),
        ):
            modified = copy.deepcopy(raw); change(modified)
            with self.subTest(change=change), self.assertRaises(ValueError):
                SequenceProjection.model_validate(modified)

    def test_missing_duplicate_truncated_or_wrong_pod_logs_rejected(self):
        snapshot, settings, base, probe, logs = case()
        for pod, truncated in (("other-pod", False), ("pod-1", True)):
            with self.subTest(pod=pod, truncated=truncated), self.assertRaises(ValueError):
                project_probe(base, probe, snapshot, settings, raw_logs=logs,
                              log_pod_uid=pod, logs_truncated=truncated)
        lines = logs.splitlines(keepends=True)
        for raw in (lines[0], logs+lines[0], b"".join(reversed(lines)),
                    logs.replace(b"INFO:app.api.routes.item:", b"untrusted:"),
                    logs+b"cache_hit endpoint=get_item item_id=42\n"):
            data = probe.model_dump(mode="json")
            data["raw_log_sha256"] = hashlib.sha256(raw).hexdigest()
            modified = ProbeRecord.model_validate(data)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                project(snapshot, settings, base, modified, raw)

    def test_internally_consistent_but_fabricated_base_view_rejected(self):
        snapshot, settings, base, probe, logs = case()
        raw = base.model_dump(mode="json")
        raw["view"]["items"][0]["payload"]["restarts"] = 9
        raw["view_hash"] = sha256_json(raw["view"])
        fabricated = Projection.model_validate(raw)
        with self.assertRaises(ValueError):
            project(snapshot, settings, fabricated, probe, logs)

    def test_runtime_tag_is_allowed_only_with_matching_deployment_image(self):
        snapshot, settings, base, probe, logs = case()
        items = [item.model_copy(deep=True) for item in snapshot.bundle.evidence]
        pod = next(item for item in items if item.kind == "workload_status")
        pod.payload["image"] = "registry.invalid/app:runtime-tag"
        pod.content_hash = sha256_json(pod.payload)
        bundle = build_evidence_bundle(target=snapshot.bundle.target, window=snapshot.bundle.window,
                                      created_at=snapshot.bundle.created_at, evidence=items)
        snapshot = CapturedSnapshot(bundle, snapshot.audit)
        base = project_snapshot(snapshot, settings, base.identity)
        probe = probe.model_copy(update={"source_bundle_hash": base.source_bundle_hash})
        self.assertEqual(project(snapshot, settings, base, probe, logs).view.schema_version, 3)
        with self.assertRaises(ValueError):
            project(snapshot, settings, base, probe.model_copy(update={"image_reference_hash": "0"*64}), logs)

    def test_mock_generation_cites_only_sequence_and_does_not_send(self):
        snapshot, settings, base, probe, logs = case()
        view = project(snapshot, settings, base, probe, logs).view
        plan = example_plan()
        templates = request_sequence_templates(view, plan)
        calls = []
        def transport(path, body, *, timeout):
            calls.append(path)
            if path == "/v1/responses/input_tokens":
                return {"object": "response.input_tokens", "input_tokens": 1800}
            report = rules_sequence_projection(view)
            return {"status": "completed", "model": plan.model,
                "usage": {"input_tokens": 1800, "output_tokens": 100},
                "output": [{"type": "message", "content": [{"type": "output_text",
                            "text": report.model_dump_json()}]}]}
        draft, usage, model = generate_once(templates, plan, transport, clock=lambda: 0)
        report = validate_sequence_report(draft, view)
        self.assertEqual(calls, ["/v1/responses/input_tokens", "/v1/responses"])
        self.assertEqual(model, "gpt-6-sol")
        self.assertEqual(usage["input_tokens"], 1800)
        self.assertEqual(report.facts[0].evidence_ids, [view.items[-1].evidence_id])
        self.assertIn("miss → hit", report.facts[0].statement)
        self.assertEqual(report.current_state, "unknown")
        self.assertEqual(report.hypotheses, [])


if __name__ == "__main__":
    unittest.main()
