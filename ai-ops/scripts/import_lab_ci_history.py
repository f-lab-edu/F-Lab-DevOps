"""운영자 인증으로 S10 이력을 검증하고 현재 읽기 수집과 함께 개인 폴더에 저장한다."""

import argparse
import base64
import subprocess
from pathlib import Path

from ai_ops.b_contracts import load_b_settings
from ai_ops.evidence import _json_object
from ai_ops.lab_ci_history import CLUSTER, verify_lab_ci_history
from ai_ops.live_collectors import collect_snapshot
from ai_ops.path_policy import cli_paths, write_private_json


def verify_cluster(settings, binding):
    """AWS에서 확인한 클러스터 ARN·HTTPS 주소·인증서를 실제 읽기 연결과 대조한다."""
    response = subprocess.run(["aws", "eks", "describe-cluster", "--region", "ap-northeast-2",
                               "--name", "urlshortener", "--output", "json"],
                              check=True, capture_output=True, timeout=30)
    if len(response.stdout) > 2 * 1024 * 1024:
        raise ValueError("cluster description too large")
    cluster = _json_object(response.stdout)["cluster"]
    connection = settings.runtime.connections[binding.kubernetes_connection]
    certificate = Path(connection.ca_file) if connection.ca_file else None
    # 연결 계약에 등록된 인증서만 읽는다. 일반 자료 입력과 인증서 형식은 구분한다.
    if certificate is None:
        raise ValueError("explicit cluster certificate required")
    if (cluster["arn"] != CLUSTER or cluster["endpoint"].rstrip("/") != connection.base_url
            or certificate.stat().st_size > 65536
            or certificate.read_bytes().strip() != base64.b64decode(cluster["certificateAuthority"]["data"], validate=True).strip()):
        raise ValueError("cluster connection identity rejected")


def main():
    parser = argparse.ArgumentParser(description="S10 CI 검증 이력을 현재 실습 대상의 읽기 조사 입력으로 가져오기")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"bindings": "input", "runtime": "input",
                                                "metrics": "input", "output": "output"})
    try:
        settings = load_b_settings(args.bindings, args.runtime, args.metrics)
        bindings = [item for item in settings.bindings.bindings if item.alias == "url-shortener-b6-lab"]
        if len(bindings) != 1:
            raise ValueError("lab binding missing")
        binding = bindings[0]
        verify_cluster(settings, binding)
        history = verify_lab_ci_history(args.run_id, args.expected_head)
        snapshot = collect_snapshot(settings, binding.target, ci_history=history)
        entries = [entry for entry in snapshot.audit.entries if entry.time_field == "artifact_observed_at"]
        if len(entries) != 2 or any(entry.status not in {"available", "stale"} for entry in entries):
            raise ValueError("lab CI evidence binding rejected")
        write_private_json(args.output, {"schema_version": 1, "history": history.summary(),
            "cluster_identity_verified": True, "snapshot": snapshot.bundle.model_dump(mode="json"),
            "audit": snapshot.audit.model_dump(mode="json"), "external_model_calls": 0})
    except Exception:
        # 예외·응답 원문과 인증값을 출력하지 않는다.
        parser.exit(1, "S10 이력 가져오기 실패: 출처·대상·시각·연결 권한을 확인하세요.\n")
    print("S10 검증 이력을 저장했습니다. 오래된 관측은 stale로 보존하며 현재 증거로 사용하지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
