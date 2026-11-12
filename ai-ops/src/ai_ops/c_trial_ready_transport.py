"""준비 완료 실행기의 실제 Kubernetes 제어·양쪽 감시 연결."""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from ai_ops.c_trial_guards import private_json, _github
from ai_ops.c_trial_transport import C4TrialTransport
from ai_ops.c_trial_ready import ACTIVATION_KEY, ACK_KEYS
from ai_ops.contracts import _utc_datetime


READY_WORKFLOW = '.github/workflows/ai-ops-c4-ready-rollback.yml'
READY_STEP = '준비 대기와 승인 후 기한 원복'
GUARD_SOURCES = {READY_WORKFLOW, 'ai-ops/scripts/run_c4_ready_guard.py',
    'ai-ops/scripts/run_c2_rollback_guard.py', 'ai-ops/src/ai_ops/c_trial_ready.py',
    'ai-ops/src/ai_ops/c_trial_ready_guard.py', 'ai-ops/src/ai_ops/c_trial_execution.py',
    'ai-ops/src/ai_ops/c2_rollback_guard.py', 'ai-ops/src/ai_ops/b6_coordination.py'}


class StandbyGuardReader:
    """실제 최초 로그 관찰과 현재 프로세스/run/step/소스를 함께 확인한다."""

    def __init__(self, anchor, expected_hashes, *, github=_github, clock=None):
        self.anchor, self.github = Path(anchor), github
        self.expected_hashes = dict(expected_hashes)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def __call__(self, intent):
        a = private_json(self.anchor)
        now = self.clock()
        if (a['intent_hash'] != intent.digest() or a.get('actual_ready_markers_verified') is not True
                or not _utc_datetime(a['observed_at']) <= now < _utc_datetime(a['standby_expires_at'])):
            raise ValueError('actual_standby_guard_evidence_required')
        root = Path(a['guard_source_root']).resolve(strict=True)
        if set(a['source_sha256']) != GUARD_SOURCES or a['source_sha256'] != self.expected_hashes:
            raise ValueError('standby_guard_source_contract_rejected')
        for name, expected in a['source_sha256'].items():
            path = root/name
            if (path.is_symlink() or not path.resolve().is_relative_to(root)
                    or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
                raise ValueError('standby_guard_source_changed')
        local = a['local']
        pid = local['pid']
        if type(pid) is not int or pid <= 0:
            raise ValueError('standby_guard_pid_rejected')
        os.kill(pid, 0)
        command = subprocess.run(['ps', '-p', str(pid), '-o', 'command='],
                                 capture_output=True, check=True, timeout=2).stdout.decode()
        if (str(root/'ai-ops/scripts/run_c4_ready_guard.py') not in command
                or local['intent_path'] not in command):
            raise ValueError('standby_guard_process_changed')
        marker = private_json(Path(local['marker']))
        if (marker['intent_hash'] != intent.digest() or marker['guard_id'] != local['guard_id']
                or marker['role'] != 'local' or marker['state'] != 'standby'):
            raise ValueError('local_standby_marker_rejected')
        remote = a['remote']
        prefix = 'repos/f-lab-edu/F-Lab-DevOps/actions/runs/'+str(remote['run_id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            run, jobs = list(pool.map(self.github, (prefix, prefix+'/jobs?per_page=100')))
        if (run['id'] != remote['run_id'] or run['status'] != 'in_progress'
                or run['event'] != 'workflow_dispatch' or run['head_branch'] != 'main'
                or run['head_sha'] != intent.targets['workflow_main'] or run['path'] != READY_WORKFLOW
                or jobs['total_count'] != len(jobs['jobs'])
                or not any(j['status'] == 'in_progress' and any(s['name'] == READY_STEP
                    and s['status'] == 'in_progress' for s in j['steps']) for j in jobs['jobs'])):
            raise ValueError('remote_standby_guard_not_running')
        return {'local': local['guard_id'], 'remote': remote['guard_id']}


class ReadyTrialTransport(C4TrialTransport):
    def __init__(self, kubeconfig, *, intent, normal_binding, image_check, standby_reader, merge_session, clock=None):
        super().__init__(kubeconfig, image_check=image_check,
                         guard_check=lambda p: False, merge_session=merge_session, clock=clock)
        self.standby_reader = standby_reader
        self.intent, self.normal_binding = intent, normal_binding
        self.activation = None

    def require_images(self, plan):
        if any(getattr(plan, k) != v for k, v in self.intent.targets.items()):
            raise ValueError('ready_image_target_changed')
        return self.image_check(self.intent.source_plan())

    def require_ready_inputs(self, intent):
        if intent != self.intent:
            raise ValueError('ready_intent_changed')
        prefix = 'repos/joungGo/c03-execution-lab'
        protection = _github(prefix+'/branches/main/protection')
        if (_github(prefix+'/git/ref/heads/main')['object']['sha'] != intent.targets['seed_main']
                or protection['required_pull_request_reviews']['required_approving_review_count'] != 1
                or protection['required_pull_request_reviews']['dismiss_stale_reviews'] is not True
                or protection['enforce_admins']['enabled'] is not True
                or protection['required_status_checks']['strict'] is not True
                or 'C03 Main PR Gate' not in protection['required_status_checks']['contexts']):
            raise ValueError('ready_git_main_or_protection_changed')
        worker, pvc, relay = (self.get(key) for key in ('worker', 'pvc', 'relay'))
        if (worker['metadata']['uid'] != intent.targets['worker_uid']
                or worker['status'].get('readyReplicas') != 1
                or pvc['metadata']['uid'] != intent.targets['pvc_uid']
                or pvc['status'].get('phase') != 'Bound' or relay['status'].get('readyReplicas') != 1):
            raise ValueError('ready_investigation_target_changed')
        if getattr(self, 'prepared_worker_pod', None) is not None:
            pods = json.loads(self._kube('-n', 'ai-ci', 'get', 'pods', '-l', 'app=ci-worker', '-o', 'json'))
            if (pods.get('metadata', {}).get('continue') or len(pods['items']) != 1
                    or {k: pods['items'][0]['metadata'][k] for k in ('name', 'uid')} != self.prepared_worker_pod):
                raise ValueError('ready_projection_worker_replaced')
        binding = json.loads(self._kube('-n', 'ai-ci', 'get', 'configmap', 'ci-normal-binding', '-o', 'json'))
        from ai_ops.normal_ci_contract import NormalCIBinding
        if NormalCIBinding.model_validate_json(binding['data']['normal-ci-binding.json']) != self.normal_binding:
            raise ValueError('ready_normal_binding_changed')

    def require_standby_guards(self, intent):
        return self.standby_reader(intent)

    def arm_ready_guards(self, activation, lease):
        current = self.get('lease')
        if (current['metadata']['uid'] != activation.execution.lease_uid
                or current['spec'].get('holderIdentity') != activation.owner_holder
                or lease.holder != activation.owner_holder or lease.uid != current['metadata']['uid']):
            raise ValueError('ready_arm_owner_changed')
        annotations = current['metadata'].setdefault('annotations', {})
        if ACTIVATION_KEY in annotations or any(k in annotations for k in ACK_KEYS.values()):
            raise ValueError('old_ready_activation_present')
        annotations[ACTIVATION_KEY] = activation.model_dump_json()
        self.lease_command('replace', '-f', '-', '-o', 'json', payload=json.dumps(current))
        self.activation = activation
        end = time.monotonic()+activation.intent.start_write_budget_seconds
        while True:
            try:
                self.require_ready_guard_acks(activation)
                return
            except MissingGuardAck:
                if time.monotonic() >= end or self.clock() >= activation.execution.planned_at + timedelta(
                        seconds=activation.intent.start_write_budget_seconds):
                    raise ValueError('ready_guard_ack_timeout') from None
                time.sleep(.1)

    def require_ready_guard_acks(self, activation):
        guards = self.require_standby_guards(activation.intent)
        if guards != activation.readiness.guards:
            raise ValueError('ready_active_guard_identity_changed')
        # 원격 조회 도중 소유권이 바뀔 수 있어 Lease는 이후에 새로 읽는다.
        lease = self.get('lease')
        annotations = lease['metadata'].get('annotations', {})
        if (lease['metadata']['uid'] != activation.execution.lease_uid
                or lease['spec'].get('holderIdentity') != activation.owner_holder
                or json.loads(annotations.get(ACTIVATION_KEY, '{}')) != activation.model_dump(mode='json')):
            raise ValueError('ready_activation_or_owner_changed')
        for role, key in ACK_KEYS.items():
            if key not in annotations:
                raise MissingGuardAck()
            if json.loads(annotations[key]) != {'activation_hash': activation.digest(), 'guard_id': guards[role]}:
                raise ValueError('ready_guard_ack_mismatch')
        return True

    def require_guards(self, plan):
        if self.activation is None or self.activation.execution != plan:
            raise ValueError('ready_guards_not_armed')
        return self.require_ready_guard_acks(self.activation)

    def clear_ready_control(self):
        """자기 정상 원복/인계·잠금 해제 뒤에만 임시 제어 annotation을 제거한다."""
        if self.activation is None:
            return
        current = self.get('lease')
        annotations = current['metadata'].get('annotations', {})
        if (current['metadata']['uid'] != self.activation.execution.lease_uid
                or current['spec'].get('holderIdentity')
                or json.loads(annotations.get(ACTIVATION_KEY, '{}')) != self.activation.model_dump(mode='json')):
            raise ValueError('ready_control_cleanup_owner_changed')
        for key in (ACTIVATION_KEY, *ACK_KEYS.values()):
            annotations.pop(key, None)
        self.lease_command('replace', '-f', '-', '-o', 'json', payload=json.dumps(current))


class MissingGuardAck(ValueError):
    pass
