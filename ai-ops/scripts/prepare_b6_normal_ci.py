"""관리자가 현재 Pod·클러스터를 읽어 정상 CI의 대상과 RBAC 계획만 만든다."""

import argparse
from pathlib import Path

from ai_ops.b6_lab import CONTEXT, NAMESPACE
from ai_ops.b6_sequence_capture import SequenceLabClient
from ai_ops.normal_ci_contract import NormalCIBinding, executor_hash
from ai_ops.normal_ci_rbac import build_normal_rbac
from ai_ops.normal_ci_runner import NormalCIClient
from ai_ops.path_policy import cli_paths, write_private_json
from prepare_model_projection import current_identity


def prepare(*, client=None, read_identity=current_identity):
    client = client or SequenceLabClient()
    collector = client.get(NAMESPACE, "deployment", "lab-collector")
    image = collector["spec"]["template"]["spec"]["containers"][0]["image"]
    current = read_identity(image)
    app = client.pin_app()
    binding = NormalCIBinding.model_validate({"cluster_arn": CONTEXT,
        "cluster_fingerprint": current["cluster_fingerprint"], "executor_sha256": executor_hash(),
        "app": {"name": app["pod_name"], "uid": app["pod_uid"],
                "deployment_uid": app["deployment_uid"], "image": app["image"]},
        "collector": {"name": current["pod_name"], "uid": current["pod_uid"],
                      "deployment_uid": current["collector_uid"], "image": image}})
    # 실제 실행자가 사용할 읽기 검사와 관리자에게만 허용된 초기 확인을 구분한다.
    baseline = NormalCIClient(binding).guard()
    if (read_identity(image) != current or client.pin_app() != app
            or baseline["app"] != app):
        raise ValueError("normal_ci_preparation_target_changed")
    return {"schema_version": 1, "binding": binding.model_dump(mode="json"),
        "rbac": build_normal_rbac(binding), "aws_applied": False, "rbac_applied": False,
        "source_module_verified": False, "ready_for_execution": False,
        "workflow_dispatched": False, "external_model_calls": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description="정상 관측 CI 현재 Pod 고정·권한 계획 생성; 적용하지 않음")
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(argv), {"output": "output"})
    try:
        write_private_json(args.output, prepare())
        print("정상 관측 CI 대상·RBAC 계획을 저장했습니다. 실제 적용과 CI 실행은 수행하지 않았습니다.")
        return 0
    except Exception:
        parser.exit(1, "정상 관측 CI 준비 실패: 클러스터·Pod 소유·기준선·공유 설정을 확인하세요.\n")


if __name__ == "__main__":
    raise SystemExit(main())
