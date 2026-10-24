"""정제 입력의 비공개 미리보기·승인·모의 비교·조회·정리를 제공한다."""

import argparse
import json
from pathlib import Path

from ai_ops.contracts import canonical_json_bytes
from ai_ops.model_comparison import ComparisonStore, rules_projection
from ai_ops.model_projection import ModelView, parse_projection
from ai_ops.path_policy import cli_paths
from ai_ops.projected_model_engine import CallPlan
from ai_ops.projected_model_transport import LiveOpenAITransport


def mock_transport(path, body, *, timeout):
    """외부 네트워크 없이 고정된 규칙 요약을 반환한다. 모델 품질 근거가 아니다."""
    if path == "/v1/responses/input_tokens":
        return {"object": "response.input_tokens", "input_tokens": len(canonical_json_bytes(body))}
    payload = json.loads(body["input"][0]["content"])
    report = rules_projection(ModelView.model_validate_json(canonical_json_bytes(payload["view"])))
    return {"status": "completed", "model": "gpt-6-sol", "usage": {"input_tokens": 100, "output_tokens": 100},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": report.model_dump_json()}]}]}


mock_transport.comparison_mode = "mock"


def main(argv=None):
    parser = argparse.ArgumentParser(description="정제 입력 비교. 실제 전송은 live 계획·일회 승인·보관 조건 확인 필요")
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--projection", type=Path, required=True)
    prep.add_argument("--plan", type=Path, required=True)
    for action in ("preview", "preview-html", "approve", "run-mock", "run-live", "show"):
        child = sub.add_parser(action); child.add_argument("--id", required=True)
        if action == "approve":
            child.add_argument("--binding-hash", required=True); child.add_argument("--actor", required=True)
            child.add_argument("--acknowledge-provider-retention", action="store_true")
            child.add_argument("--confirm-optional-data-sharing-off", action="store_true")
    sub.add_parser("purge").add_argument("--apply", action="store_true")
    args = cli_paths(parser, parser.parse_args(argv), {"projection": "input", "plan": "input"})
    try:
        store = ComparisonStore()
        if args.action == "prepare":
            if args.plan.stat().st_size > 8192 or args.projection.stat().st_size > 256*1024:
                raise ValueError()
            identifier = store.prepare(parse_projection(args.projection.read_bytes()),
                                       CallPlan.model_validate_json(args.plan.read_bytes()))
            result = {"id": identifier, "state": "prepared_or_existing"}
        elif args.action == "preview":
            result = store.preview(args.id)
        elif args.action == "preview-html":
            result = {"path": str(store.write_preview(args.id))}
        elif args.action == "approve":
            store.approve(args.id, binding_hash=args.binding_hash, actor=args.actor,
                          acknowledge_provider_retention=args.acknowledge_provider_retention,
                          confirm_optional_data_sharing_off=args.confirm_optional_data_sharing_off)
            result = {"id": args.id, "state": "approved"}
        elif args.action == "run-mock":
            result = store.run(args.id, transport=mock_transport)
        elif args.action == "run-live":
            record = store.show(args.id)
            if record["plan"]["execution_mode"] != "live":
                raise ValueError("comparison_live_plan_required")
            result = store.run(args.id, transport=LiveOpenAITransport(record["plan"]["credential_fingerprint"]))
        elif args.action == "show":
            result = store.show(args.id)
        else:
            result = store.purge(apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        parser.exit(1, "정제 비교 처리 실패: 비공개 입력·승인·상태·예산·경로를 확인하세요.\n")


if __name__ == "__main__":
    raise SystemExit(main())
