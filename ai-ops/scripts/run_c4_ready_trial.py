"""준비 영수증을 게시하고 새 실제 승인 파일을 받으면 같은 프로세스에서 즉시 실행한다."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import threading
import time
from types import SimpleNamespace

from ai_ops.c_trial_ready import ReadyTrialPlan, ReadyApproval, ReadyTrialController
from ai_ops.c_trial_ready_transport import ReadyTrialTransport, StandbyGuardReader
from ai_ops.c_trial_schedule import RefreshingProbe, validate_operator_socket
from ai_ops.c_trial_bundle import verify_ready_runtime
from ai_ops.c_trial_guards import private_json
from ai_ops.c_trial_notice import _socket_path, require_bridge
from ai_ops.c_trial_github import GitHubMergeSession
from ai_ops.normal_ci_contract import NormalCIBinding
from ai_ops.c_trial_read_cache import warm_trial_reads
from ai_ops.c_trial_read_budget import approval_rejection_codes, rejection_source_frames
from ai_ops.contracts import sha256_json
from ai_ops.path_policy import cli_paths, write_private_json
from run_c4_trial import recover_and_observe
from ai_ops.c_operator import OperatorServer
from ai_ops.c_projection_client import ProjectionClient


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='C-04 준비 완료 후 실제 승인으로 즉시 시작')
    for name in ('intent', 'preflight', 'guard-evidence', 'normal-binding', 'projection-probe', 'c2-kubeconfig'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('approval', 'readiness', 'activation', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--operator-state-root', required=True)
    parser.add_argument('--normal-run-id', type=int, required=True)
    parser.add_argument('--review-notifier-socket', required=True)
    parser.add_argument('--execute-approved-trial', action='store_true', required=True)
    return cli_paths(parser, parser.parse_args(argv), {
        **{n: 'input' for n in ('intent', 'preflight', 'guard_evidence', 'normal_binding', 'projection_probe', 'c2_kubeconfig')},
        'approval': 'store', 'readiness': 'output', 'activation': 'output',
        'run_dir': 'store', 'output': 'output'})


def publish_execution(activation, executor, run_dir, activation_path):
    """감시 활성화/첫 Argo 쓰기 전에 기존 수동 원복 도구가 읽을 소유 자료를 게시한다."""
    write_private_json(activation_path, activation.model_dump(mode='json'))
    write_private_json(run_dir/'active-plan.json', activation.execution.model_dump(mode='json'))
    write_private_json(run_dir/'derived-authorization.json', activation.authorization().model_dump(mode='json'))
    owner = {
        'trial_id': activation.intent.trial_id, 'plan_hash': executor.plan.digest(), 'pid': os.getpid(),
        'lease_uid': executor.plan.lease_uid, 'injection_holder': executor.lease.holder,
        'executor_holder': executor.executor_holder}
    write_private_json(run_dir/'result-owner.json', owner)
    from ai_ops.c_execution_control import ExecutionControl
    executor.control = ExecutionControl(run_dir, executor.plan, owner, clock=executor.clock)


def main(argv=None):
    if not __debug__:
        raise ValueError('검증을 제거하는 최적화 모드는 사용할 수 없습니다')
    args = parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    intent = ReadyTrialPlan.model_validate(private_json(args.intent))
    verify_ready_runtime(root, intent)
    preflight = private_json(args.preflight)
    if preflight['plan_hash'] != intent.digest() or sha256_json(preflight['checks']) != intent.preflight_sha256:
        raise ValueError('ready_preflight_changed')
    if args.approval.exists():
        raise ValueError('ready_approval_must_follow_current_preparation')
    _socket_path(args.review_notifier_socket, existing=True)
    args.run_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    if any(not p.resolve().is_relative_to(args.run_dir.resolve()) for p in
           (args.approval, args.readiness, args.activation, args.output)):
        raise ValueError('ready_run_outputs_must_share_new_directory')
    binding = NormalCIBinding.model_validate(private_json(args.normal_binding))
    from ai_ops.c_trial_aws import AWSImageReader
    images = RefreshingProbe(intent.source_plan(), AWSImageReader())
    guards = RefreshingProbe(intent, StandbyGuardReader(args.guard_evidence,
        preflight['checks']['rollback_workflow_hashes']))
    def standby_reader(current):
        guards.require(current)
        with guards.lock:
            return {role: guards.value[role] for role in ('local', 'remote')}
    transport = ReadyTrialTransport(args.kubeconfig, intent=intent, normal_binding=binding,
        image_check=images.require, standby_reader=standby_reader,
        merge_session=GitHubMergeSession)
    controller = ReadyTrialController(intent, transport, args.run_dir/'approval-consumed.json')
    evidence = {'trial_id': intent.trial_id, 'intent_hash': intent.digest(),
                'status': 'preparing', 'fault_injected': False}
    post = forward = server = None
    try:
        images.start(); guards.start()
        caches = warm_trial_reads(intent.source_plan(), binding, args.normal_run_id,
                                  intent.targets['workflow_main'])
        end = time.monotonic()+15
        while True:
            try:
                images.require(intent.source_plan()); guards.require(intent); break
            except ValueError:
                if time.monotonic() >= end or images.error_type or guards.error_type:
                    raise
                time.sleep(.1)
        operator_root = Path(args.operator_state_root)
        if operator_root != Path('/private/tmp')/('c04-'+str(intent.trial_id)):
            raise ValueError('ready_operator_directory_rejected')
        validate_operator_socket(operator_root)
        if (operator_root/'operator.sock').exists():
            raise ValueError('ready_operator_socket_already_active')
        pods = json.loads(transport._kube('-n', 'ai-ci', 'get', 'pods', '-l', 'app=ci-worker', '-o', 'json'))
        if pods.get('metadata', {}).get('continue') or len(pods['items']) != 1:
            raise ValueError('ready_worker_pod_ambiguous')
        worker = pods['items'][0]
        transport.prepared_worker_pod = {k: worker['metadata'][k] for k in ('name', 'uid')}
        if not all(c['ready'] for c in worker['status']['containerStatuses']):
            raise ValueError('ready_worker_pod_unready')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        with (args.run_dir/'projection-forward.log').open('xb') as log:
            os.fchmod(log.fileno(), 0o600)
            forward = subprocess.Popen(['kubectl', '--kubeconfig', str(args.c2_kubeconfig), '-n', 'ai-ci',
                'port-forward', '--address=127.0.0.1', 'pod/'+worker['metadata']['name'], str(port)+':9444'],
                stdout=log, stderr=subprocess.STDOUT)
        end = time.monotonic()+20
        while True:
            if forward.poll() is not None:
                raise ValueError('ready_projection_forward_stopped')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.5): break
            except OSError:
                if time.monotonic() >= end: raise ValueError('ready_projection_forward_timeout') from None
                time.sleep(.1)
        identity = Path.home()/'.local/share/url-shortener-ai-ops/c2-projection-identity-20261028'
        projection = ProjectionClient(identity/'token', identity/'tls.crt', port=port)
        probe = private_json(args.projection_probe)
        if projection.fetch(probe['incident_id'], probe['run_id']).bundle_hash != probe['bundle_hash']:
            raise ValueError('ready_projection_bundle_changed')
        os.environ['AI_OPS_FILES_ROOT'] = str(operator_root)
        server = OperatorServer(operator_root, None, 149856960, projection_client=projection)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        write_private_json(args.run_dir/'state.json', {'normal_run_id': args.normal_run_id,
            'normal_head': intent.targets['workflow_main'], 'operator_state_root': str(operator_root),
            'worker_pod': {'name': worker['metadata']['name'], 'uid': worker['metadata']['uid']}})
        write_private_json(args.run_dir/'normal-binding.json', binding.model_dump(mode='json'))
        receipt = controller.prepare()
        write_private_json(args.readiness, receipt.model_dump(mode='json'))
        print(json.dumps({'status': 'ready_awaiting_actual_approval', 'trial_id': intent.trial_id,
            'intent_hash': intent.digest(), 'readiness_hash': receipt.digest(),
            'expires_at': receipt.expires_at.isoformat()}), flush=True)
        while not args.approval.exists():
            if (args.run_dir/'abort').exists() or controller.moment() >= receipt.expires_at:
                raise ValueError('ready_approval_expired_or_aborted')
            time.sleep(.2)
        approval = ReadyApproval.model_validate(private_json(args.approval))
        verify_ready_runtime(root, intent)
        original_arm = transport.arm_ready_guards

        def arm(activation, lease):
            nonlocal post
            # 실제 상대 기한 승인의 파생 자료다. 시각을 새로 승인받았다고 기록하지 않는다.
            publish_execution(activation, controller.executor, args.run_dir, args.activation)
            require_bridge(activation.execution, args.review_notifier_socket)
            original_arm(activation, lease)

        transport.arm_ready_guards = arm
        executor, handoff = controller.start(approval)
        executor.read_caches = caches
        evidence.update(status='investigation_and_review', fault_injected=True,
                        failure_handoff=handoff.model_dump(mode='json'), plan_hash=executor.plan.digest())
        write_private_json(args.run_dir/'result-handoff.json', evidence)
        # 기존 C-02/PR 연결기는 같은 실제 activation에서 파생한 계획과 승인만 사용한다.
        log = (args.run_dir/'post.log').open('xb')
        os.fchmod(log.fileno(), 0o600)
        post = subprocess.Popen([sys.executable, str(root/'ai-ops/scripts/prepare_c4_recovery_pr.py'),
            '--plan', str(args.run_dir/'active-plan.json'), '--authorization',
            str(args.run_dir/'derived-authorization.json'), '--run-dir', str(args.run_dir),
            '--kubeconfig', args.kubeconfig, '--review-notifier-socket', args.review_notifier_socket,
            '--execute-approved-trial'], stdout=log, stderr=subprocess.STDOUT)
        recovery = SimpleNamespace(output=args.output, kubeconfig=args.kubeconfig,
                                   recovery_context=args.run_dir/'recovery-context.json')
        recover_and_observe(recovery, executor, transport, evidence)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        evidence.update(status='stopped', error_type=type(exc).__name__)
        evidence['approval_rejection_codes'] = approval_rejection_codes(exc)
        evidence['rejection_source_frames'] = rejection_source_frames(exc)
        executor = controller.executor
        if executor is not None and executor.injected:
            evidence['fault_injected'] = True if executor.handoff is not None else None
        if executor is not None and not executor.finished and not controller.fallback_attempted:
            try:
                if executor.injected:
                    executor.restore(); evidence['fallback'] = 'verified_normal_restored'
                elif executor.lease.uid is not None:
                    executor.cancel_before_injection()
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
                evidence['fallback'] = 'lock_retained_inspect_current_owner'
    finally:
        if controller.executor is not None and controller.executor.control is not None:
            evidence['execution_control'] = controller.executor.control.summary()
        if post is not None:
            if post.poll() is None:
                post.terminate()
                try: post.wait(timeout=15)
                except subprocess.TimeoutExpired: post.kill(); post.wait(timeout=5)
        for process in (forward,):
            if process is not None and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        if server is not None:
            server.shutdown(); server.server_close()
            (operator_root/'operator.sock').unlink(missing_ok=True)
        for probe in (images, guards):
            try: probe.close()
            except ValueError: evidence.update(status='stopped', probe_cleanup_complete=False)
        if controller.executor is not None and controller.executor.finished:
            try:
                transport.clear_ready_control()
                evidence['ready_control_removed'] = True
                job = evidence.get('capture_job')
                if job is not None:
                    current = json.loads(transport._kube('-n', 'ai-ci', 'get', 'job', job['name'],
                                                       '--ignore-not-found', '-o', 'json') or b'{}')
                    if current:
                        if current['metadata']['uid'] != job['uid'] or current['status'].get('succeeded') != 1:
                            raise ValueError('ready_capture_cleanup_uid_or_status_changed')
                        transport._kube('-n', 'ai-ci', 'delete', 'job', job['name'], '--wait=true', '--timeout=15s')
                    evidence['capture_job_removed'] = True
            except (ValueError, KeyError, OSError, subprocess.SubprocessError):
                evidence.update(status='stopped', ready_cleanup_needs_check=True)
    write_private_json(args.output, evidence)
    print(json.dumps({'status': evidence['status'], 'fault_injected': evidence['fault_injected']}), flush=True)
    return 0 if evidence['status'] == 'resolved' else 1


if __name__ == '__main__':
    raise SystemExit(main())
