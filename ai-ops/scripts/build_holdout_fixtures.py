"""A7 최종 평가용 합성 증거 16건을 결정적으로 생성하거나 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_ops.contracts import canonical_json_bytes, load_target_registry
from ai_ops.evidence import load_evidence_bundle


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "evals/fixtures/holdout"
REGISTRY = ROOT / "config/targets.example.json"
TARGET = json.loads(REGISTRY.read_text(encoding="utf-8"))["targets"][0]


def _json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _time(day: int, minute: int, *, kst: bool = False) -> str:
    moment = datetime(2026, 8, day, tzinfo=timezone.utc) + timedelta(minutes=minute)
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
        "ci_run_id": f"holdout-{day:02d}",
        "status": status,
        **extra,
    }


def _cases() -> dict[str, list[tuple[str, str, dict | str | None, int, str, str | None]]]:
    # 각 튜플은 종류·경로·원본 내용·관측 분·상태·누락 사유 순서다.
    return {
        "case-01": [
            ("metric", "metrics/cache.json", _metric(1, 18, "cache_outcomes", "requests", [("error", 7), ("hit", 23)]), 18, "available", None),
            ("log", "logs/app.log", "RedisError: connection reset during cache lookup\nrequest continued through primary database read\n", 19, "available", None),
        ],
        "case-02": [
            ("metric", "metrics/cache.json", _metric(2, 31, "cache_outcomes", "requests", [("error", 4), ("miss", 1)]), 31, "available", None),
            ("log", "logs/app.log", "cached payload JSONDecodeError during URL lookup\napplication used database fallback\n", 32, "available", None),
            ("config_summary", "config/cache.json", {"cache_intended_enabled": True, "redis_url_configured": True, "read_engine_role": "shared"}, 32, "available", None),
        ],
        "case-03": [
            ("deployment", "deployment/change.json", _deployment(3, "v2-absent", "progressing", "a"), 12, "available", None),
            ("event", "events/pod.json", {"reason": "ImagePullBackOff", "message": "manifest for the selected tag was not found", "type": "Warning", "involved_uid": "pod-h03"}, 15, "available", None),
        ],
        "case-04": [
            ("deployment", "deployment/change.json", _deployment(4, "v2-private", "progressing", "b"), 21, "available", None),
            ("event", "events/pod.json", {"reason": "ImagePullBackOff", "message": "registry returned 401 for image pull", "type": "Warning", "involved_uid": "pod-h04"}, 24, "available", None),
        ],
        "case-05": [
            ("metric", "metrics/db.json", _metric(5, 33, "db_latency", "seconds", [("p99", 0.84), ("count", 180)]), 33, "available", None),
            ("log", "logs/app.log", "ORM mapping duration reached 700ms within app-side database timing\nserver execution timing was not collected\n", 34, "available", None),
        ],
        "case-06": [
            ("metric", "metrics/db.json", _metric(6, 27, "db_latency", "seconds", [("p99", 1.3), ("count", 45)]), 27, "available", None),
            ("log", "logs/app.log", "database client pool wait reached 800ms\nSQL execution timing was not collected\n", 28, "available", None),
        ],
        "case-07": [
            ("deployment", "deployment/first.json", _deployment(7, "v3-bad", "failed", "c"), 12, "available", None),
            ("deployment", "deployment/second.json", _deployment(7, "v2-stable", "healthy_after_rollback", "d", rollback_revision="d" * 40), 29, "available", None),
        ],
        "case-08": [
            ("deployment", "deployment/first.json", _deployment(8, "v4-healthfail", "failed", "e"), 16, "available", None),
            ("deployment", "deployment/second.json", _deployment(8, "v3-stable", "healthy_after_rollback", "f", rollback_revision="f" * 40), 37, "available", None),
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v3-stable", "phase": "Running", "ready": True, "restarts": 0}, 39, "available", None),
        ],
        "case-09": [
            ("metric", "metrics/cache.json", _metric(9, 23, "cache_outcomes", "requests", [("miss", 9), ("error", 0)]), 23, "available", None),
            ("config_summary", "config/cache.json", {"cache_intended_enabled": True, "redis_url_configured": True, "read_engine_role": "shared"}, 24, "available", None),
        ],
        "case-10": [
            ("metric", "metrics/cache.json", _metric(10, 42, "cache_outcomes", "requests", [("miss", 14), ("error", 0)]), 42, "available", None),
            ("log", "logs/app.log", "cache entry reached TTL; next lookup fetched from database\nno cache error reported\n", 43, "available", None),
        ],
        "case-11": [
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v5", "phase": "Running", "ready": True, "restarts": 0}, 20, "available", None),
            ("metric", "metrics/http.json", _metric(11, 21, "http_error_ratio", "ratio", [("ratio", 0), ("requests", 300)]), 21, "available", None),
        ],
        "case-12": [
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v6", "phase": "Running", "ready": True, "restarts": 1}, 35, "available", None),
            ("metric", "metrics/http.json", _metric(12, 36, "http_error_ratio", "ratio", [("ratio", 0), ("requests", 90)]), 36, "available", None),
        ],
        "case-13": [
            ("metric", "metrics/cache.json", _metric(13, 14, "cache_outcomes", "requests", [("consistency_primary", 5), ("error", 0)]), 14, "available", None),
            ("log", "logs/app.log", "recent write marker active for URL; read routed to Primary\n", 15, "available", None),
        ],
        "case-14": [
            ("metric", "metrics/cache.json", _metric(14, 49, "cache_outcomes", "requests", [("consistency_primary", 2), ("error", 0)]), 49, "available", None),
            ("log", "logs/app.log", "recent write marker still active at request time; Primary read selected\n", 50, "available", None),
        ],
        "case-15": [
            ("metric", "metrics/cache.json", None, 20, "missing", "metric query was not collected"),
            ("log", "logs/app.log", None, 21, "permission_denied", "application log read was denied"),
        ],
        "case-16": [
            ("workload_status", "workload/status.json", None, 18, "stale", "workload status is older than the request window"),
            ("event", "events/pod.json", None, 19, "missing", "event record was not collected"),
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
            "evidence_id": f"ev-h{day:02d}-{index + 1}", "kind": kind,
            "source_ref": ref, "raw_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "observed_at": _time(day, minute, kst=day % 2 == 0) if raw is not None else None,
            "collected_at": _time(day, minute + 1, kst=day % 2 != 0),
            "status": status, "missing_reason": reason,
            "provenance": {"kind": "synthetic", "origin": "a7-holdout", "fixture_version": "1"},
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
    suite = {"schema_version": 1, "suite_id": "a7-holdout-v1", "cases": rows}
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
