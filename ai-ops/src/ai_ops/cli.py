"""A3 저장 증거 검증·규칙 조사·보고서 재조회 명령행 인터페이스."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from ai_ops.contracts import load_target_registry
from ai_ops.evidence import load_evidence_bundle, redact_text
from ai_ops.incidents import IncidentStore, RunStopped
from ai_ops.openai_engine import AIConfig, analyze_ai, api_key_from_environment
from ai_ops.reports import DIAGNOSIS, format_kst


DEFAULT_STORE = Path.home() / ".local/share/url-shortener-ai-ops"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aiops", description="저장된 운영 증거의 로컬 조사")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-bundle", help="manifest와 증거 파일을 검증한다")
    validate.add_argument("--bundle", type=Path, required=True)
    validate.add_argument("--registry", type=Path, required=True)
    investigate = commands.add_parser("investigate", help="검증된 증거로 규칙 또는 AI 보고서를 만든다")
    investigate.add_argument("--bundle", type=Path, required=True)
    investigate.add_argument("--registry", type=Path, required=True)
    investigate.add_argument("--store", type=Path, default=DEFAULT_STORE)
    investigate.add_argument("--mode", choices=("rules", "ai"), default="rules")
    investigate.add_argument("--question", required=True)
    investigate.add_argument("--request-id", required=True)
    investigate.add_argument("--ai-account", default="personal-openai", help="AI 예산을 묶을 계정 별칭이다")
    show = commands.add_parser("show", help="저장된 실행 결과를 다시 보여준다")
    show.add_argument("--store", type=Path, default=DEFAULT_STORE)
    show.add_argument("--incident", required=True)
    show.add_argument("--run", required=True)
    show.add_argument("--format", choices=("markdown", "json"), default="markdown")
    configure = commands.add_parser("configure-api-key", help="API 키를 화면에 표시하지 않고 개인 저장소에 보관한다")
    configure.add_argument("--store", type=Path, default=DEFAULT_STORE)
    status = commands.add_parser("api-key-status", help="키 값을 표시하지 않고 설정 상태만 확인한다")
    status.add_argument("--store", type=Path, default=DEFAULT_STORE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-bundle":
            bundle = load_evidence_bundle(args.bundle, load_target_registry(args.registry))
            print("증거 묶음 검증 완료")
            print(f"대상: {bundle.target.cluster_id}/{bundle.target.namespace}/{bundle.target.workload}")
            print(f"조사 범위: {format_kst(bundle.window.start)} ~ {format_kst(bundle.window.end)}")
            print(f"bundle_hash: {bundle.bundle_hash}")
            for item in bundle.evidence:
                print(f"- {item.evidence_id}: {item.status}, 관측 {format_kst(item.observed_at)}, 수집 {format_kst(item.collected_at)}")
            return 0
        if args.command == "configure-api-key":
            if not sys.stdin.isatty():
                raise ValueError("키는 터미널에서 직접 입력해야 합니다")
            store = IncidentStore(args.store)
            key = getpass.getpass("OpenAI API 키: ")
            store.save_api_key(key)
            print("API 키를 개인 저장소에 보관했습니다. 외부 API 호출은 하지 않았습니다.")
            print(f"저장소: {store.root}")
            return 0
        if args.command == "api-key-status":
            store = IncidentStore(args.store)
            source = "환경변수" if api_key_from_environment() else "개인 저장소" if store.load_api_key() else "미설정"
            print(f"API 키 상태: {source}")
            print(f"저장소: {store.root}")
            return 0
        if args.command == "investigate":
            store = IncidentStore(args.store)
            ai_config = None
            analyzer = None
            if args.mode == "ai":
                api_key = api_key_from_environment() or store.load_api_key()
                if not api_key:
                    raise ValueError("API 키가 없습니다. configure-api-key 명령으로 입력해 주세요")
                ai_config = AIConfig(account_label=args.ai_account)
                analyzer = lambda request, bundle, run_id: analyze_ai(
                    request, bundle, run_id, ai_config, api_key)
            options = {"analyzer": analyzer, "mode": "ai", "ai_config": ai_config} if analyzer else {}
            record = store.investigate(bundle_root=args.bundle, registry=load_target_registry(args.registry),
                                       question=args.question, request_id=args.request_id, **options)
            print(f"상태: {record.metadata['status']}")
            print(f"사건 ID: {record.incident_id}")
            print(f"실행 ID: {record.run_id}")
            if record.request is not None:
                print(f"요청 시각: {format_kst(record.request.requested_at)}")
            if record.report is not None:
                print(f"진단: {DIAGNOSIS[record.report.diagnosis_status]}")
            else:
                print("기존 실행은 완료되지 않았다. 새 조사는 다른 request ID로 시작한다.")
            return 0 if record.metadata["status"] == "completed" else 1
        store = IncidentStore(args.store)
        record = store.show(args.incident, args.run)
        if record.report is None:
            if args.format == "json":
                print(json.dumps(record.metadata, ensure_ascii=False, indent=2))
            else:
                print(f"실행 상태: {record.metadata['status']}")
                print(f"사건 ID: {record.incident_id}, 실행 ID: {record.run_id}")
                print(f"마지막 기록: {format_kst(datetime.fromisoformat(record.metadata['updated_at'].replace('Z', '+00:00')))}")
                print(f"사유: {record.metadata.get('failure_reason') or '없음'}")
            return 1
        if args.format == "json":
            print(record.report.model_dump_json(indent=2))
        else:
            print(record.markdown, end="")
        return 0
    except RunStopped as exc:
        print(f"실행 {exc.status}: 사건 {exc.incident_id}, 실행 {exc.run_id}", file=sys.stderr)
        if exc.reason:
            safe_reason, _ = redact_text(exc.reason)
            print(f"사유: {safe_reason}", file=sys.stderr)
        return 130 if exc.status == "cancelled" else 1
    except ValidationError:
        print("입력 계약 검증에 실패했습니다.", file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, EOFError) as exc:
        safe_error, _ = redact_text(str(exc))
        print(f"오류: {safe_error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("사용자가 조사를 취소했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
