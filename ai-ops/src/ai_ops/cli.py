"""저장 증거와 B snapshot의 규칙 조사·보고서 재조회 명령행 인터페이스."""

from __future__ import annotations

import argparse
import getpass
import json
import signal
import sqlite3
import threading
import sys
from datetime import datetime
from pathlib import Path
from ai_ops.path_policy import cli_paths
from typing import Sequence

from pydantic import ValidationError

from ai_ops.contracts import load_target_registry
from ai_ops.b_contracts import load_b_settings
from ai_ops.data_policy import ensure_external_model_allowed
from ai_ops.live_collectors import collect_snapshot
from ai_ops.evidence import load_evidence_bundle, redact_text
from ai_ops.incidents import IncidentStore, RunStopped
from ai_ops.openai_engine import AIConfig, analyze_ai, api_key_from_environment
from ai_ops.reports import DIAGNOSIS, format_kst
from ai_ops.observability import observe
from ai_ops.job_queue import JobQueue
from ai_ops.worker import Worker
from ai_ops.retention import QueueRetention


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
    collect = commands.add_parser("collect-investigate", help="등록 대상의 자료를 읽고 규칙 보고서를 저장한다")
    collect.add_argument("--bindings", type=Path, required=True)
    collect.add_argument("--runtime", type=Path, required=True)
    collect.add_argument("--metric-queries", type=Path, required=True)
    collect.add_argument("--target", required=True, help="등록된 대상 별칭이다")
    collect.add_argument("--store", type=Path, default=DEFAULT_STORE)
    collect.add_argument("--question", required=True)
    collect.add_argument("--request-id", required=True)
    collect.add_argument("--route")
    collect.add_argument("--operation")
    collect.add_argument("--mode", choices=("rules",), default="rules", help="현재 captured 입력은 규칙 조사만 지원한다")
    for name, help_text in (("queue-submit", "조사 요청을 영속 큐에 접수한다"),
                            ("queue-work", "큐의 읽기 전용 수집·규칙 보고서를 처리한다")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--bindings", type=Path, required=True)
        command.add_argument("--runtime", type=Path, required=True)
        command.add_argument("--metric-queries", type=Path, required=True)
        command.add_argument("--store", type=Path, default=DEFAULT_STORE)
        if name == "queue-submit":
            command.add_argument("--target", required=True)
            command.add_argument("--question", required=True)
            command.add_argument("--request-id", required=True)
            command.add_argument("--route")
            command.add_argument("--operation")
        else:
            command.add_argument("--serve", action="store_true", help="명시한 경우에만 연속 처리한다")
    for name, help_text in (("queue-show", "질문을 제외한 작업 상태를 조회한다"),
                            ("queue-cancel", "대기·진행 작업을 취소하고 늦은 게시를 막는다")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--store", type=Path, default=DEFAULT_STORE)
        command.add_argument("--job", required=True)
    retention = commands.add_parser("queue-retention", help="7일 보관표를 보고 명시한 경우에만 연결 자료를 삭제한다")
    retention.add_argument("--store", type=Path, default=DEFAULT_STORE)
    retention.add_argument("--job", help="선택한 작업 하나만 확인하거나 정리한다")
    retention.add_argument("--apply", action="store_true", help="기본 미리보기 대신 만료된 완료 작업의 자료를 삭제한다")
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


def _print_record(record) -> int:
    print(f"상태: {record.metadata['status']}")
    print(f"사건 ID: {record.incident_id}")
    print(f"실행 ID: {record.run_id}")
    if record.collection_audit is not None:
        print(f"수집 상태: {record.collection_audit.collection_status}")
    if record.metadata["status"] == "partial":
        if record.metadata.get("failure_reason") == "collection_incomplete":
            print("수집 자료가 불완전하여 규칙 분석 부분 결과를 저장했습니다.")
        else:
            print(f"AI 실패 사유: {record.metadata['failure_reason']} · 규칙 분석 부분 결과")
    if record.request is not None:
        print(f"요청 시각: {format_kst(record.request.requested_at)}")
    if record.report is not None:
        print(f"진단: {DIAGNOSIS[record.report.diagnosis_status]}")
    else:
        print("기존 실행은 완료되지 않았다. 새 조사는 다른 request ID로 시작한다.")
    return 0 if record.metadata["status"] == "completed" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = cli_paths(parser, parser.parse_args(argv), {'bundle': 'directory', 'registry': 'input', 'bindings': 'input', 'runtime': 'input', 'metric_queries': 'input', 'store': 'store'})
    try:
        if args.command.startswith("queue-"):
            queue = JobQueue(args.store)
            if args.command == "queue-retention":
                value = QueueRetention(queue).run(apply=args.apply, job_id=args.job)
                print(json.dumps(value, ensure_ascii=False, indent=2))
                return 2 if args.apply and any(item["action"] == "blocked" for item in value["entries"]) else 0
            if args.command in ("queue-show", "queue-cancel"):
                value = queue.cancel(args.job) if args.command == "queue-cancel" else queue.show(args.job)
            else:
                settings = load_b_settings(args.bindings, args.runtime, args.metric_queries)
                if args.command == "queue-submit":
                    job, duplicate = queue.submit(settings, target=args.target, question=args.question,
                        request_id=args.request_id, route=args.route, operation=args.operation)
                    value = {**queue.show(job), "duplicate": duplicate}
                else:
                    stopped = threading.Event()
                    worker = Worker(queue, settings, stopped=stopped)
                    if args.serve:
                        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
                        signal.signal(signal.SIGINT, lambda *_: stopped.set())
                        worker.serve()
                        return 0
                    value = worker.run_once()
            print(json.dumps(value if value is not None else {"state": "idle"}, ensure_ascii=False, indent=2))
            return 0
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
        if args.command == "collect-investigate":
            settings = load_b_settings(args.bindings, args.runtime, args.metric_queries)
            binding = next((item for item in settings.bindings.bindings if item.alias == args.target), None)
            if binding is None:
                raise ValueError("target alias is not registered")
            with observe(args.store, "collection") as observation:
                snapshot = collect_snapshot(settings, binding.target, route=args.route, operation=args.operation)
                observation["result"] = {"complete": "success", "partial": "partial", "unavailable": "failure"}[snapshot.audit.collection_status]
            with observe(args.store, "report") as observation:
                store = IncidentStore(args.store)
                record = store.investigate_snapshot(snapshot=snapshot, settings=settings,
                                                    question=args.question, request_id=args.request_id)
                observation["result"] = "success" if record.metadata["status"] == "completed" else "partial"
            return _print_record(record)
        if args.command == "investigate":
            registry = load_target_registry(args.registry)
            bundle = load_evidence_bundle(args.bundle, registry)
            if args.mode == "ai":
                ensure_external_model_allowed(bundle)
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
            record = store.investigate_bundle(bundle=bundle, registry=registry,
                                       question=args.question, request_id=args.request_id, **options)
            return _print_record(record)
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
            if record.metadata["status"] == "partial":
                print(json.dumps({"status": "partial", "failure_reason": record.metadata["failure_reason"],
                                  "report": record.report.model_dump(mode="json")}, ensure_ascii=False, indent=2))
            else:
                print(record.report.model_dump_json(indent=2))
        else:
            print(record.markdown, end="")
        return 0 if record.metadata["status"] == "completed" else 1
    except RunStopped as exc:
        print(f"실행 {exc.status}: 사건 {exc.incident_id}, 실행 {exc.run_id}", file=sys.stderr)
        if exc.reason:
            safe_reason, _ = redact_text(exc.reason)
            print(f"사유: {safe_reason}", file=sys.stderr)
        return 130 if exc.status == "cancelled" else 1
    except sqlite3.Error:
        print("큐 저장소 읽기·쓰기 실패: 접수·완료로 간주하지 마세요.", file=sys.stderr)
        return 2
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
