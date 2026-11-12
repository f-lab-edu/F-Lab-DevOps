"""감시 ACK와 실제 준비 파일/프로세스/GitHub 원본의 대조. 외부 호출은 대역이다."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import hashlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch, Mock

from ai_ops.c_trial_ready_transport import ReadyTrialTransport, StandbyGuardReader, GUARD_SOURCES, READY_WORKFLOW, READY_STEP, MissingGuardAck
from ai_ops.c_trial_ready import ACTIVATION_KEY, ACK_KEYS
import test_c_trial_ready as fixtures
from test_c2_active_capture import NOW


class ReadyTransportTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ReadyStartTest(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.f.start(); self.activation = self.f.activation
        self.lease = deepcopy(self.f.fx.server.value)
        self.lease['metadata']['annotations'] = {}
        self.port = object.__new__(ReadyTrialTransport)
        self.port.intent = self.activation.intent
        self.port.activation = None
        self.port.clock = lambda: NOW
        self.port.get = lambda key: deepcopy(self.lease)
        self.port.standby_reader = lambda p: deepcopy(self.activation.readiness.guards)
        self.port.image_check = Mock(return_value=True)
        self.port.lease_command = self.replace

    def replace(self, *args, payload):
        self.lease = json.loads(payload)
        a = self.lease['metadata']['annotations']
        if ACTIVATION_KEY in a:
            for role, key in ACK_KEYS.items():
                a[key] = json.dumps({'activation_hash': self.activation.digest(),
                                     'guard_id': self.activation.readiness.guards[role]})

    def test_real_adapter_arms_one_plan_and_verifies_both_exact_acks(self):
        self.port.arm_ready_guards(self.activation, self.f.controller.executor.lease)
        self.assertTrue(self.port.require_guards(self.activation.execution))
        self.assertEqual(json.loads(self.lease['metadata']['annotations'][ACTIVATION_KEY]),
                         self.activation.model_dump(mode='json'))

    def test_wrong_or_missing_ack_and_foreign_holder_are_not_ready(self):
        self.port.arm_ready_guards(self.activation, self.f.controller.executor.lease)
        a = self.lease['metadata']['annotations']; old = a.pop(ACK_KEYS['remote'])
        with self.assertRaises(MissingGuardAck): self.port.require_ready_guard_acks(self.activation)
        a[ACK_KEYS['remote']] = json.dumps({'activation_hash': 'f'*64, 'guard_id': 'other'})
        with self.assertRaises(ValueError): self.port.require_ready_guard_acks(self.activation)
        a[ACK_KEYS['remote']] = old; self.lease['spec']['holderIdentity'] = 'foreign'
        with self.assertRaises(ValueError): self.port.require_ready_guard_acks(self.activation)

    def test_existing_activation_is_not_overwritten_and_cleanup_requires_empty_lease(self):
        self.port.arm_ready_guards(self.activation, self.f.controller.executor.lease)
        with self.assertRaises(ValueError): self.port.arm_ready_guards(self.activation, self.f.controller.executor.lease)
        with self.assertRaises(ValueError): self.port.clear_ready_control()
        self.lease['spec']['holderIdentity'] = ''
        self.port.clear_ready_control()
        self.assertFalse(set(self.lease['metadata']['annotations']) & {ACTIVATION_KEY, *ACK_KEYS.values()})

    def test_image_cache_remains_bound_to_same_targets_after_time_binding(self):
        self.port.require_images(self.activation.execution)
        self.assertEqual(self.port.image_check.call_args[0][0].digest(), self.activation.intent.digest())
        with self.assertRaises(ValueError):
            self.port.require_images(self.activation.execution.model_copy(update={'worker_uid': 'replacement'}))


class StandbyEvidenceTest(unittest.TestCase):
    def setUp(self):
        f = fixtures.ReadyStartTest(); f.setUp(); self.addCleanup(f.doCleanups); self.intent = f.intent
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name); self.root.chmod(0o700)
        self.addCleanup(patch.stopall); patch.dict(os.environ, {'AI_OPS_FILES_ROOT': str(self.root)}).start()
        hashes = {}
        for name in GUARD_SOURCES:
            p = self.root/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('local fixture')
            hashes[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.anchor = {'intent_hash': self.intent.digest(), 'actual_ready_markers_verified': True,
            'observed_at': (NOW-timedelta(seconds=1)).isoformat(), 'standby_expires_at': (NOW+timedelta(minutes=10)).isoformat(),
            'guard_source_root': str(self.root), 'source_sha256': hashes,
            'local': {'pid': 123, 'intent_path': str(self.root/'intent.json'), 'marker': str(self.root/'marker.json'), 'guard_id': 'local:123:n'},
            'remote': {'run_id': 456, 'guard_id': 'remote:456:n'}}
        self.write('marker.json', {'state': 'standby', 'role': 'local', 'intent_hash': self.intent.digest(), 'guard_id': 'local:123:n'})
        self.write('anchor.json', self.anchor)
        self.remote = dict(id=456, status='in_progress', event='workflow_dispatch', head_branch='main',
                           head_sha=self.intent.targets['workflow_main'], path=READY_WORKFLOW)
        self.jobs = {'total_count': 1, 'jobs': [{'status': 'in_progress', 'steps': [{'name': READY_STEP, 'status': 'in_progress'}]}]}
        patch('ai_ops.c_trial_ready_transport.os.kill').start()
        patch('ai_ops.c_trial_ready_transport.subprocess.run', return_value=Mock(stdout=(str(self.root/'ai-ops/scripts/run_c4_ready_guard.py')+' '+str(self.root/'intent.json')).encode())).start()
        self.reader = StandbyGuardReader(self.root/'anchor.json', hashes, clock=lambda: NOW,
            github=lambda p: deepcopy(self.jobs if '/jobs?' in p else self.remote))

    def write(self, name, value):
        p = self.root/name; p.write_text(json.dumps(value)); p.chmod(0o600)

    def test_current_process_sources_job_and_real_marker_are_required(self):
        self.assertEqual(self.reader(self.intent), {'local': 'local:123:n', 'remote': 'remote:456:n'})

    def test_changed_source_and_cancelled_or_wrong_workflow_are_rejected(self):
        for key, value in [('status', 'completed'), ('head_sha', 'f'*40), ('path', 'old.yml')]:
            old = self.remote[key]; self.remote[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.reader(self.intent)
            self.remote[key] = old
        (self.root/'ai-ops/scripts/run_c4_ready_guard.py').write_text('changed')
        with self.assertRaises(ValueError): self.reader(self.intent)

    def test_future_evidence_and_truncated_jobs_cannot_be_ready(self):
        self.anchor['observed_at'] = (NOW+timedelta(seconds=1)).isoformat(); self.write('anchor.json', self.anchor)
        with self.assertRaises(ValueError): self.reader(self.intent)
        self.anchor['observed_at'] = NOW.isoformat(); self.write('anchor.json', self.anchor)
        self.jobs['total_count'] = 2
        with self.assertRaises(ValueError): self.reader(self.intent)

    def test_changing_both_anchor_hash_and_file_cannot_replace_approved_guard_code(self):
        name = 'ai-ops/scripts/run_c4_ready_guard.py'
        (self.root/name).write_text('replacement')
        self.anchor['source_sha256'][name] = hashlib.sha256(b'replacement').hexdigest()
        self.write('anchor.json', self.anchor)
        with self.assertRaises(ValueError): self.reader(self.intent)


if __name__ == '__main__': unittest.main()
