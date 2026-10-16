"""A7 두 번째 미노출 평가용 합성 증거 16건을 생성하거나 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_ops.contracts import canonical_json_bytes, load_target_registry
from ai_ops.evidence import load_evidence_bundle


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "evals/fixtures/holdout-v2"
REGISTRY = ROOT / "config/targets.example.json"
TARGET = json.loads(REGISTRY.read_text(encoding="utf-8"))["targets"][0]


def _json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _time(day: int, minute: int, *, kst: bool = False) -> str:
    moment = datetime(2026, 9, day, tzinfo=timezone.utc) + timedelta(minutes=minute)
    if kst:
        return moment.astimezone(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _metric(day: int, minute: int, query: str, unit: str, samples: list[tuple[str, float]]) -> dict:
    return {"query_id": query, "unit": unit, "samples": [
        {"at": _time(day, minute, kst=day % 2 == 0), "outcome": outcome, "value": value}
        for outcome, value in samples
    ]}


def _deployment(day: int, tag: str, status: str, revision: str, **extra: str) -> dict:
    return {
        "image": f"registry.example.invalid/url-shortener:{tag}",
        "source_sha": f"{day:040x}",
        "gitops_revision": revision * 40,
        "ci_run_id": f"holdout-v2-{day:02d}",
        "status": status,
        **extra,
    }


def _cases() -> dict[str, list[tuple[str, str, dict | str | None, int, str, str | None]]]:
    # 각 튜플은 종류·경로·원본 내용·관측 분·상태·누락 사유 순서다.
    return {
        "case-01": [
            ("metric", "metrics/cache.json", _metric(1, 11, "cache_outcomes", "requests", [("error", 5), ("hit", 67)]), 11, "available", None),
            ("log", "logs/app.log", "cache GET rejected with NOAUTH for five requests; application used database fallback\n", 12, "available", None),
        ],
        "case-02": [
            ("metric", "metrics/cache.json", _metric(2, 25, "cache_outcomes", "requests", [("error", 8), ("hit", 41)]), 25, "available", None),
            ("log", "logs/app.log", "cache read exceeded client timeout 120ms; request continued via database fallback\n", 26, "available", None),
            ("config_summary", "config/cache.json", {"cache_intended_enabled": True, "redis_url_configured": True, "read_engine_role": "shared"}, 27, "available", None),
        ],
        "case-03": [
            ("deployment", "deployment/change.json", _deployment(3, "v7-cert", "progressing", "1"), 18, "available", None),
            ("event", "events/pod.json", {"reason": "ImagePullBackOff", "message": "registry TLS certificate signed by unknown authority during image pull", "type": "Warning", "involved_uid": "pod-v03"}, 20, "available", None),
        ],
        "case-04": [
            ("deployment", "deployment/change.json", _deployment(4, "v7-limited", "progressing", "2"), 30, "available", None),
            ("event", "events/pod.json", {"reason": "ImagePullBackOff", "message": "registry returned HTTP 429 pull rate limit; retry scheduled", "type": "Warning", "involved_uid": "pod-v04"}, 32, "available", None),
        ],
        "case-05": [
            ("metric", "metrics/db.json", _metric(5, 17, "db_latency", "seconds", [("p99", 0.91), ("count", 130)]), 17, "available", None),
            ("log", "logs/app.log", "database hostname resolution took 650ms in client connection setup; SQL execution timing unavailable\n", 18, "available", None),
        ],
        "case-06": [
            ("metric", "metrics/db.json", _metric(6, 39, "db_latency", "seconds", [("p99", 1.16), ("count", 62)]), 39, "available", None),
            ("log", "logs/app.log", "database connection TLS handshake took 740ms on application client; SQL execution timing unavailable\n", 40, "available", None),
        ],
        "case-07": [
            ("deployment", "deployment/first.json", _deployment(7, "v8-notready", "failed", "3"), 9, "available", None),
            ("event", "events/pod.json", {"reason": "Unhealthy", "message": "readiness probe failed for new revision", "type": "Warning", "involved_uid": "pod-v07"}, 11, "available", None),
            ("deployment", "deployment/second.json", _deployment(7, "v7-stable", "healthy_after_rollback", "4", rollback_revision="4" * 40), 33, "available", None),
        ],
        "case-08": [
            ("deployment", "deployment/first.json", _deployment(8, "v9-regression", "failed", "5"), 14, "available", None),
            ("deployment", "deployment/second.json", _deployment(8, "v8-stable", "healthy_after_rollback", "6", rollback_revision="6" * 40), 42, "available", None),
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v8-stable", "phase": "Running", "ready": True, "restarts": 0}, 44, "available", None),
        ],
        "case-09": [
            ("metric", "metrics/cache.json", _metric(9, 29, "cache_outcomes", "requests", [("miss", 6), ("error", 0)]), 29, "available", None),
            ("log", "logs/app.log", "new URL key had no cached entry; database lookup succeeded and entry was populated\n", 30, "available", None),
        ],
        "case-10": [
            ("metric", "metrics/cache.json", _metric(10, 36, "cache_outcomes", "requests", [("bypass", 12), ("error", 0)]), 36, "available", None),
            ("config_summary", "config/cache.json", {"cache_intended_enabled": False, "redis_url_configured": False, "read_engine_role": "shared"}, 37, "available", None),
        ],
        "case-11": [
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v10", "phase": "Running", "ready": True, "restarts": 0}, 23, "available", None),
            ("metric", "metrics/http.json", _metric(11, 24, "http_error_ratio", "ratio", [("ratio", 0), ("requests", 410)]), 24, "available", None),
        ],
        "case-12": [
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v11", "phase": "Running", "ready": True, "restarts": 1}, 16, "available", None),
            ("metric", "metrics/http.json", _metric(12, 17, "http_error_ratio", "ratio", [("ratio", 0), ("requests", 160)]), 17, "available", None),
            ("event", "events/pod.json", {"reason": "Started", "message": "container started after scheduled node maintenance", "type": "Normal", "involved_uid": "pod-v12"}, 18, "available", None),
        ],
        "case-13": [
            ("metric", "metrics/cache.json", _metric(13, 22, "cache_outcomes", "requests", [("consistency_primary", 7), ("error", 0)]), 22, "available", None),
            ("log", "logs/app.log", "read-after-write consistency marker found; URL lookup deliberately routed to Primary\n", 23, "available", None),
        ],
        "case-14": [
            ("metric", "metrics/cache.json", _metric(14, 34, "cache_outcomes", "requests", [("consistency_primary", 3), ("error", 0)]), 34, "available", None),
            ("log", "logs/app.log", "short-link target updated moments ago; consistency window routed follow-up reads to Primary\n", 35, "available", None),
        ],
        "case-15": [
            ("metric", "metrics/cache.json", None, 28, "missing", "Prometheus query timed out; no sample available"),
            ("log", "logs/app.log", None, 29, "permission_denied", "container log permission was denied"),
        ],
        "case-16": [
            ("workload_status", "workload/status.json", None, 31, "stale", "last workload snapshot predates this observation window"),
            ("event", "events/pod.json", None, 32, "missing", "event stream for this window was unavailable"),
        ],
    }


def _files(case_id: str, items: list[tuple[str, str, dict | str | None, int, str, str | None]]) -> dict[str, bytes]:
    day = int(case_id[-2:])
    files: dict[str, bytes] = {}
    entries = []
    for index, (kind, ref, content, minute, status, reason) in enumerate(items):
        raw = _json(content) if isinstance(content, dict) else content.encode("utf-8") if isinstance(content, str) else None
        if raw is not None:
            files[ref] = raw
        entries.append({
            "evidence_id": f"ev-v{day:02d}-{index + 1}", "kind": kind,
            "source_ref": ref, "raw_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "observed_at": _time(day, minute, kst=day % 2 == 0) if raw is not None else None,
            "collected_at": _time(day, minute + 1, kst=day % 2 != 0),
            "status": status, "missing_reason": reason,
            "provenance": {"kind": "synthetic", "origin": "a7-holdout-v2", "fixture_version": "2"},
        })
    files["manifest.json"] = _json({
        "schema_version": 1, "target": TARGET,
        "window": {"start": _time(day, 0), "end": _time(day, 60)},
        "created_at": _time(day, 70), "entries": entries,
    })
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description="A7 합성 최종 평가 입력 16건")
    parser.add_argument("--check", action="store_true", help="고정 입력·해시가 저장된 파일과 같은지 확인")
    args = parser.parse_args()
    cases = _cases()
    for case_id, items in cases.items():
        for ref, content in _files(case_id, items).items():
            path = BASE / case_id / ref
            if args.check:
                if not path.is_file() or path.read_bytes() != content:
                    raise SystemExit(f"평가 입력이 고정본과 다릅니다: {path}")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    registry = load_target_registry(REGISTRY)
    rows = []
    for case_id in cases:
        folder = BASE / case_id
        bundle = load_evidence_bundle(folder, registry)
        rows.append({
            "case_id": case_id, "bundle": case_id,
            "input_hash": bundle.bundle_hash,
            "manifest_sha256": hashlib.sha256((folder / "manifest.json").read_bytes()).hexdigest(),
        })
    suite = {"schema_version": 1, "suite_id": "a7-holdout-v2", "cases": rows}
    suite["suite_hash"] = hashlib.sha256(canonical_json_bytes(suite)).hexdigest()
    suite_path = BASE / "suite.json"
    if args.check:
        if not suite_path.is_file() or suite_path.read_bytes() != _json(suite):
            raise SystemExit("평가 suite 해시가 고정본과 다릅니다")
        expected = {BASE / case_id / ref for case_id, items in cases.items() for ref in _files(case_id, items)} | {suite_path}
        actual = set(BASE.rglob("*.json")) | set(BASE.rglob("*.log"))
        if actual != expected:
            raise SystemExit("평가 입력 파일 목록이 고정본과 다릅니다")
    else:
        suite_path.write_bytes(_json(suite))
    print(f"{len(rows)}건 확인: {suite['suite_hash']}")


if __name__ == "__main__":
    main()
