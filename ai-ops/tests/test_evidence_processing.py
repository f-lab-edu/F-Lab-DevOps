"""A2 증거 파일 경계·시간·마스킹·합성 시나리오의 회귀 시험."""

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ai_ops.contracts import load_target_registry, parse_evidence_bundle_json
from ai_ops.evidence import (
    create_sanitized_incident_request,
    load_evidence_bundle,
    normalize_source_time,
    redact_text,
    sanitize_question,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals/fixtures/dev"
SENTINEL = "DEMO_SECRET_SENTINEL"


class EvidenceProcessingTest(unittest.TestCase):
    def setUp(self):
        self.registry = load_target_registry(ROOT / "config/targets.example.json")

    def fixture(self, name):
        return load_evidence_bundle(FIXTURES / name, self.registry)

    def copy_fixture(self, name="s1-cache-error"):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        destination = Path(temp.name) / "bundle"
        shutil.copytree(FIXTURES / name, destination)
        return destination

    def update_manifest(self, directory, update):
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text())
        update(manifest)
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def update_raw_json(self, directory, ref, update):
        path = directory / ref
        value = json.loads(path.read_text())
        update(value)
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        self.update_manifest(directory, lambda m: next(
            e.update(raw_sha256=hashlib.sha256(raw).hexdigest())
            for e in m["entries"] if e["source_ref"] == ref
        ))

    def test_eight_synthetic_cases_preserve_meaning_and_zero(self):
        cases = {path.name: self.fixture(path.name) for path in FIXTURES.iterdir() if path.is_dir()}
        self.assertEqual(len(cases), 8)
        for bundle in cases.values():
            self.assertEqual(parse_evidence_bundle_json(bundle.model_dump_json().encode()), bundle)
            self.assertTrue(all(item.provenance.kind == "synthetic" for item in bundle.evidence))
        s1 = self.fixture("s1-cache-error")
        s2 = self.fixture("s2-normal-miss")
        s9 = self.fixture("s9-primary-consistency")
        self.assertEqual(s1.evidence[0].payload["samples"][0]["outcome"], "error")
        self.assertEqual(s1.evidence[2].status, "permission_denied")
        self.assertIsNone(s1.evidence[2].payload)
        self.assertEqual(s2.evidence[0].payload["samples"][1]["value"], 0)
        self.assertEqual(s2.evidence[0].payload["samples"][0]["outcome"], "miss")
        self.assertEqual(s9.evidence[0].payload["samples"][0]["outcome"], "consistency_primary")
        self.assertEqual(s9.evidence[0].payload["samples"][1]["value"], 0)
        self.assertEqual(cases["s3-image-tag"].evidence[1].payload["reason"], "ImagePullBackOff")
        self.assertEqual(cases["s4-db-latency"].evidence[0].payload["query_id"], "db_latency")
        self.assertTrue(all(item.payload is None for item in cases["s5-insufficient-data"].evidence))
        self.assertEqual(cases["s6-healthy"].evidence[0].payload["restarts"], 0)
        self.assertEqual(
            [item.payload["status"] for item in cases["s10-rollback-recovered"].evidence],
            ["failed", "healthy_after_rollback"],
        )

    def test_kst_and_utc_samples_align_and_preserve_source_time(self):
        bundle = self.fixture("s1-cache-error")
        samples = bundle.evidence[0].payload["samples"]
        self.assertEqual(samples[0]["at"], samples[1]["at"])
        self.assertEqual(samples[0]["at"], "2026-09-28T00:20:00Z")
        self.assertEqual(samples[0]["source_offset"], "+09:00")
        self.assertEqual(samples[1]["source_offset"], "Z")
        self.assertEqual(bundle.evidence[0].observed_at, datetime(2026, 9, 28, 0, 20, tzinfo=timezone.utc))
        self.assertEqual(bundle.evidence[0].payload["source_time"]["observed_raw"], "2026-09-28T09:20:00+09:00")
        with self.assertRaisesRegex(ValueError, "explicit offset"):
            normalize_source_time("2026-09-28T09:20:00")
        with self.assertRaisesRegex(ValueError, "14 hours"):
            normalize_source_time("2026-09-28T09:20:00+15:00")

    def test_unknown_fields_and_secret_sentinel_never_enter_normalized_input(self):
        s1 = self.fixture("s1-cache-error")
        s2 = self.fixture("s2-normal-miss")
        self.assertNotIn(SENTINEL, s1.model_dump_json())
        self.assertNotIn(SENTINEL, s2.model_dump_json())
        self.assertNotIn("ignored_raw_field", s1.model_dump_json())
        self.assertNotIn("redis_url", s2.evidence[1].payload)
        self.assertIn("[REDACTED", s1.evidence[1].payload["lines"][0])
        self.assertEqual(s1.evidence[1].redaction_summary.count, 1)
        question, summary = sanitize_question("조사해줘 token=" + SENTINEL)
        self.assertNotIn(SENTINEL, question)
        self.assertEqual(summary.count, 1)
        output, counts = redact_text("Authorization: Bearer example-token password=" + SENTINEL)
        self.assertNotIn(SENTINEL, output)
        self.assertGreaterEqual(sum(counts.values()), 1)
        output, counts = redact_text('{"password": "' + SENTINEL + '", "endpoint": "mongodb://user:' + SENTINEL + '@example.invalid/db"}')
        self.assertNotIn(SENTINEL, output)
        self.assertGreaterEqual(sum(counts.values()), 2)
        request, question_summary = create_sanitized_incident_request(
            request_id="a2-test", target=s1.target, question="조사해줘 token=" + SENTINEL,
            window=s1.window, bundle=s1, registry=self.registry,
            requested_at=datetime(2026, 9, 28, 1, 10, tzinfo=timezone.utc),
        )
        self.assertNotIn(SENTINEL, request.model_dump_json())
        self.assertEqual(question_summary.count, 1)

    def test_manifest_and_raw_hash_changes_fail(self):
        directory = self.copy_fixture()
        (directory / "logs/cache.log").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        self.update_manifest(directory, lambda m: m["entries"].append(copy.deepcopy(m["entries"][0])))
        with self.assertRaisesRegex(ValueError, "invalid evidence manifest"):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        self.update_manifest(directory, lambda m: m.update(secret=SENTINEL))
        with self.assertRaises(ValueError) as error:
            load_evidence_bundle(directory, self.registry)
        self.assertNotIn(SENTINEL, str(error.exception))

    def test_path_escape_and_symlinks_fail_before_read(self):
        directory = self.copy_fixture()
        self.update_manifest(directory, lambda m: m["entries"][0].update(source_ref="../outside.json"))
        with self.assertRaises(ValueError):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        self.update_manifest(directory, lambda m: m["entries"][0].update(source_ref="/etc/passwd"))
        with self.assertRaises(ValueError):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        path = directory / "logs/cache.log"
        path.unlink()
        path.symlink_to(Path(__file__))
        with self.assertRaisesRegex(ValueError, "cannot be opened safely"):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        shutil.rmtree(directory / "logs")
        (directory / "logs").symlink_to(Path(__file__).parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "cannot be opened safely"):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        alias = directory.parent / "alias"
        alias.symlink_to(directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "root cannot be opened safely"):
            load_evidence_bundle(alias, self.registry)

    def test_oversize_file_is_rejected_before_hash_or_parse(self):
        directory = self.copy_fixture()
        (directory / "logs/cache.log").write_bytes(b"a" * (64 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "size limit"):
            load_evidence_bundle(directory, self.registry)

    def test_ambiguous_or_wrong_order_times_are_rejected(self):
        directory = self.copy_fixture()
        self.update_manifest(directory, lambda m: m["entries"][0].update(observed_at="2026-09-28T09:20:00"))
        with self.assertRaisesRegex(ValueError, "explicit offset"):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        self.update_raw_json(directory, "metrics/cache.json", lambda m: m["samples"][0].update(at="2026-09-28T09:20:00"))
        with self.assertRaisesRegex(ValueError, "explicit offset"):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        self.update_manifest(directory, lambda m: m["entries"][0].update(collected_at="2026-09-28T00:19:00Z"))
        with self.assertRaisesRegex(ValueError, "follows collection"):
            load_evidence_bundle(directory, self.registry)

    def test_status_without_payload_and_zero_are_distinct(self):
        directory = self.copy_fixture("s2-normal-miss")
        def mark_missing(manifest):
            item = manifest["entries"][1]
            item.update(status="stale", observed_at=None, raw_sha256=None, missing_reason="no fresh configuration sample")
        self.update_manifest(directory, mark_missing)
        bundle = load_evidence_bundle(directory, self.registry)
        self.assertEqual(bundle.evidence[1].status, "stale")
        self.assertIsNone(bundle.evidence[1].payload)
        self.assertEqual(bundle.evidence[0].payload["samples"][1]["value"], 0)

    def test_registered_target_and_fixed_metric_queries_are_enforced(self):
        directory = self.copy_fixture()
        self.update_manifest(directory, lambda m: m["target"].update(namespace="another"))
        with self.assertRaisesRegex(ValueError, "not registered"):
            load_evidence_bundle(directory, self.registry)
        directory = self.copy_fixture()
        self.update_raw_json(directory, "metrics/cache.json", lambda m: m.update(query_id="arbitrary_promql"))
        with self.assertRaisesRegex(ValueError, "not allowed"):
            load_evidence_bundle(directory, self.registry)


if __name__ == "__main__":
    unittest.main()
