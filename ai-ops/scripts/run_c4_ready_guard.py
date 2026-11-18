"""로컬/원격 감시를 먼저 준비하고 실제 승인 바인딩을 기다린다."""

import argparse
import json
import os
from pathlib import Path
import uuid

from ai_ops.c_trial_ready import ReadyTrialPlan
from ai_ops.c_trial_ready_guard import standby_guard
from ai_ops.c_trial_guards import private_json
from ai_ops.c_trial_lease_client import (TrialLeaseClient, LeaseConflict, LeaseRequestError,
                                       lease_failure_summary)
from ai_ops.path_policy import cli_paths, write_private_json
from run_c2_rollback_guard import (SERVER_ARGO, get_application, verify_normal,
    verify_local_kubeconfig, verify_isolated_server_permissions, rollback_at_deadline, call)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='C-04 승인 대기 독립 원복 감시')
    parser.add_argument('--intent', type=Path, required=True)
    parser.add_argument('--marker', type=Path, required=True)
    parser.add_argument('--kubeconfig', required=True)
    parser.add_argument('--role', choices=['local', 'remote'], required=True)
    parser.add_argument('--core', action='store_true')
    # Executables use TrialLeaseClient's owner/mode/absolute-path/hash validator,
    # not the JSON data path policy used for intent and marker files.
    parser.add_argument('--lease-trace-binary')
    parser.add_argument('--lease-trace-sha256')
    args = cli_paths(parser, parser.parse_args(argv), {'intent': 'input', 'marker': 'output'})
    if (args.lease_trace_binary is None) != (args.lease_trace_sha256 is None):
        parser.error('lease trace requires both binary and sha256')
    if args.core != (args.role == 'local'):
        raise ValueError('ready_guard_role_transport_rejected')
    intent = ReadyTrialPlan.model_validate(private_json(args.intent))
    if args.core:
        verify_local_kubeconfig(args.kubeconfig)
        os.environ['KUBECONFIG'] = args.kubeconfig
        os.environ.pop('ARGOCD_AUTH_TOKEN', None)
    else:
        verify_isolated_server_permissions()
    core_config = args.marker.parent/(args.marker.stem+'-argocd-config')
    if args.core and core_config.exists():
        raise ValueError('ready_guard_core_config_already_exists')
    argo = ('argocd', '--config', str(core_config), '--core', 'app') if args.core else SERVER_ARGO
    target = intent.targets
    normal_plan = dict(schema_version=2, source_repository=target['repository'],
        target='urlshortener/ai-ops-lab/lab-api/practice', application_uid=target['application_uid'],
        deployment_uid=target['deployment_uid'], baseline_revision=target['normal_revision'],
        failure_revision=target['failure_revision'], normal_image=target['normal_image'],
        failure_image=target['failure_image'])

    def get(ns, kind, name):
        return json.loads(call('kubectl', '--kubeconfig', args.kubeconfig,
                              '-n', ns, 'get', kind, name, '-o', 'json'))

    verify_normal(normal_plan, get_application(argo), get('ai-ops-lab', 'deployment', 'lab-api'))

    client = TrialLeaseClient(args.kubeconfig, target['lease_uid'], handle_sigterm=True,
        trace_binary=args.lease_trace_binary, trace_sha256=args.lease_trace_sha256)

    def replace(lease):
        try:
            client.replace(lease)
            return True
        except LeaseConflict:
            return False

    identity = str(os.getpid()) if args.role == 'local' else os.environ['GITHUB_RUN_ID']
    guard_id = args.role+':'+identity+':'+uuid.uuid4().hex

    def ready(marker):
        write_private_json(args.marker, marker)
        print(json.dumps({'status': 'c04_guard_standby_ready', **marker}), flush=True)

    try:
        client.start()
        result = standby_guard(intent, role=args.role, guard_id=guard_id,
            get_lease=client.get, replace_lease=replace,
            rollback=lambda plan, initial: rollback_at_deadline(plan, get=get, argo=argo,
                kubeconfig=args.kubeconfig, initial_lease=initial),
            publish_ready=ready)
    except LeaseRequestError as error:
        # A bound failure sidecar invalidates the old ready marker before exit.
        # Recording failure must not
        # replace the original exception, retry the request, or change rollback.
        try:
            write_private_json(args.marker.with_name(args.marker.name+'.failure.json'),
                dict(state='failed', role=args.role,
                guard_id=guard_id, intent_hash=intent.digest(),
                lease_failure=lease_failure_summary(error.diagnostic)))
        except (OSError, ValueError):
            pass
        raise
    finally:
        try:
            client.close()
        finally:
            if args.core: core_config.unlink(missing_ok=True)
    print(json.dumps(result), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
