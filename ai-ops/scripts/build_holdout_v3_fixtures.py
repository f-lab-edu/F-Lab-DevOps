"""A7 세 번째 미노출 평가용 합성 증거 16건을 생성하거나 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_ops.contracts import canonical_json_bytes, load_target_registry
from ai_ops.evidence import load_evidence_bundle


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "evals/fixtures/holdout-v3"
RUBRIC = ROOT / "evals/rubrics/holdout-v3/scenarios.json"
REGISTRY = ROOT / "config/targets.example.json"
TARGET = json.loads(REGISTRY.read_text(encoding="utf-8"))["targets"][0]


def _json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _time(day: int, minute: int, *, kst: bool = False) -> str:
    moment = datetime(2026, 9, day + 10, tzinfo=timezone.utc) + timedelta(minutes=minute)
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
        "ci_run_id": f"holdout-v3-{day:02d}",
        "status": status,
        **extra,
    }


def _cases() -> dict[str, list[tuple[str, str, dict | str | None, int, str, str | None]]]:
    # 각 튜플은 종류·경로·원본 내용·관측 분·상태·누락 사유 순서다.
    return {
        "case-01": [
            ("metric", "metrics/cache.json", _metric(1, 13, "cache_outcomes", "requests", [("error", 11), ("hit", 54)]), 13, "available", None),
            ("log", "logs/app.log", "cache ACL rollover rejected the configured username-password pair; eleven reads used database fallback\n", 14, "available", None),
            ("config_summary", "config/cache.json", {"cache_intended_enabled": True, "redis_url_configured": True, "read_engine_role": "shared"}, 15, "available", None),
        ],
        "case-02": [
            ("metric", "metrics/cache.json", _metric(2, 21, "cache_outcomes", "requests", [("error", 4), ("hit", 12)]), 21, "available", None),
            ("log", "logs/app.log", "old cached value raised UnicodeDecodeError in the new payload decoder; URL read continued with database fallback\n", 22, "available", None),
        ],
        "case-03": [
            ("deployment", "deployment/change.json", _deployment(3, "v12-no-tag", "progressing", "7"), 19, "available", None),
            ("event", "events/pod.json", {"reason": "ErrImagePull", "message": "registry manifest unknown: requested tag v12-no-tag was not found", "type": "Warning", "involved_uid": "pod-w03"}, 20, "available", None),
        ],
        "case-04": [
            ("deployment", "deployment/change.json", _deployment(4, "v12-registry-503", "progressing", "8"), 34, "available", None),
            ("event", "events/pod.json", {"reason": "ImagePullBackOff", "message": "registry returned HTTP 503 Service Unavailable during image pull", "type": "Warning", "involved_uid": "pod-w04"}, 35, "available", None),
        ],
        "case-05": [
            ("metric", "metrics/db.json", _metric(5, 27, "db_latency", "seconds", [("p99", 1.07), ("count", 110)]), 27, "available", None),
            ("log", "logs/app.log", "application DB connection pool acquisition waited 830ms; measured SQL execution was 12ms\n", 28, "available", None),
        ],
        "case-06": [
            ("metric", "metrics/db.json", _metric(6, 37, "db_latency", "seconds", [("p99", 0.95), ("count", 90)]), 37, "available", None),
            ("log", "logs/app.log", "DB hostname lookup returned SERVFAIL; client waited 680ms before connection and SQL execution was not measured\n", 38, "available", None),
        ],
        "case-07": [
            ("deployment", "deployment/first.json", _deployment(7, "v13-readiness", "failed", "9"), 10, "available", None),
            ("event", "events/pod.json", {"reason": "Unhealthy", "message": "new revision readiness endpoint returned HTTP 503", "type": "Warning", "involved_uid": "pod-w07"}, 12, "available", None),
            ("deployment", "deployment/second.json", _deployment(7, "v12-stable", "healthy_after_rollback", "a", rollback_revision="a" * 40), 31, "available", None),
        ],
        "case-08": [
            ("deployment", "deployment/first.json", _deployment(8, "v14-memory", "failed", "b"), 16, "available", None),
            ("event", "events/pod.json", {"reason": "OOMKilled", "message": "new revision container exceeded its memory limit", "type": "Warning", "involved_uid": "pod-w08"}, 18, "available", None),
            ("deployment", "deployment/second.json", _deployment(8, "v13-stable", "healthy_after_rollback", "c", rollback_revision="c" * 40), 41, "available", None),
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v13-stable", "phase": "Running", "ready": True, "restarts": 0}, 43, "available", None),
        ],
        "case-09": [
            ("metric", "metrics/cache.json", _metric(9, 26, "cache_outcomes", "requests", [("miss", 5), ("error", 0)]), 26, "available", None),
            ("log", "logs/app.log", "first read after short-link registration missed cache, returned target from database, and warmed the entry\n", 27, "available", None),
        ],
        "case-10": [
            ("metric", "metrics/cache.json", _metric(10, 33, "cache_outcomes", "requests", [("bypass", 9), ("error", 0)]), 33, "available", None),
            ("config_summary", "config/cache.json", {"cache_intended_enabled": False, "redis_url_configured": False, "read_engine_role": "shared"}, 34, "available", None),
            ("log", "logs/app.log", "cache feature flag was intentionally off for this window; reads used database directly\n", 35, "available", None),
        ],
        "case-11": [
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v15", "phase": "Running", "ready": True, "restarts": 0}, 22, "available", None),
            ("metric", "metrics/http.json", _metric(11, 23, "http_error_ratio", "ratio", [("ratio", 0), ("requests", 360)]), 23, "available", None),
        ],
        "case-12": [
            ("workload_status", "workload/status.json", {"image": "registry.example.invalid/url-shortener:v16", "phase": "Running", "ready": True, "restarts": 1}, 17, "available", None),
            ("metric", "metrics/http.json", _metric(12, 18, "http_error_ratio", "ratio", [("ratio", 0), ("requests", 220)]), 18, "available", None),
            ("event", "events/pod.json", {"reason": "Started", "message": "container started after scheduled rolling upgrade", "type": "Normal", "involved_uid": "pod-w12"}, 19, "available", None),
        ],
        "case-13": [
            ("metric", "metrics/cache.json", _metric(13, 29, "cache_outcomes", "requests", [("consistency_primary", 4), ("error", 0)]), 29, "available", None),
            ("log", "logs/app.log", "read-your-writes token on the request pinned the follow-up lookup to Primary as designed\n", 30, "available", None),
        ],
        "case-14": [
            ("metric", "metrics/cache.json", _metric(14, 38, "cache_outcomes", "requests", [("consistency_primary", 5), ("error", 0)]), 38, "available", None),
            ("log", "logs/app.log", "caller requested strong consistency for a just-edited link; lookup intentionally used Primary\n", 39, "available", None),
        ],
        "case-15": [
            ("metric", "metrics/http.json", None, 25, "missing", "metrics scrape was denied for this observation window"),
            ("log", "logs/app.log", None, 26, "permission_denied", "application log access was denied"),
        ],
        "case-16": [
            ("workload_status", "workload/status.json", None, 31, "stale", "workload snapshot predates the requested window"),
            ("deployment", "deployment/change.json", None, 32, "missing", "deployment history was unavailable for this window"),
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
            "evidence_id": f"ev-w{day:02d}-{index + 1}", "kind": kind,
            "source_ref": ref, "raw_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "observed_at": _time(day, minute, kst=day % 2 == 0) if raw is not None else None,
            "collected_at": _time(day, minute + 1, kst=day % 2 != 0),
            "status": status, "missing_reason": reason,
            "provenance": {"kind": "synthetic", "origin": "a7-holdout-v3", "fixture_version": "3"},
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
    suite = {"schema_version": 1, "suite_id": "a7-holdout-v3", "cases": rows}
    suite["suite_hash"] = hashlib.sha256(canonical_json_bytes(suite)).hexdigest()
    suite_path = BASE / "suite.json"
    if args.check:
        if not suite_path.is_file() or suite_path.read_bytes() != _json(suite):
            raise SystemExit("평가 suite 해시가 고정본과 다릅니다")
        if not RUBRIC.is_file():
            raise SystemExit("평가 정답표가 없습니다")
        rubric = json.loads(RUBRIC.read_text(encoding="utf-8"))
        expected_rubric_hash = hashlib.sha256(canonical_json_bytes({
            key: value for key, value in rubric.items() if key != "rubric_hash"
        })).hexdigest()
        if (rubric.get("suite_id") != suite["suite_id"] or
                rubric.get("rubric_hash") != expected_rubric_hash or
                {row["case_id"] for row in rubric.get("cases", [])} != set(cases)):
            raise SystemExit("평가 정답표의 해시나 사례가 고정본과 다릅니다")
        expected = {BASE / case_id / ref for case_id, items in cases.items() for ref in _files(case_id, items)} | {suite_path}
        actual = set(BASE.rglob("*.json")) | set(BASE.rglob("*.log"))
        if actual != expected:
            raise SystemExit("평가 입력 파일 목록이 고정본과 다릅니다")
    else:
        suite_path.write_bytes(_json(suite))
    print(f"{len(rows)}건 확인: {suite['suite_hash']}")


if __name__ == "__main__":
    main()
