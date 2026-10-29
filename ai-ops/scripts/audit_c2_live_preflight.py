"""현재 Argo·Deployment·Lease와 고정 revision values를 조회해 제안 차단 사유를 남긴다."""

import argparse
import json
from pathlib import Path
import subprocess

from ai_ops.c_live_preflight import SHA, assess_live_preflight
from ai_ops.c_recovery_proof import (assess_incident_request, assess_proposal_recheck,
                                     assess_recovery_proof)
from ai_ops.c_recovery import (IMAGE_REPOSITORY, IncidentProjection, RecoveryDraftStore,
                               _values, draft_verified_proposal, read_existing_request)
from ai_ops.c_operator import _client
from ai_ops.contracts import sha256_json
from ai_ops.normal_ci_contract import NormalCIBinding
from ai_ops.normal_ci_history import verify_normal_ci_history
from ai_ops.path_policy import cli_paths, write_private_json


def _run(*args: str) -> str:
    output = subprocess.run(args, capture_output=True, text=True, timeout=15, check=True).stdout
    if len(output.encode()) > 2 * 1024 * 1024:
        raise ValueError("복구 근거 조회 응답이 너무 큽니다")
    return output


def _collect(args):
    """원본 서비스와 인증된 사건을 매 호출마다 새로 조회한다."""
    projection = request = None
    kubeconfig = str(Path(args.kubeconfig).expanduser().absolute())
    def get(namespace: str, kind: str, name: str) -> dict:
        return json.loads(_run("kubectl", "--kubeconfig", kubeconfig, "-n", namespace,
                               "get", kind, name, "-o", "json"))
    app = get("argocd", "application", "lab-target")
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    lease = get("ai-ops-lab", "lease", "b6-scenario-lock")
    revision = app["spec"]["source"]["targetRevision"]
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise ValueError("실습 Argo revision이 전체 SHA가 아닙니다")
    values = _run("git", "show", revision + ":ai-ops/lab/values.yaml").encode()
    result = assess_live_preflight(app, deployment, lease, values)
    if args.normal_binding is not None and result["blockers"] == ["prior_digest_and_failure_proof_required"]:
        if args.normal_binding.stat().st_size > 8192:
            raise ValueError("정상 CI 결합값 파일이 너무 큽니다")
        binding = NormalCIBinding.model_validate_json(args.normal_binding.read_bytes())
        history = verify_normal_ci_history(args.normal_run_id, args.normal_head, binding)
        cluster = json.loads(_run("aws", "eks", "describe-cluster", "--region", "ap-northeast-2",
                                  "--name", "urlshortener", "--output", "json"))["cluster"]
        account = json.loads(_run("aws", "sts", "get-caller-identity", "--output", "json"))
        kube = json.loads(_run("kubectl", "--kubeconfig", kubeconfig, "config", "view",
                               "--minify", "--raw", "-o", "json"))
        connection = kube["clusters"][0]["cluster"]
        fingerprint = sha256_json({"arn": cluster["arn"], "endpoint": cluster["endpoint"],
                                   "ca": cluster["certificateAuthority"]["data"]})
        if (account["Account"] != "716174522908" or cluster["status"] != "ACTIVE"
                or cluster["arn"] != binding.cluster_arn or fingerprint != binding.cluster_fingerprint
                or connection.get("server") != cluster["endpoint"]
                or connection.get("certificate-authority-data") != cluster["certificateAuthority"]["data"]):
            raise ValueError("정상 CI와 현재 클러스터의 동일성을 확인하지 못했습니다")
        def ecr_lookup(digest: str) -> tuple[dict, bool]:
            command = ("aws", "ecr", "describe-images", "--region", "ap-northeast-2",
                       "--repository-name", "urlshortener", "--image-ids",
                       "imageDigest=" + digest, "--output", "json")
            response = subprocess.run(command, capture_output=True, text=True, timeout=15)
            if response.returncode and "ImageNotFoundException" in response.stderr:
                return {"imageDetails": []}, True
            if response.returncode or len(response.stdout.encode()) > 65536:
                raise ValueError("ECR 이미지 조회를 확인하지 못했습니다")
            return json.loads(response.stdout), False
        old_digest = binding.app.image.removeprefix(IMAGE_REPOSITORY + "@")
        ecr, _ = ecr_lookup(old_digest)
        bad_digest = _values(values)["api"]["image"]["digest"]
        _, bad_absent = ecr_lookup(bad_digest)
        pods = json.loads(_run("kubectl", "--kubeconfig", kubeconfig, "-n", "ai-ops-lab",
                               "get", "pods", "-l", "app=lab-api", "-o", "json"))["items"]
        owners = pods[0].get("metadata", {}).get("ownerReferences", []) if len(pods) == 1 else []
        replica_set = get("ai-ops-lab", "replicaset", owners[0]["name"]) if len(owners) == 1 \
            and owners[0].get("kind") == "ReplicaSet" else {}
        result = assess_recovery_proof(app, deployment, lease, values, replica_set, pods,
                                       binding, history, ecr, bad_absent)
        if args.operator_state_root is not None and result["proof_ready"]:
            shown = _client(args.operator_state_root / "operator.sock", "show",
                            args.incident_id, args.incident_run_id)
            if shown.get("status") != "ok":
                raise ValueError("인증된 사건 조회가 거부됐습니다")
            projection = IncidentProjection.model_validate(shown["projection"])
            request = read_existing_request(args.operator_state_root, args.request_id)
            github_id = _run("gh", "api", "user", "--jq", ".id").strip()
            if not github_id.isdecimal() or request.requester_github_id != int(github_id):
                raise ValueError("현재 운영자와 해결 요청의 GitHub ID가 다릅니다")
            result = assess_incident_request(result, projection, request)
    return result, projection, request, values


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 현재 변경안 선행 조건 읽기 전용 감사")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--normal-binding", type=Path)
    parser.add_argument("--normal-run-id", type=int)
    parser.add_argument("--normal-head")
    parser.add_argument("--operator-state-root", type=Path)
    parser.add_argument("--incident-id")
    parser.add_argument("--incident-run-id")
    parser.add_argument("--request-id")
    parser.add_argument("--proposal-recheck", action="store_true")
    parser.add_argument("--proposal-output", type=Path)
    args = cli_paths(parser, parser.parse_args(), {"output": "output", "normal_binding": "input",
                                                  "operator_state_root": "store",
                                                  "proposal_output": "output"})
    if any(value is not None for value in (args.normal_binding, args.normal_run_id, args.normal_head)) \
            and not all(value is not None for value in (args.normal_binding, args.normal_run_id, args.normal_head)):
        parser.error("정상 CI 근거의 결합값·실행 ID·head SHA를 함께 지정해야 합니다")
    incident_fields = (args.operator_state_root, args.incident_id, args.incident_run_id, args.request_id)
    if any(value is not None for value in incident_fields) and (not all(value is not None for value in incident_fields)
                                                               or args.normal_binding is None):
        parser.error("사건 연결에는 운영자 상태 폴더·사건/실행/요청 ID와 정상 CI 근거가 함께 필요합니다")
    if args.proposal_output is not None and not args.proposal_recheck:
        parser.error("변경안 출력에는 실시간 재조회가 필요합니다")
    if args.proposal_output is not None and args.proposal_output == args.output:
        parser.error("감사 결과와 변경안은 서로 다른 파일에 기록해야 합니다")
    if args.proposal_recheck and not all(value is not None for value in incident_fields):
        parser.error("재조회에는 인증된 사건과 해결 요청이 필요합니다")
    result, _, _, _ = _collect(args)
    if args.proposal_recheck:
        if result.get("incident_ready"):
            refreshed, projection, request, values = _collect(args)
            result = assess_proposal_recheck(result, refreshed)
            if args.proposal_output is not None and result["proposal_ready"]:
                proposal = draft_verified_proposal(request, projection, result, values)
                RecoveryDraftStore(args.operator_state_root).save(request, proposal)
                write_private_json(args.proposal_output, proposal.model_dump(mode="json"))
        else:
            result["proposal_ready"] = False
    write_private_json(args.output, result)
    print("C-02 실시간 근거를 읽기 전용으로 기록했습니다. PR·승인·배포는 생성하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
