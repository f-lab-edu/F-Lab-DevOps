"""실패 진단을 보존하는 새 비교의 미리보기·승인·실행·안전한 조회를 제공한다."""

import argparse
import json
from pathlib import Path

from ai_ops.model_sequence_projection import parse_sequence_projection
from ai_ops.path_policy import cli_paths
from ai_ops.projected_model_engine import CallPlan
from ai_ops.projected_model_transport import LiveOpenAITransport
from ai_ops.sequence_diagnostics import DiagnosticSequenceStore
from compare_sequence_projection import mock_transport


def main(argv=None):
    parser = argparse.ArgumentParser(description="별도 v3 진단 비교; 초안은 비공개로 최대 7일 보존")
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--projection", type=Path, required=True)
    prep.add_argument("--plan", type=Path, required=True)
    for action in ("preview-html", "approve", "run-mock", "run-live", "show"):
        child = sub.add_parser(action); child.add_argument("--id", required=True)
        if action == "approve":
            child.add_argument("--binding-hash", required=True)
            child.add_argument("--actor", required=True)
            child.add_argument("--acknowledge-provider-retention", action="store_true")
            child.add_argument("--confirm-optional-data-sharing-off", action="store_true")
    sub.add_parser("purge").add_argument("--apply", action="store_true")
    args = cli_paths(parser, parser.parse_args(argv), {"projection": "input", "plan": "input"})
    try:
        store = DiagnosticSequenceStore()
        if args.action == "prepare":
            if args.plan.stat().st_size > 8192 or args.projection.stat().st_size > 256*1024:
                raise ValueError("diagnostic comparison input too large")
            identifier = store.prepare(parse_sequence_projection(args.projection.read_bytes()),
                CallPlan.model_validate_json(args.plan.read_bytes()))
            result = {"id": identifier, "state": "prepared_or_existing"}
        elif args.action == "preview-html":
            result = {"path": str(store.write_preview(args.id))}
        elif args.action == "approve":
            store.approve(args.id, binding_hash=args.binding_hash, actor=args.actor,
                acknowledge_provider_retention=args.acknowledge_provider_retention,
                confirm_optional_data_sharing_off=args.confirm_optional_data_sharing_off)
            result = {"id": args.id, "state": "approved"}
        elif args.action in {"run-mock", "run-live"}:
            record = store.show(args.id)
            transport = mock_transport if args.action == "run-mock" else LiveOpenAITransport(record["plan"]["credential_fingerprint"])
            store.run(args.id, transport=transport)
            result = store.summary(args.id)
        elif args.action == "show":
            result = store.summary(args.id)
        else:
            result = store.purge(apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        parser.exit(1, "진단 비교 실패: 비공개 경로·입력·승인·상태·예산을 확인하세요.\n")


if __name__ == "__main__":
    raise SystemExit(main())
