"""v3 exporter의 미복구 입력·현재 대상 교체·반출 결합을 검증한다."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from ai_ops.contracts import canonical_json_bytes
from test_model_sequence_projection import case, project


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_sequence_projection as exporter


def fixture():
    snapshot, settings, base, probe, raw = case()
    base = base.model_copy(update={"identity": base.identity.model_copy(update={"exporter_sha256": exporter.exporter_hash()})})
    projection = project(snapshot, settings, base, probe, raw)
    app = {"deployment_uid": probe.deployment_uid, "pod_uid": probe.pod_uid,
           "image": "PRIVATE_SOURCE_SENTINEL:v1"}
    observations = []
    for read in probe.reads:
        value = read.model_dump(mode="json", exclude={"log_at", "log_message"})
        value.update(request_started_at=value["started_at"], request_finished_at=value["finished_at"])
        observations.append(value)
    capture = {"status": "captured", "external_model_calls": 0,
        "recovery": {"owned_data_removed": True, "baseline_unchanged": True},
        "lock_release": {"released": True}, "item_id": probe.item_id,
        "observations": observations, "raw_logs": raw.decode(),
        "reads": [r.model_dump(mode="json") for r in probe.reads],
        "raw_log_sha256": probe.raw_log_sha256, "log_source_hash": probe.log_source_hash,
        "recorded_at": probe.recorded_at.isoformat(),
        "baseline": {"app": app, "lab": {"lab-collector": {"image":
            "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops@sha256:"+"a"*64}}},
        "investigation": {"incident_id": base.identity.incident_id, "run_id": base.identity.run_id,
                          "bundle_hash": base.source_bundle_hash}}
    current = {"cluster_fingerprint": base.identity.cluster_fingerprint,
               "deployment_uid": base.identity.deployment_uid, "pod_name": "lab-collector-safe"}
    return capture, projection, current


class SequenceExporterTest(unittest.TestCase):
    def test_current_identity_and_validated_projection_roundtrip(self):
        capture, projection, current = fixture()
        execute = Mock(return_value=canonical_json_bytes(projection.model_dump(mode="json")))
        client = Mock(); client.pin_app.return_value = capture["baseline"]["app"]
        with patch.object(exporter, "current_identity", return_value=current):
            self.assertEqual(exporter.prepare_export(capture, execute=execute, client=client), projection)
        self.assertIn("lab-collector-safe", execute.call_args.args[0])
        program = execute.call_args.kwargs["data"].decode()
        compile(program, "<sequence-exporter>", "exec")
        self.assertIn("print(result.model_dump_json())", program)
        self.assertNotIn("print(record", program)

    def test_incomplete_recovery_changed_capture_and_nonprivate_image_rejected(self):
        capture, _, _ = fixture()
        for change in (lambda c: c.update(status="failed"),
                       lambda c: c["recovery"].update(owned_data_removed=False),
                       lambda c: c.update(raw_logs=c["raw_logs"]+"invalid"),
                       lambda c: c["baseline"]["lab"]["lab-collector"].update(image="arbitrary:latest")):
            data = deepcopy(capture); change(data)
            execute = Mock()
            with self.subTest(change=change), self.assertRaises(ValueError):
                exporter.prepare_export(data, execute=execute, client=Mock())
            execute.assert_not_called()

    def test_collector_or_app_replacement_prevents_export_acceptance(self):
        capture, projection, current = fixture()
        execute = Mock(return_value=canonical_json_bytes(projection.model_dump(mode="json")))
        client = Mock(); client.pin_app.return_value = capture["baseline"]["app"]
        with patch.object(exporter, "current_identity", side_effect=[current, {**current, "pod_name": "other"}]), self.assertRaises(ValueError):
            exporter.prepare_export(capture, execute=execute, client=client)
        client.pin_app.return_value = {**capture["baseline"]["app"], "pod_uid": "other"}
        execute.reset_mock()
        with patch.object(exporter, "current_identity", return_value=current), self.assertRaises(ValueError):
            exporter.prepare_export(capture, execute=execute, client=client)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
