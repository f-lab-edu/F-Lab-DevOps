"""정제 입력의 출처·허용 필드·익명화·시간·반출 경계를 검증한다."""

import copy
from dataclasses import replace
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from ai_ops.b_contracts import BindingRegistry, load_b_settings
from ai_ops.collection import CapturedSnapshot, ReadFailure, SnapshotBuilder
from ai_ops.contracts import build_evidence_bundle, canonical_json_bytes, sha256_json
from ai_ops.data_policy import ModelDataDenied, ensure_external_model_allowed
from ai_ops.live_collectors.common import add
from ai_ops.model_projection import (ModelView, Projection, SourceIdentity, TARGET,
                                     parse_projection, project_snapshot)
from ai_ops.projected_model_engine import CallPlan, request_templates
from test_live_collectors import AT, Clock, stamp


ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "PRIVATE_SOURCE_SENTINEL"


def example_snapshot():
    settings = load_b_settings(ROOT / "config/targets.practice.example.json",
        ROOT / "config/b-runtime.example.json", ROOT / "config/metric-queries.json")
    bindings = settings.bindings.model_dump(mode="json")
    bindings["bindings"][0]["target"] = dict(TARGET)
    settings = replace(settings, bindings=BindingRegistry.model_validate(bindings))
    clock = Clock(); builder = SnapshotBuilder(settings, settings.bindings.bindings[0].target, clock=clock)
    builder.workload_expected_pods = 1
    add(builder, "deployment", "deployment", at=stamp(), payload={"image": SENTINEL+":v1", "status": "unknown"},
        context={"resource_uid": "deployment-1", "resource_version": "1"})
    add(builder, "pod-status", "workload_status", at=stamp(), payload={"image": SENTINEL+":v1",
        "phase": "Running", "ready": True, "restarts": 0}, context={"resource_uid": "pod-1",
        "resource_version": "1", "deployment_uid": "deployment-1", "replicaset_uid": "rs-1"})
    add(builder, "pod-config", "config_summary", at=stamp(), payload={"read_engine_role": "unknown",
        "redis_url_configured": False}, context={"resource_uid": "pod-1", "resource_version": "1"})
    add(builder, "cache", "metric", at=stamp(), payload={"query_id": "cache_outcomes", "unit": "count",
        "samples": [{"at": stamp(), "outcome": "miss", "value": 2}, {"at": stamp(), "outcome": "hit", "value": 3}]})
    add(builder, "api-missing", "metric", failure=ReadFailure("missing", "no_traffic"))
    add(builder, "event", "event", at=stamp(), time_field="eventTime", payload={"reason": "Started",
        "type": "Normal", "message": SENTINEL, "involved_uid": "pod-1"})
    clock.elapsed = 1
    snapshot = builder.finish()
    identity = SourceIdentity(cluster_fingerprint="a"*64, deployment_uid="deployment-1",
        config_hash=settings.config_hash, incident_id="inc-"+"1"*32, run_id="run-"+"2"*32,
        exporter_sha256="b"*64)
    return snapshot, settings, identity


def example_projection():
    return project_snapshot(*example_snapshot())


def example_plan():
    return CallPlan(account_label="test", price={"checked_on": AT.date().isoformat(),
        "source": "https://developers.openai.com/api/docs/pricing", "input_per_million": 2.0,
        "output_per_million": 10.0})


class ModelProjectionTest(unittest.TestCase):
    def test_actual_fields_selected_without_source_identity_and_source_unchanged(self):
        snapshot, settings, identity = example_snapshot()
        before = canonical_json_bytes(snapshot.bundle.model_dump(mode="json"))
        projection = project_snapshot(snapshot, settings, identity)
        sent = canonical_json_bytes(request_templates(projection.view, example_plan()))
        for secret in (SENTINEL, "deployment-1", "urlshortener", "ai-ops-lab", "ev-pod-status", "source_ref"):
            self.assertNotIn(secret.encode(), sent)
        self.assertEqual(before, canonical_json_bytes(snapshot.bundle.model_dump(mode="json")))
        self.assertEqual(projection.view.observed_pods, 1)
        missing = next(i for i in projection.view.items if i.source_status == "missing")
        self.assertIsNone(missing.payload)
        self.assertEqual(missing.reason, "no_traffic")
        self.assertEqual(sum(e.count for e in projection.view.excluded), 2)
        config = next(i.payload for i in projection.view.items if i.kind == "config_summary")
        self.assertFalse(config.redis_url_configured)
        self.assertIsNone(config.cache_intended_enabled)
        self.assertEqual(parse_projection(canonical_json_bytes(projection.model_dump(mode="json"))), projection)
        with self.assertRaises(ModelDataDenied):
            ensure_external_model_allowed(snapshot.bundle)

    def test_different_target_config_owner_and_tampered_audit_rejected(self):
        snapshot, settings, identity = example_snapshot()
        for changed in (identity.model_copy(update={"deployment_uid": "other"}),
                        identity.model_copy(update={"config_hash": "c"*64})):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                project_snapshot(snapshot, settings, changed)
        audit = snapshot.audit.model_copy(deep=True)
        next(e for e in audit.entries if e.evidence_id == "ev-pod-config").resource_uid = "other-pod"
        with self.assertRaises(ValueError):
            project_snapshot(CapturedSnapshot(snapshot.bundle, audit), settings, identity)
        data = snapshot.bundle.target.model_dump(); data["environment"] = "production"
        from ai_ops.contracts import Target
        target = Target.model_validate(data)
        other = build_evidence_bundle(target=target, window=snapshot.bundle.window,
            created_at=snapshot.bundle.created_at, evidence=[i.model_copy(update={"target": target}) for i in snapshot.bundle.evidence])
        with self.assertRaises(ValueError):
            project_snapshot(CapturedSnapshot(other, snapshot.audit), settings, identity)

    def test_unknown_fields_boolean_numbers_nan_and_hash_changes_rejected(self):
        original = example_projection().model_dump(mode="json")
        for mutate in (lambda x: x["view"].update(api_key=SENTINEL),
                       lambda x: x["view"]["items"][0]["payload"].update(image=SENTINEL),
                       lambda x: x["view"]["items"][0]["payload"].update(restarts=True),
                       lambda x: x["view"]["items"][2]["payload"]["samples"][0].update(value=float("nan")),
                       lambda x: x.update(view_hash="0"*64),
                       lambda x: x["mapping"][0].update(source_id="ev-other"),
                       lambda x: x.update(source_count=255)):
            raw = copy.deepcopy(original); mutate(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                Projection.model_validate_json(json.dumps(raw))

    def test_view_limits_time_availability_and_untrusted_strings_rejected(self):
        original = example_projection().view.model_dump(mode="json")
        for mutate in (lambda x: x["items"].extend(x["items"]*20),
                       lambda x: x["items"][0].update(observed_at="2026-10-16T10:00:00+09:00"),
                       lambda x: x["items"][0].update(source_status="missing"),
                       lambda x: x["items"][2]["payload"].update(dimension="route=/private-user"),
                       lambda x: x["items"][2]["payload"]["samples"][0].update(outcome="evil")):
            raw = copy.deepcopy(original); mutate(raw)
            with self.assertRaises(ValueError):
                ModelView.model_validate_json(json.dumps(raw))

    def test_exporter_has_no_raw_snapshot_output_and_checks_current_identity(self):
        spec = importlib.util.spec_from_file_location("projection_export_script", ROOT/"scripts/prepare_model_projection.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(module, "current_identity", return_value={"cluster_fingerprint": "c"*64}):
            # 코드 해시가 맞지 않으면 kubectl exec 이전에 차단한다.
            execute = unittest.mock.Mock()
            with self.assertRaises(ValueError):
                module.prepare_export(example_snapshot()[2],
                    "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops@sha256:"+"a"*64,
                    execute=execute)
            execute.assert_not_called()
        program = module.exporter_program("pass", example_snapshot()[2].model_dump(mode="json"))
        self.assertIn("print(result.model_dump_json())", program)
        self.assertNotIn("print(record", program)
        compile(program, "<test-exporter>", "exec")

    def test_export_result_requires_same_identity_before_and_after_read(self):
        spec = importlib.util.spec_from_file_location("projection_export_roundtrip", ROOT/"scripts/prepare_model_projection.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        snapshot, settings, identity = example_snapshot()
        identity = identity.model_copy(update={"exporter_sha256": module.exporter_hash()})
        projection = project_snapshot(snapshot, settings, identity)
        current = {"cluster_fingerprint": identity.cluster_fingerprint,
                   "deployment_uid": identity.deployment_uid, "pod_name": "lab-collector-safe"}
        executed = []
        def execute(args, **kwargs):
            executed.append(args)
            self.assertIn("lab-collector-safe", args)
            return canonical_json_bytes(projection.model_dump(mode="json"))
        image = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops@sha256:"+"a"*64
        with patch.object(module, "current_identity", side_effect=[current, current]):
            self.assertEqual(module.prepare_export(identity, image, execute=execute), projection)
        with patch.object(module, "current_identity", side_effect=[current, {**current, "pod_name": "replacement"}]), self.assertRaises(ValueError):
            module.prepare_export(identity, image, execute=execute)
        self.assertEqual(len(executed), 2)

    def test_request_size_limit_rejects_before_approval_or_transport(self):
        with patch("ai_ops.projected_model_engine.INSTRUCTIONS", "가"*32000), self.assertRaises(ValueError):
            request_templates(example_projection().view, example_plan())


if __name__ == "__main__":
    unittest.main()
