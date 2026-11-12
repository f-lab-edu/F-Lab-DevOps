"""새 실행별 승인과 실제 감시 확인 후 한 프로세스에서 C-04 실습을 진행한다."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

from ai_ops.c_trial_execution import C4TrialExecutor, TrialAuthorization, TrialPlan
from ai_ops.c_trial_github import GitHubMergeSession
from ai_ops.c_trial_source import TrialSource
from ai_ops.c_trial_transport import C4TrialTransport
from ai_ops.c_trial_bundle import verify_runtime_bundle
from ai_ops.c_trial_guards import GuardReader, private_json
from ai_ops.c_trial_schedule import RefreshingProbe, schedule_trial
from ai_ops.c_trial_read_budget import recovery_github_reader, approval_rejection_codes
from ai_ops.path_policy import cli_paths, permitted_path, write_private_json


def recovery_args(path, executor, transport, kubeconfig):
    from ai_ops.c_pr_link import RecoveryPRLink
    from ai_ops.c_recovery import RecoveryDraftStore

    value = private_json(path)
    fields = {"trial_id", "normal_binding", "normal_run_id", "normal_head", "operator_state_root",
              "incident_id", "incident_run_id", "request_id", "proposal_id", "proposal_audit",
              "pr_link", "pull_number"}
    if set(value) != fields or value["trial_id"] != executor.plan.trial_id:
        raise ValueError("현재 실습에 연결된 실제 사건·PR 문맥이 필요합니다")
    notice=private_json(path.parent/'review-notified.json')if executor.plan.schema_version==2 else None
    state = permitted_path(Path(value["operator_state_root"]), "store")
    context = SimpleNamespace(kubeconfig=kubeconfig, recovery_repository="joungGo/c03-execution-lab",
        normal_binding=permitted_path(Path(value["normal_binding"]), "input"),
        normal_run_id=value["normal_run_id"], normal_head=value["normal_head"], operator_state_root=state,
        incident_id=value["incident_id"], incident_run_id=value["incident_run_id"], request_id=value["request_id"])
    source = TrialSource(context, executor.handoff, transport, clock=executor.clock,
        **getattr(executor,'read_caches',{}))
    result=dict(drafts=RecoveryDraftStore(state), proposal_id=value["proposal_id"],
        pull_number=value["pull_number"], allowed_github_id=149856960, pr_author_id=354870297,
        original_recheck=private_json(Path(value["proposal_audit"])),
        pr_link=RecoveryPRLink.model_validate(private_json(Path(value["pr_link"]))),
        live_reader=source.reader(), reader=recovery_github_reader(executor.plan.manual_recovery_at,
            repository=context.recovery_repository, clock=executor.clock))
    if notice is not None:result['review_notice']=notice
    return result


def recover_and_observe(args, executor, transport, evidence):
    """고정 시각/준비 완료 실행이 공유하는 실제 리뷰·복구·C-04 관찰 경로."""
    plan, handoff = executor.plan, executor.handoff
    evidence["capture_job"] = executor.investigate()
    while datetime.now(timezone.utc) < plan.manual_recovery_at:
        executor.check_cancellation()
        if args.recovery_context.exists():
            receipt = executor.recover(**recovery_args(args.recovery_context, executor, transport, args.kubeconfig))
            evidence.update(status="c03_verified", phase="c04_observation", c03_handoff=receipt.model_dump(mode="json"))
            write_private_json(args.output.with_name(args.output.stem+"-c03.json"),
                               receipt.model_dump(mode="json"))
            from ai_ops.c_verification_collect import CollectionPlan, collect
            from ai_ops.c_verification_prepare import prepare_collection
            from ai_ops.c_verification_transport import KubectlReader
            from ai_ops.c_verification import verify_recovery

            os.environ["KUBECONFIG"] = args.kubeconfig
            reader = KubectlReader()
            prepared = prepare_collection(reader, handoff=receipt)
            evidence["collection_preparation"] = prepared
            if prepared["status"] != "ready":
                raise ValueError("새 C-03 인계 후 관찰 준비를 확인하지 못했습니다")
            request, audit = collect(CollectionPlan.model_validate(prepared["collection_plan"]), reader,
                                     enabled=True, cancel_check=executor.check_cancellation)
            executor.check_cancellation()
            evidence.update(collection=audit, verification=verify_recovery(request))
            evidence["status"] = evidence["verification"]["status"]
            break
        post=args.output.parent/'post-state.json'
        if post.exists():
            from ai_ops.c_trial_review_window import review_path_ended
            post_state=private_json(post)
            if review_path_ended(post_state):
                evidence['early_owned_fallback_reason']=post_state['stage']
                break
        time.sleep(2)
    if not executor.finished:
        executor.restore()
        evidence["status"] = "restored_without_c03_completion"


def main():
    parser = argparse.ArgumentParser(description="승인된 C-04 한 번의 실습 실행기")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--guard-evidence", type=Path, required=True)
    parser.add_argument("--recovery-context", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--review-notifier-socket", required=True)
    parser.add_argument("--execute-approved-trial", action="store_true", required=True)
    args = cli_paths(parser, parser.parse_args(), {"plan": "input", "authorization": "input",
        "guard_evidence": "input", "recovery_context": "output", "output": "output"})
    plan = TrialPlan.model_validate(private_json(args.plan))
    if plan.schema_version!=2:
        raise ValueError('새 실행에는 최소 리뷰 시간을 고정한 schema2 계획이 필요합니다')
    authorization = TrialAuthorization.model_validate(private_json(args.authorization))
    verify_runtime_bundle(Path(__file__).resolve().parents[2])
    from ai_ops.c_trial_notice import require_bridge
    require_bridge(plan,args.review_notifier_socket)
    from ai_ops.c_trial_aws import AWSImageReader
    image_cache = RefreshingProbe(plan, AWSImageReader())
    guard_cache = RefreshingProbe(plan, GuardReader(args.guard_evidence))
    transport = C4TrialTransport(args.kubeconfig, image_check=image_cache.require,
        guard_check=guard_cache.require, merge_session=GitHubMergeSession)
    executor = C4TrialExecutor(plan, transport)
    owner = {
        "trial_id": plan.trial_id, "plan_hash": plan.digest(), "pid": os.getpid(),
        "lease_uid": plan.lease_uid, "injection_holder": executor.lease.holder,
        "executor_holder": executor.executor_holder}
    write_private_json(args.output.with_name(args.output.stem+"-owner.json"), owner)
    from ai_ops.c_execution_control import ExecutionControl
    executor.control = ExecutionControl(args.output.parent, plan, owner, clock=executor.clock)
    evidence = {"trial_id": plan.trial_id, "plan_hash": plan.digest(), "status": "started", "fault_injected": False,
                "phase": "preparation"}
    def prepared(at):
        evidence.update(prepared_at=at.isoformat())
        write_private_json(args.output.with_name(args.output.stem+'-prepared.json'), {
            'trial_id':plan.trial_id,'plan_hash':plan.digest(),'prepared_at':at.isoformat(),
            'fault_injected':False,'same_process_pid':os.getpid()})
        print(json.dumps({'status':'prepared','prepared_at':at.isoformat()}),flush=True)
    try:
        image_cache.start()
        guard_cache.start()
        from ai_ops.c_trial_read_cache import warm_trial_reads
        from ai_ops.normal_ci_contract import NormalCIBinding
        prep=private_json(args.output.parent/'state.json')
        executor.read_caches=warm_trial_reads(plan,
            NormalCIBinding.model_validate(private_json(args.output.parent/'normal-binding.json')),
            prep['normal_run_id'],prep['normal_head'])
        # 준비 마감 전 같은 프로세스가 준비를 완료하고 예정 30초 전에 자동 진입한다.
        schedule_trial(executor, authorization, on_prepared=prepared)
        evidence.update(phase="scheduled_injection", automatic_entry_at=executor._moment().isoformat())
        # 소유권 객체는 주입부터 복구까지 이 프로세스 안에 유지된다.
        handoff = executor.inject(authorization)
        evidence.update(fault_injected=True, failure_handoff=handoff.model_dump(mode="json"))
        evidence["phase"] = "investigation_and_review"
        write_private_json(args.output.with_name(args.output.stem+"-handoff.json"), evidence)
        recover_and_observe(args, executor, transport, evidence)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        evidence.update(status="stopped", injection_attempted=executor.injected,
                        stop_step=executor.last_step,
                        stop_error_type=type(exc).__name__, image_probe_error=image_cache.error_type,
                        guard_probe_error=guard_cache.error_type)
        evidence['approval_rejection_codes'] = approval_rejection_codes(exc)
        if executor.injected and executor.handoff is None:
            evidence["fault_injected"] = None
            evidence["fault_observation"] = "unconfirmed_inspect_runtime"
        if not executor.finished and (executor.injected or executor.lease.uid is not None):
            try:
                if executor.injected:
                    executor.restore()
                    evidence["fallback"] = "verified_normal_restored"
                else:
                    executor.cancel_before_injection()
                    evidence["fallback"] = "verified_normal_before_injection"
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
                evidence["fallback"] = "lock_retained_inspect_current_owner_and_operation"
    finally:
        evidence['execution_control'] = executor.control.summary()
        evidence['probe_threads_stopped'] = True
        for probe in (image_cache,guard_cache):
            try:probe.close()
            except ValueError:
                evidence.update(status='stopped',probe_threads_stopped=False)
    write_private_json(args.output, evidence)
    print(json.dumps({"status": evidence["status"], "fault_injected": evidence["fault_injected"]}))
    return 0 if evidence["status"] == "resolved" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        raise SystemExit("C-04 실행 입력·자격 확인에 실패했습니다. 개인 실행 기록을 확인하세요.") from None
