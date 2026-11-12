"""현재 Argo·Deployment·Lease와 고정 revision values를 조회해 제안 차단 사유를 남긴다."""

import argparse
import json
from pathlib import Path
import subprocess

from ai_ops.c_approval import (ApprovalGitHubReader, _blob, _entry, _lab_tree, _tree,
                               prepare_pr_link)
from ai_ops.c_execution_live import C3LiveReader
from ai_ops.c_pr_link import read_private_c_record
from ai_ops.c_live_preflight import SHA, assess_live_preflight
from ai_ops.c_recovery_proof import (assess_incident_request, assess_proposal_recheck,
                                     assess_recovery_proof)
from ai_ops.c_recovery import (IMAGE_REPOSITORY, RECOVERY_REPOSITORIES, REPOSITORY, IncidentProjection, RecoveryDraftStore,
                               _values, draft_verified_proposal, read_existing_request)
from ai_ops.c_operator import _client
from ai_ops.contracts import sha256_json
from ai_ops.normal_ci_contract import NormalCIBinding
from ai_ops.normal_ci_history import verify_normal_ci_history
from ai_ops.path_policy import cli_paths, permitted_path, write_private_json


def _run(*args: str) -> str:
    output = subprocess.run(args, capture_output=True, text=True, timeout=15, check=True).stdout
    if len(output.encode()) > 2 * 1024 * 1024:
        raise ValueError("복구 근거 조회 응답이 너무 큽니다")
    return output


def _collect(args, *, expected_lease_holder=None, include_snapshot=False,
             read_values=None):
    """원본 서비스와 인증된 사건을 매 호출마다 새로 조회한다."""
    projection = request = None
    repository = getattr(args, "recovery_repository", REPOSITORY)
    kubeconfig = str(Path(args.kubeconfig).expanduser().absolute())
    def get(namespace: str, kind: str, name: str) -> dict:
        return json.loads(_run("kubectl", "--kubeconfig", kubeconfig, "-n", namespace,
                               "get", kind, name, "-o", "json"))
    app = get("argocd", "application", "lab-target")
    deployment = get("ai-ops-lab", "deployment", "lab-api")
    lease = get("ai-ops-lab", "lease", "b6-scenario-lock")
    if repository not in RECOVERY_REPOSITORIES or (
            app["spec"]["source"].get("repoURL") != f"https://github.com/{repository}.git"):
        raise ValueError("선택한 복구 저장소와 Argo 원본이 다릅니다")
    revision = app["spec"]["source"]["targetRevision"]
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise ValueError("실습 Argo revision이 전체 SHA가 아닙니다")
    if read_values is None and repository != REPOSITORY:
        read_values = lambda revision: _github_values(revision, repository=repository)
    values = (read_values(revision) if read_values is not None else
              _run("git", "show", revision + ":ai-ops/lab/values.yaml").encode())
    result = assess_live_preflight(app, deployment, lease, values,
                                   expected_lease_holder=expected_lease_holder, repository=repository)
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
        if (account["Account"] != "764428911194" or cluster["status"] != "ACTIVE"
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
                                       binding, history, ecr, bad_absent,
                                       expected_lease_holder=expected_lease_holder, repository=repository)
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
    if include_snapshot:
        # C-03은 이 자료로 실행 경계를 판단한다. 긴 CI/ECR 조회 중 대상이나
        # 공유 잠금이 바뀌었다면 한 번의 수집 안에서도 닫힌 상태로 끝낸다.
        if (get("argocd", "application", "lab-target") != app
                or get("ai-ops-lab", "deployment", "lab-api") != deployment
                or get("ai-ops-lab", "lease", "b6-scenario-lock") != lease):
            raise ValueError("C-03 원본 조회 중 실습 대상 또는 잠금이 바뀌었습니다")
        return result, projection, request, values, {
            "application": app, "deployment": deployment, "lease": lease,
            "values_bytes": values,
        }
    return result, projection, request, values


def _github_values(revision, *, reader=None, repository=REPOSITORY):
    """관측 revision의 values를 로컬 checkout 대신 GitHub 고정 객체에서 읽는다."""
    reader = ApprovalGitHubReader(repository=repository) if reader is None else reader
    lab = _tree(reader, _lab_tree(reader, revision, repository), repository)
    return _blob(reader, _entry(lab, "values.yaml", "blob", "100644"), repository=repository)


def collect_c3_execution_snapshot(args, expected_lease=None):
    """C-03 조회 adapter용 원본 결과와 현재 대상 객체를 메모리에서 반환한다."""
    holder = None if expected_lease is None else expected_lease["holder"]
    result, _, _, _, snapshot = _collect(
        args, expected_lease_holder=holder, include_snapshot=True,
        read_values=lambda sha: _github_values(sha, repository=getattr(args, "recovery_repository", REPOSITORY)))
    return result, snapshot


def make_c3_live_reader(args):
    """기존 C-02 원본 수집을 C-03 실행 직전 조회 계약에 연결한다."""
    return C3LiveReader(lambda expected_lease: collect_c3_execution_snapshot(args, expected_lease))


def main() -> int:
    parser = argparse.ArgumentParser(description="C-02 현재 변경안 선행 조건 읽기 전용 감사")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--recovery-repository", choices=sorted(RECOVERY_REPOSITORIES), default=REPOSITORY)
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
    parser.add_argument("--existing-proposal-id")
    parser.add_argument("--proposal-audit", type=Path)
    parser.add_argument("--pr-link-output", type=Path)
    args = cli_paths(parser, parser.parse_args(), {"output": "output", "normal_binding": "input",
                                                  "operator_state_root": "store",
                                                  "proposal_output": "output", "proposal_audit": "input",
                                                  "pr_link_output": "output"})
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
    link_fields = (args.existing_proposal_id, args.proposal_audit, args.pr_link_output)
    link_requested = any(value is not None for value in link_fields)
    if link_requested:
        if (not all(value is not None for value in link_fields) or not args.proposal_recheck
                or args.proposal_output is not None or args.pr_link_output == args.output):
            parser.error("C-03 연결에는 기존 초안 ID·원본 감사·별도 출력 경로와 재조회가 필요합니다")
        try:
            permitted_path(args.pr_link_output, "store")
        except (ValueError, OSError):
            parser.error("C-03 연결 기록에는 등록된 개인 저장 폴더를 사용해야 합니다")
    result, _, _, _ = _collect(args)
    if args.proposal_recheck:
        if result.get("incident_ready"):
            refreshed, projection, request, values = _collect(args)
            result = assess_proposal_recheck(result, refreshed)
            if args.proposal_output is not None and result["proposal_ready"]:
                proposal = draft_verified_proposal(request, projection, result, values)
                RecoveryDraftStore(args.operator_state_root).save(request, proposal)
                write_private_json(args.proposal_output, proposal.model_dump(mode="json"))
            if link_requested and result["proposal_ready"]:
                try:
                    store = RecoveryDraftStore(args.operator_state_root)
                    actor = request.requester_github_id
                    proposal = store.read(args.existing_proposal_id, actor_github_id=actor)
                    original = json.loads(read_private_c_record(args.proposal_audit))
                    decision, link = prepare_pr_link(proposal, original, result)
                    if store.read(proposal.proposal_id, actor_github_id=actor) != proposal:
                        raise ValueError("proposal_changed_during_linking")
                    if link is not None:
                        write_private_json(args.pr_link_output, link.model_dump(mode="json"))
                        decision["link_hash"] = link.link_hash
                    result["c03_pr_link"] = decision
                except (ValueError, KeyError, TypeError, AttributeError, OSError, subprocess.SubprocessError):
                    parser.exit(1, "C-03 연결 실패. 초안·원본 감사·현재 상태·GitHub 소스와 Helm을 확인하세요.\n")
        else:
            result["proposal_ready"] = False
        if link_requested and "c03_pr_link" not in result:
            result["c03_pr_link"] = {
                "status": "live_proof_blocked", "pr_created": False, "execution_allowed": False,
            }
    write_private_json(args.output, result)
    if link_requested:
        print(result["c03_pr_link"]["status"])
    else:
        print("C-02 실시간 근거를 읽기 전용으로 기록했습니다. PR·승인·배포는 생성하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
