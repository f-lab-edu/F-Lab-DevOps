import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from ai_ops.contracts import (
    AnalysisReport,
    EvidenceBundle,
    EvidenceItem,
    IncidentRequest,
    TargetRegistry,
    TimeWindow,
    build_evidence_bundle,
    create_incident_request,
    load_target_registry,
    parse_evidence_bundle_json,
    parse_incident_request_json,
    sha256_json,
    validate_request_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 9, 27, 1, 0, tzinfo=timezone.utc)


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.registry = load_target_registry(ROOT / "config/targets.example.json")
        self.target = self.registry.targets[0]
        self.window = TimeWindow(
            start="2026-09-27T00:00:00Z", end="2026-09-27T01:00:00Z"
        )
        self.payload = {"cache_miss": 0}
        self.item = EvidenceItem(
            schema_version=1,
            evidence_id="ev-cache-1",
            kind="metric",
            source_ref="metrics/cache.json",
            observed_at="2026-09-27T00:30:00Z",
            collected_at="2026-09-27T00:31:00Z",
            target=self.target,
            time_range=self.window,
            status="available",
            missing_reason=None,
            provenance={"kind": "synthetic", "origin": "contract-test", "fixture_version": "1"},
            payload=self.payload,
            content_hash=sha256_json(self.payload),
            redaction_summary={"categories": [], "count": 0},
        )
        self.bundle = build_evidence_bundle(
            target=self.target,
            window=self.window,
            created_at=AT,
            evidence=[self.item],
        )

    def make_request(self):
        return create_incident_request(
            request_id="request-1",
            target=self.target,
            question="캐시 상태를 조사해줘",
            window=self.window,
            bundle=self.bundle,
            registry=self.registry,
            requested_at=AT,
        )

    def test_registered_request_round_trip_and_generated_id(self):
        request = self.make_request()
        self.assertRegex(request.incident_id, r"^inc-[0-9a-f]{32}$")
        self.assertNotEqual(request.incident_id, self.make_request().incident_id)
        parsed = IncidentRequest.model_validate_json(request.model_dump_json())
        self.assertEqual(parse_incident_request_json(request.model_dump_json().encode()), request)
        self.assertEqual(parse_evidence_bundle_json(self.bundle.model_dump_json().encode()), self.bundle)
        validate_request_bundle(parsed, self.bundle, self.registry)
        self.assertEqual(parsed, request)

    def test_invalid_schema_version_id_and_extra_field_are_rejected(self):
        base = self.make_request().model_dump(mode="json")
        for patch in ({"schema_version": 2}, {"incident_id": "user-chosen"}, {"admin": True}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                IncidentRequest.model_validate({**base, **patch})

    def test_serialized_input_is_bounded_before_parsing(self):
        with self.assertRaisesRegex(ValueError, "16 KiB"):
            parse_incident_request_json(b"x" * (16 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "4 MiB"):
            parse_evidence_bundle_json(b"x" * (4 * 1024 * 1024 + 1))

    def test_bad_target_and_duplicate_registry_are_rejected(self):
        unknown = self.target.model_validate(
            {**self.target.model_dump(mode="json"), "namespace": "other"}
        )
        with self.assertRaisesRegex(ValueError, "not registered"):
            create_incident_request(
                request_id="request-1", target=unknown, question="조사해줘",
                window=self.window, bundle=self.bundle, registry=self.registry, requested_at=AT,
            )
        with self.assertRaises(ValidationError):
            TargetRegistry(schema_version=1, targets=[self.target, self.target])
        invalid = self.target.model_dump()
        invalid["namespace"] = "../other"
        with self.assertRaises(ValidationError):
            self.target.model_validate(invalid)
        invalid["namespace"] = "bad.name"
        with self.assertRaises(ValidationError):
            self.target.model_validate(invalid)

    def test_request_cannot_switch_bundle_or_window(self):
        request = self.make_request()
        base = request.model_dump(mode="json")
        altered = IncidentRequest.model_validate({**base, "bundle_hash": "0" * 64})
        with self.assertRaisesRegex(ValueError, "bundle_hash"):
            validate_request_bundle(altered, self.bundle, self.registry)
        different = TimeWindow(
            start="2026-09-26T23:00:00Z", end="2026-09-27T00:00:00Z"
        )
        altered = IncidentRequest.model_validate(
            {**base, "window": different.model_dump(mode="json")}
        )
        with self.assertRaisesRegex(ValueError, "target or window"):
            validate_request_bundle(altered, self.bundle, self.registry)

    def test_request_question_and_request_id_limits(self):
        base = self.make_request().model_dump(mode="json")
        for bad_question in (" ", "x" * 1001, "a\x00b"):
            with self.subTest(bad_question=bad_question[:10]), self.assertRaises(ValidationError):
                IncidentRequest.model_validate({**base, "question": bad_question})
        with self.assertRaises(ValidationError):
            IncidentRequest.model_validate({**base, "request_id": "../../other"})

    def test_utc_time_and_window_bounds(self):
        invalid = [
            {"start": "2026-09-27T00:00:00", "end": "2026-09-27T01:00:00Z"},
            {"start": "2026-09-27T09:00:00+09:00", "end": "2026-09-27T01:00:00Z"},
            {"start": "2026-09-27T02:00:00Z", "end": "2026-09-27T01:00:00Z"},
            {"start": "2026-09-27T00:00:00Z", "end": "2026-09-28T00:00:01Z"},
            {"start": 1790467200, "end": "2026-09-27T01:00:00Z"},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                TimeWindow.model_validate(value)

    def test_missing_evidence_is_distinct_from_zero(self):
        self.assertEqual(self.item.payload["cache_miss"], 0)
        missing = self.item.model_dump(mode="json")
        missing.update(
            evidence_id="ev-cache-missing", status="missing", observed_at=None,
            missing_reason="collector did not receive a sample", payload=None, content_hash=None,
        )
        entry = EvidenceItem.model_validate(missing)
        self.assertIsNone(entry.payload)
        with self.assertRaises(ValidationError):
            EvidenceItem.model_validate({**missing, "missing_reason": None})
        with self.assertRaises(ValidationError):
            EvidenceItem.model_validate({**missing, "status": "available"})

    def test_payload_and_bundle_hash_are_checked(self):
        changed = self.item.model_dump(mode="json")
        changed["payload"] = {"cache_miss": 1}
        with self.assertRaises(ValidationError):
            EvidenceItem.model_validate(changed)
        changed = self.bundle.model_dump(mode="json")
        changed["evidence"][0]["payload"]["cache_miss"] = 1
        with self.assertRaises(ValidationError):
            EvidenceBundle.model_validate(changed)
        changed = self.bundle.model_dump(mode="json")
        changed["bundle_hash"] = "0" * 64
        with self.assertRaises(ValidationError):
            EvidenceBundle.model_validate(changed)

    def test_payload_must_be_finite_json_and_bounded(self):
        base = self.item.model_dump(mode="json")
        for payload in ({"x": float("nan")}, {"x": "a" * (65 * 1024)}):
            changed = copy.deepcopy(base)
            changed["payload"] = payload
            changed["content_hash"] = "0" * 64
            with self.subTest(payload=str(payload)[:10]), self.assertRaises(ValidationError):
                EvidenceItem.model_validate(changed)

    def test_source_ref_and_duplicate_evidence_are_rejected(self):
        base = self.item.model_dump(mode="json")
        for source_ref in ("../secret", "/etc/passwd", "folder\\secret", "https://example.com/x"):
            with self.subTest(source_ref=source_ref), self.assertRaises(ValidationError):
                EvidenceItem.model_validate({**base, "source_ref": source_ref})
        with self.assertRaises(ValidationError):
            build_evidence_bundle(
                target=self.target, window=self.window, created_at=AT,
                evidence=[self.item, self.item],
            )

    def test_registry_file_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "targets.json"
            path.write_bytes(b"x" * (32 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "32 KiB"):
                load_target_registry(path)

    def test_report_shape_limits_and_no_execution(self):
        report = AnalysisReport(
            schema_version=1, incident_id=self.make_request().incident_id,
            run_id="run-" + "a" * 32, input_hash=self.bundle.bundle_hash,
            target=self.target, window=self.window, analysis_status="complete",
            diagnosis_status="no_incident", current_state="healthy",
            facts=[{"statement": "miss 값은 0", "evidence_ids": ["ev-cache-1"]}],
            execution={"mode": "rules", "cost_usd": None},
        )
        self.assertEqual(AnalysisReport.model_validate_json(report.model_dump_json()), report)
        base = report.model_dump(mode="json")
        with self.assertRaises(ValidationError):
            AnalysisReport.model_validate({**base, "hypotheses": [{"cause": "guess"}]})
        with self.assertRaises(ValidationError):
            AnalysisReport.model_validate({**base, "suggested_actions": [
                {"description": "restart", "execution_status": "executed"}
            ]})

    def test_schema_files_match_models(self):
        from ai_ops.contracts import EvidenceBundle, IncidentRequest, AnalysisReport

        for name, model in (
            ("incident-request", IncidentRequest),
            ("evidence-bundle", EvidenceBundle),
            ("analysis-report", AnalysisReport),
        ):
            with self.subTest(name=name):
                expected = model.model_json_schema()
                actual = json.loads((ROOT / "schemas" / (name + ".schema.json")).read_text())
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
