"""A2 개발용 합성 증거를 결정적으로 생성하거나 저장된 파일과 비교한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = json.loads((ROOT / "config/targets.example.json").read_text(encoding="utf-8"))["targets"][0]
WINDOW = {"start": "2026-09-28T00:00:00Z", "end": "2026-09-28T01:00:00Z"}


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def entry(evidence_id: str, kind: str, ref: str, raw: bytes | None,
          observed_at: str | None, collected_at: str,
          status: str = "available", reason: str | None = None) -> dict:
    return {
        "evidence_id": evidence_id,
        "kind": kind,
        "source_ref": ref,
        "raw_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
        "observed_at": observed_at,
        "collected_at": collected_at,
        "status": status,
        "missing_reason": reason,
        "provenance": {"kind": "synthetic", "origin": "a2-dev-fixture", "fixture_version": "1"},
    }


def case_files(case: str) -> dict[str, bytes]:
    if case == "s1-cache-error":
        metric = encoded({
            "query_id": "cache_outcomes", "unit": "requests", "samples": [
                {"at": "2026-09-28T09:20:00+09:00", "outcome": "error", "value": 3},
                {"at": "2026-09-28T00:20:00Z", "outcome": "miss", "value": 0},
            ], "ignored_raw_field": "do not pass this field to the model",
        })
        log = b"cache lookup RedisError timeout token=DEMO_SECRET_SENTINEL\nrequest continued via write session\n"
        files = {"metrics/cache.json": metric, "logs/cache.log": log}
        entries = [
            entry("ev-s1-metric", "metric", "metrics/cache.json", metric, "2026-09-28T09:20:00+09:00", "2026-09-28T09:21:00+09:00"),
            entry("ev-s1-log", "log", "logs/cache.log", log, "2026-09-28T00:20:05Z", "2026-09-28T00:21:00Z"),
            entry("ev-s1-event-missing", "event", "events/cache.json", None, None, "2026-09-28T00:22:00Z", "permission_denied", "event read permission was denied"),
        ]
    elif case == "s2-normal-miss":
        metric = encoded({
            "query_id": "cache_outcomes", "unit": "requests", "samples": [
                {"at": "2026-09-28T00:25:00Z", "outcome": "miss", "value": 5},
                {"at": "2026-09-28T09:25:00+09:00", "outcome": "error", "value": 0},
            ],
        })
        config = encoded({"cache_intended_enabled": True, "redis_url_configured": True, "read_engine_role": "shared", "redis_url": "redis://user:DEMO_SECRET_SENTINEL@example.invalid:6379"})
        files = {"metrics/cache.json": metric, "config/summary.json": config}
        entries = [
            entry("ev-s2-metric", "metric", "metrics/cache.json", metric, "2026-09-28T00:25:00Z", "2026-09-28T00:26:00Z"),
            entry("ev-s2-config", "config_summary", "config/summary.json", config, "2026-09-28T00:25:00Z", "2026-09-28T00:26:00Z"),
        ]
    elif case == "s3-image-tag":
        deploy = encoded({"image": "example.invalid/url-shortener:missing-tag", "source_sha": "a" * 40, "gitops_revision": "b" * 40, "status": "progressing"})
        event = encoded({"reason": "ImagePullBackOff", "message": "image tag was not found in the synthetic registry", "type": "Warning", "involved_uid": "pod-example-1"})
        files = {"deployment/change.json": deploy, "events/pull.json": event}
        entries = [
            entry("ev-s3-deployment", "deployment", "deployment/change.json", deploy, "2026-09-28T00:12:00Z", "2026-09-28T00:14:00Z"),
            entry("ev-s3-event", "event", "events/pull.json", event, "2026-09-28T09:13:00+09:00", "2026-09-28T00:14:00Z"),
        ]
    elif case == "s4-db-latency":
        metric = encoded({"query_id": "db_latency", "unit": "seconds", "samples": [
            {"at": "2026-09-28T00:35:00Z", "outcome": "p99", "value": 0.9},
            {"at": "2026-09-28T00:35:00Z", "outcome": "count", "value": 120},
        ]})
        log = b"synthetic app-side latency includes ORM and injected delay\nDB server time was not collected\n"
        files = {"metrics/db.json": metric, "logs/db.log": log}
        entries = [
            entry("ev-s4-metric", "metric", "metrics/db.json", metric, "2026-09-28T00:35:00Z", "2026-09-28T00:36:00Z"),
            entry("ev-s4-log", "log", "logs/db.log", log, "2026-09-28T00:35:05Z", "2026-09-28T00:36:00Z"),
        ]
    elif case == "s5-insufficient-data":
        files = {}
        entries = [
            entry("ev-s5-metric-missing", "metric", "metrics/cache.json", None, None, "2026-09-28T00:40:00Z", "missing", "metric sample was not collected"),
            entry("ev-s5-log-denied", "log", "logs/cache.log", None, None, "2026-09-28T00:40:00Z", "permission_denied", "log read permission was denied"),
        ]
    elif case == "s6-healthy":
        workload = encoded({"image": "example.invalid/url-shortener:healthy", "phase": "Running", "ready": True, "restarts": 0})
        metric = encoded({"query_id": "http_error_ratio", "unit": "ratio", "samples": [
            {"at": "2026-09-28T00:45:00Z", "outcome": "ratio", "value": 0},
            {"at": "2026-09-28T00:45:00Z", "outcome": "requests", "value": 200},
        ]})
        files = {"workload/status.json": workload, "metrics/http.json": metric}
        entries = [
            entry("ev-s6-workload", "workload_status", "workload/status.json", workload, "2026-09-28T00:45:00Z", "2026-09-28T00:46:00Z"),
            entry("ev-s6-metric", "metric", "metrics/http.json", metric, "2026-09-28T00:45:00Z", "2026-09-28T00:46:00Z"),
        ]
    elif case == "s9-primary-consistency":
        metric = encoded({
            "query_id": "cache_outcomes", "unit": "requests", "samples": [
                {"at": "2026-09-28T09:30:00+09:00", "outcome": "consistency_primary", "value": 2},
                {"at": "2026-09-28T00:30:00Z", "outcome": "error", "value": 0},
            ],
        })
        log = b"recent write marker active; read routed to Primary\nno cache access error observed\n"
        files = {"metrics/cache.json": metric, "logs/route.log": log}
        entries = [
            entry("ev-s9-metric", "metric", "metrics/cache.json", metric, "2026-09-28T09:30:00+09:00", "2026-09-28T09:31:00+09:00"),
            entry("ev-s9-log", "log", "logs/route.log", log, "2026-09-28T00:30:05Z", "2026-09-28T00:31:00Z"),
        ]
    elif case == "s10-rollback-recovered":
        failed = encoded({"image": "example.invalid/url-shortener:missing-tag", "source_sha": "c" * 40, "gitops_revision": "d" * 40, "ci_run_id": "synthetic-100", "status": "failed"})
        recovered = encoded({"image": "example.invalid/url-shortener:previous", "source_sha": "a" * 40, "gitops_revision": "e" * 40, "rollback_revision": "e" * 40, "ci_run_id": "synthetic-100", "status": "healthy_after_rollback"})
        files = {"deployment/failed.json": failed, "deployment/recovered.json": recovered}
        entries = [
            entry("ev-s10-failed", "deployment", "deployment/failed.json", failed, "2026-09-28T00:50:00Z", "2026-09-28T00:51:00Z"),
            entry("ev-s10-recovered", "deployment", "deployment/recovered.json", recovered, "2026-09-28T00:55:00Z", "2026-09-28T00:56:00Z"),
        ]
    else:
        raise ValueError("unknown development case")
    manifest = {
        "schema_version": 1, "target": TARGET, "window": WINDOW,
        "created_at": "2026-09-28T01:10:00Z", "entries": entries,
    }
    files["manifest.json"] = encoded(manifest)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="A2 합성 개발 증거 생성")
    parser.add_argument("--check", action="store_true", help="저장된 fixture와 일치하는지만 검사")
    args = parser.parse_args()
    base = ROOT / "evals/fixtures/dev"
    for case in (
        "s1-cache-error", "s2-normal-miss", "s3-image-tag", "s4-db-latency",
        "s5-insufficient-data", "s6-healthy", "s9-primary-consistency",
        "s10-rollback-recovered",
    ):
        for ref, content in case_files(case).items():
            path = base / case / ref
            if args.check:
                if not path.exists() or path.read_bytes() != content:
                    raise SystemExit(f"fixture differs: {path}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)


if __name__ == "__main__":
    main()
