"""로컬 입력만 읽어 C-04 JSON·한글 판정 보고서를 저장한다. 네트워크 호출은 없다."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from ai_ops.c_verification import VerificationInput, VerificationPolicy, verify_recovery
from ai_ops.c_verification_archive import from_c03_archive
from ai_ops.path_policy import cli_paths, write_private_json


REASONS = {
    "evidence_missing": "필수 증거가 없습니다.",
    "collection_failed": "증거 수집이 실패했습니다.",
    "target_identity_mismatch": "관측한 대상의 UID가 실행 대상과 다릅니다.",
    "query_or_rule_definition_mismatch": "지표 조회식 또는 경보 규칙이 계획과 다릅니다.",
    "observation_window_unavailable_or_invalid": "실행 시각·관찰 창·자료의 최신성을 확인하지 못했습니다.",
    "sample_time_unavailable_or_outside_window": "관측 시각이 없거나 정해진 창을 벗어났습니다.",
    "insufficient_samples": "관측 표본 수가 부족합니다.",
    "unordered_duplicate_or_gapped_samples": "중복·역순 시각 또는 지나치게 긴 관측 공백이 있습니다.",
    "incomplete_observation_window": "최소 관찰 시간과 관찰 창 양끝을 충족하지 못했습니다.",
    "reading_unavailable_or_evaluation_failed": "측정값이 없거나 경보 평가가 정상적으로 확인되지 않았습니다.",
    "recovery_condition_not_met": "관측값이 정해진 회복 조건을 충족하지 못했습니다.",
}


def read_json(path):
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("입력 파일의 형식 또는 크기가 허용 범위를 벗어났습니다")
    return json.loads(path.read_bytes())


def render_report(report: dict) -> str:
    """보고서 표시만 KST로 바꾸고 내부 입력·해시는 UTC로 유지한다."""
    from zoneinfo import ZoneInfo

    at = datetime.fromisoformat(report["evaluated_at"]).astimezone(ZoneInfo("Asia/Seoul"))
    parts = [f'# C-04 복구 판정: {report["label"]}',
             f'판정 시각: {at:%Y-%m-%d %H:%M:%S} KST',
             f'자료 구분: {report["provenance"]}. 로컬 제공 자료이며 실시간 원본 인증을 대신하지 않습니다.',
             f'대상: ai-ops-lab/lab-api · 목표 revision: {report["merge_sha"]}',
             f'목표 이미지: {report["desired_digest"]}',
             f'입력 해시: {report["input_hash"]}', ""]
    policy = report["observation_policy"]
    parts += [f'관찰 기준: 최소 {policy["minimum_span_seconds"]}초·{policy["minimum_samples"]}개 표본, '
              f'최대 공백 {policy["maximum_gap_seconds"]}초. 내부 시간은 UTC입니다.', ""]
    for check in report["checks"]:
        state = {"passed": "통과", "failed": "실패", "unknown": "확인 불가"}[check["state"]]
        parts.append(f'- {check["kind"]} / {check["name"]}: {state} · 유효 표본 {check["eligible_samples"]}개')
        parts.extend("  - "+REASONS[x] for x in check["reasons"])
        if check["failed_readings"]:
            parts.append("  - 불일치 표본: "+json.dumps(check["failed_readings"], ensure_ascii=False))
    if report["unexpected_series"]:
        parts.append("- 계획에 없는 증거 항목이 있어 해결 판정을 유보합니다.")
    parts += ["", "후속 처리: "+("결과 보고" if report["service_resolved"] else "중지하고 결과·부족한 증거를 보고"),
              "이 판정은 후속 변경의 실행 승인이 아닙니다. 실행·추가 모델 호출은 하지 않았습니다.", ""]
    return "\n".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description="C-04 로컬 회복 검증; 설치·장애 주입·복구 실행 없음")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input", type=Path)
    mode.add_argument("--c03-archive", type=Path)
    parser.add_argument("--policy", type=Path, help="C-03 보존 자료 모드에서 사용할 명시적 확인 계획")
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(argv), {"input": "input", "c03_archive": "directory",
        "policy": "input", "output": "output"})
    try:
        if args.input:
            if args.policy is not None:
                raise ValueError("입력 파일에 이미 확인 계획이 있습니다")
            request = VerificationInput.model_validate(read_json(args.input))
        else:
            if args.policy is None:
                raise ValueError("보존 자료 모드에는 확인 계획이 필요합니다")
            from ai_ops.path_policy import permitted_path
            def archived(name):
                return read_json(permitted_path(args.c03_archive/name, "input"))
            request = from_c03_archive(proposal=archived("proposal.json"),
                receipt=archived("recovery-verified.json"), http=archived("c03-restored-http.json"),
                runtime=archived("c03-restored-runtime.json"),
                policy=VerificationPolicy.model_validate(read_json(args.policy)),
                evaluated_at=datetime.now(timezone.utc))
        report = verify_recovery(request)
        write_private_json(args.output, {"verification": report, "report_markdown": render_report(report)})
        print(json.dumps({"status": report["status"], "label": report["label"],
                          "input_hash": report["input_hash"], "execution_allowed": False}, ensure_ascii=False))
        return 0 if report["status"] == "resolved" else 1
    except (ValueError, TypeError, KeyError, OSError):
        parser.exit(2, "C-04 입력·경로·계약 검증 실패. 원본 오류와 자료 본문은 출력하지 않았습니다.\n")


if __name__ == "__main__":
    raise SystemExit(main())
