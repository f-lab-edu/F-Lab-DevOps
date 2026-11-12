"""종료한 자기 실습 프로세스의 고정 소유권만 확인해 정상 SHA로 복귀한다."""

import argparse
import os
from pathlib import Path
import re
import subprocess

from ai_ops.b6_coordination import LabLease
from ai_ops.c_failure_handoff import FailureHandoff
from ai_ops.c_trial_execution import C4TrialExecutor, TrialAuthorization, TrialPlan
from ai_ops.c_trial_github import GitHubMergeSession
from ai_ops.c_trial_transport import C4TrialTransport
from ai_ops.path_policy import cli_paths, write_private_json
from run_c4_trial import private_json


def resume(plan, authorization, owner, transport, *, handoff=None, journal=None, reader=None):
    if (owner.get("trial_id") != plan.trial_id or owner.get("plan_hash") != plan.digest()
            or owner.get("lease_uid") != plan.lease_uid
            or authorization.trial_id != plan.trial_id or authorization.plan_hash != plan.digest()
            or authorization.fault_injection_approved is not True
            or authorization.approved_at > plan.approval_cutoff
            or not re.fullmatch(r"manual-[a-f0-9]{32}", owner.get("injection_holder", ""))
            or not re.fullmatch(r"c3-[a-f0-9]{32}", owner.get("executor_holder", ""))
            or type(owner.get("pid")) is not int or owner["pid"] <= 1):
        raise ValueError("원래 승인된 실습·소유권 기록이 다릅니다")
    try:
        os.kill(owner["pid"], 0)
    except ProcessLookupError:
        pass
    else:
        raise ValueError("원래 실행 프로세스가 살아 있어 원복 소유권을 재개하지 않습니다")
    executor = C4TrialExecutor(plan, transport, clock=getattr(transport, "clock", None))
    executor.lease = LabLease(transport.lease_command, owner["injection_holder"], clock=executor.clock)
    executor.lease.uid = plan.lease_uid
    executor.executor_holder = owner["executor_holder"]
    if journal is not None:
        from ai_ops.c_execution_control import verified_resume_merge
        from ai_ops.c_approval import ApprovalGitHubReader
        executor.merge_sha = verified_resume_merge(journal, plan=plan, owner=owner,
            reader=reader or ApprovalGitHubReader(repository='joungGo/c03-execution-lab'))
    if handoff is not None:
        if (handoff.trial_id != plan.trial_id or handoff.lease_uid != plan.lease_uid
                or handoff.owner_holder != owner["injection_holder"]
                or handoff.executor_holder != owner["executor_holder"]
                or handoff.application_uid != plan.application_uid
                or handoff.deployment_uid != plan.deployment_uid
                or handoff.normal_revision != plan.normal_revision
                or handoff.failure_revision != plan.failure_revision
                or handoff.normal_image != plan.normal_image or handoff.failure_image != plan.failure_image):
            raise ValueError("종료 프로세스의 실패 인계가 다릅니다")
        executor.handoff = handoff
    executor.injected = True
    executor.restore()
    return {"trial_id": plan.trial_id, "plan_hash": plan.digest(), "status": "verified_owned_rollback",
            "previously_merged_sha": executor.merge_sha, "new_merge_performed": False,
            "git_main_normalization": "read_back_and_review_if_seed_remains"}


def main():
    parser = argparse.ArgumentParser(description="종료한 C-04 실습의 정확한 기존 소유권 원복")
    for name in ("plan", "authorization", "owner", "handoff", "output"):
        parser.add_argument("--"+name, type=Path, required=name != "handoff")
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--journal", type=Path, help="병합 이후 중단됐으면 실제 Git 원본과 재대조할 실행 장부")
    args = cli_paths(parser, parser.parse_args(), {"plan": "input", "authorization": "input",
        "owner": "input", "handoff": "input", "journal": "input", "output": "output"})
    transport = C4TrialTransport(args.kubeconfig, image_check=lambda plan: False,
        guard_check=lambda plan: False, merge_session=GitHubMergeSession)
    record = private_json(args.handoff) if args.handoff else None
    handoff = FailureHandoff.model_validate(record.get("failure_handoff", record)) if record else None
    result = resume(TrialPlan.model_validate(private_json(args.plan)),
        TrialAuthorization.model_validate(private_json(args.authorization)), private_json(args.owner),
        transport, handoff=handoff, journal=args.journal)
    write_private_json(args.output, result)
    print("종료한 실습 소유권의 정상 원복을 확인했습니다.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        raise SystemExit("소유권·현재 SHA·실행 프로세스 상태가 달라 원복을 중지했습니다.") from None
