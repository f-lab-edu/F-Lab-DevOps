"""A7 고정 입력을 정답표 없이 실행하고 개인 저장소에 결과를 보존한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_ops.cli import DEFAULT_STORE
from ai_ops.contracts import canonical_json_bytes, load_target_registry
from ai_ops.evidence import load_evidence_bundle
from ai_ops.fixed_summary import FIXED_PROMPT_VERSION, analyze_fixed_summary
from ai_ops.incidents import IncidentStore, RunStopped
from ai_ops.openai_engine import (
    AIConfig, CONTEXT_VERSION, MODEL, PRICE_DATE, PROMPT_VERSION, TOOL_VERSION, analyze_ai,
    api_key_from_environment,
)


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SUITE_PATH = ROOT / "evals/fixtures/holdout/suite.json"
REGISTRY_PATH = ROOT / "config/targets.example.json"
QUESTION = "제공된 시간대의 서비스 상태와 가능한 원인을 근거·불확실성과 함께 설명해 주세요."
EVAL_DAILY_BUDGET_USD = 10.0
MAX_EVAL_DAILY_BUDGET_USD = 30.0
STOP_REASONS = {
    "daily_budget_limit", "model_http_401", "model_http_403",
    "model_input_too_large",
    "model_credit_balance_exhausted", "model_insufficient_quota",
    "model_organization_spend_limit_exceeded", "model_project_spend_limit_exceeded",
    "model_organization_usage_limit_exceeded",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_suite(path: Path = SUITE_PATH) -> dict:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != 1 or suite.get("suite_id") not in ("a7-holdout-v1", "a7-holdout-v2", "a7-holdout-v3"):
        raise ValueError("평가 suite 버전이 다릅니다")
    expected = hashlib.sha256(canonical_json_bytes({k: v for k, v in suite.items() if k != "suite_hash"})).hexdigest()
    if suite.get("suite_hash") != expected or len(suite.get("cases", [])) != 16:
        raise ValueError("평가 suite 해시 또는 건수가 다릅니다")
    case_ids = [row["case_id"] for row in suite["cases"]]
    if len(set(case_ids)) != 16 or any(not case.startswith("case-") for case in case_ids):
        raise ValueError("평가 사건 ID가 중복되거나 잘못됐습니다")
    return suite


def _runbook_snapshot() -> dict[str, str]:
    paths = [ROOT / "runbooks/index.yaml", *sorted((ROOT / "runbooks").glob("*.md"))]
    return {path.relative_to(ROOT).as_posix(): _sha(path) for path in paths}


def _write_private(path: Path, value: dict) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir() or path.parent.resolve().is_relative_to(REPO_ROOT.resolve()):
        raise ValueError("평가 실행 결과는 저장소 밖의 디렉터리에 보관해야 합니다")
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".a7-", delete=False) as output:
        temporary = Path(output.name)
        os.fchmod(output.fileno(), 0o600)
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _daily_budget_available(store: IncidentStore, config: AIConfig) -> bool:
    """비용 예약 전 예비 확인을 한다. 최종 제한은 IncidentStore가 집행한다."""
    ledger_path = store.root / "ai-budget.json"
    if not ledger_path.exists():
        return True
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if ledger.get("schema_version") != 1 or not isinstance(ledger.get("days"), dict):
        raise ValueError("AI 예산 장부 형식이 잘못됐습니다")
    spent = ledger["days"].get(datetime.now(timezone.utc).date().isoformat(), 0)
    if type(spent) not in (int, float) or not math.isfinite(spent) or spent < 0:
        raise ValueError("AI 예산 장부 금액이 잘못됐습니다")
    return spent + config.per_case_usd <= config.per_day_usd + 1e-9


def _request_id(mode: str, case_id: str, repeat: int, attempt: int,
                suite_id: str = "a7-holdout-v1") -> str:
    """입력 구성 변경 뒤 이전 사건을 재조회하지 않도록 버전을 ID에 넣는다."""
    version = f"-{CONTEXT_VERSION}" if mode == "ai" else "-fixed-v2" if mode == "fixed" else ""
    suite_version = "-v3" if suite_id == "a7-holdout-v3" else "-v2" if suite_id == "a7-holdout-v2" else ""
    return f"a7{suite_version}-{mode}{version}-{case_id}-r{repeat}-a{attempt}"


def main() -> None:
    parser = argparse.ArgumentParser(description="A7 고정 입력 조사 실행; 정답표를 읽지 않는다")
    parser.add_argument("--mode", choices=("rules", "fixed", "ai"), default="rules")
    parser.add_argument("--suite", type=Path, default=SUITE_PATH,
                        help="고정 입력 suite.json; 기본값은 기존 v1")
    parser.add_argument("--case", action="append", dest="case_ids", help="이번 호출에서 실행할 case ID; 여러 번 지정 가능")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-new-runs", type=int, default=None)
    parser.add_argument("--daily-budget-usd", type=float, default=EVAL_DAILY_BUDGET_USD,
                        help="A7 평가 전용 UTC 일일 선예약 한도; 기본 $10, 최대 $30")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--result", type=Path, default=None)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3 or args.max_new_runs is not None and args.max_new_runs < 1:
        parser.error("반복 횟수는 1~3, 새 실행 상한은 양수여야 합니다")
    if not math.isfinite(args.daily_budget_usd) or not 0.10 <= args.daily_budget_usd <= MAX_EVAL_DAILY_BUDGET_USD:
        parser.error("A7 UTC 일일 선예약 한도는 $0.10~$30여야 합니다")
    suite_path = args.suite.resolve()
    suite = _load_suite(suite_path)
    known_cases = {row["case_id"] for row in suite["cases"]}
    selected_cases = set(args.case_ids) if args.case_ids else known_cases
    if not selected_cases <= known_cases:
        parser.error("--case에는 suite에 등록된 사건 ID만 사용할 수 있습니다")
    registry = load_target_registry(REGISTRY_PATH)
    bundles = {}
    for row in suite["cases"]:
        folder = (suite_path.parent / row["bundle"]).resolve()
        if folder.parent != suite_path.parent or _sha(folder / "manifest.json") != row["manifest_sha256"]:
            raise ValueError(f"평가 원본 manifest가 바뀌었습니다: {row['case_id']}")
        bundle = load_evidence_bundle(folder, registry)
        if bundle.bundle_hash != row["input_hash"]:
            raise ValueError(f"평가 입력 해시가 바뀌었습니다: {row['case_id']}")
        bundles[row["case_id"]] = folder
    store = IncidentStore(args.store)
    suite_prefix = "a7-v3" if suite["suite_id"] == "a7-holdout-v3" else "a7-v2" if suite["suite_id"] == "a7-holdout-v2" else "a7"
    result_name = (f"{suite_prefix}-ai-{CONTEXT_VERSION}-results.json" if args.mode == "ai"
                   else f"{suite_prefix}-fixed-prompt-v2-results.json" if args.mode == "fixed"
                   else f"{suite_prefix}-{args.mode}-results.json")
    result_path = args.result or store.root / result_name
    if result_path.is_symlink():
        raise ValueError("평가 결과 파일은 symlink일 수 없습니다")
    config = (AIConfig(account_label="personal-openai", per_day_usd=args.daily_budget_usd)
              if args.mode != "rules" else None)
    api_key = (api_key_from_environment() or store.load_api_key()) if config else ""
    if config and not api_key:
        raise ValueError("AI 실행에는 개인 저장소 또는 환경변수의 API 키가 필요합니다")
    header = {
        "schema_version": 1, "suite_id": suite["suite_id"], "suite_hash": suite["suite_hash"],
        "mode": args.mode, "repeats": args.repeats,
        "model": MODEL if config else None,
        "prompt_version": (FIXED_PROMPT_VERSION if args.mode == "fixed" else PROMPT_VERSION) if config else None,
        "tool_version": ("fixed-none-v1" if args.mode == "fixed" else TOOL_VERSION) if config else None,
        "price_date": PRICE_DATE if config else None,
        "runbook_hashes": _runbook_snapshot(),
        "input_hashes": {row["case_id"]: row["input_hash"] for row in suite["cases"]},
        "results": [],
    }
    if args.mode == "ai":
        header["context_version"] = CONTEXT_VERSION
    if result_path.exists():
        existing = json.loads(result_path.read_text(encoding="utf-8"))
        fixed_settings = lambda value: {k: v for k, v in value.items() if k not in ("results", "repeats")}
        if (fixed_settings(existing) != fixed_settings(header) or
                type(existing.get("repeats")) is not int or not 1 <= existing["repeats"] <= args.repeats):
            raise ValueError("기존 평가 실행의 고정 설정이 현재와 다릅니다")
        header = existing
        header["repeats"] = args.repeats
    completed = {(row["case_id"], row["repeat"]) for row in header["results"]
                 if row["status"] == "completed"}
    new_runs = 0
    for row in suite["cases"]:
        case_id = row["case_id"]
        if case_id not in selected_cases:
            continue
        for repeat in range(1, args.repeats + 1):
            if (case_id, repeat) in completed:
                continue
            if args.max_new_runs is not None and new_runs >= args.max_new_runs:
                print(f"설정한 새 실행 상한 {args.max_new_runs}건에 도달했습니다")
                return
            if config and not _daily_budget_available(store, config):
                print("UTC 일일 AI 예산이 부족해 새 사건을 만들지 않고 중지했습니다")
                return
            attempt = 1 + sum(row["case_id"] == case_id and row["repeat"] == repeat
                              for row in header["results"])
            request_id = _request_id(args.mode, case_id, repeat, attempt, suite["suite_id"])
            started = time.perf_counter()
            try:
                if args.mode == "ai":
                    record = store.investigate(
                        bundle_root=bundles[case_id], registry=registry, question=QUESTION,
                        request_id=request_id, mode="ai", ai_config=config,
                        analyzer=lambda request, bundle, run_id: analyze_ai(request, bundle, run_id, config, api_key),
                    )
                elif args.mode == "fixed":
                    record = store.investigate(
                        bundle_root=bundles[case_id], registry=registry, question=QUESTION,
                        request_id=request_id, mode="ai", ai_config=config,
                        analyzer=lambda request, bundle, run_id: analyze_fixed_summary(
                            request, bundle, run_id, config, api_key),
                    )
                else:
                    record = store.investigate(
                        bundle_root=bundles[case_id], registry=registry, question=QUESTION,
                        request_id=request_id,
                    )
            except RunStopped as stopped:
                record = store.show(stopped.incident_id, stopped.run_id)
            result = {
                "case_id": case_id, "repeat": repeat, "attempt": attempt,
                "input_hash": row["input_hash"],
                "incident_id": record.incident_id, "run_id": record.run_id,
                "status": record.metadata["status"], "failure_reason": record.metadata.get("failure_reason"),
                "wall_ms": int((time.perf_counter() - started) * 1000),
                "model_usage": record.metadata.get("model_usage"),
                "report": record.report.model_dump(mode="json") if record.report else None,
            }
            header["results"].append(result)
            _write_private(result_path, header)
            new_runs += 1
            print(f"{case_id} #{repeat}: {result['status']} ({result['failure_reason'] or 'ok'})")
            if config and result["failure_reason"] in STOP_REASONS:
                print("API 인증·할당량·예산 또는 고정 입력 한도 문제로 유료 평가를 중지했습니다")
                return
    print(f"평가 실행 기록: {result_path}")


if __name__ == "__main__":
    main()
