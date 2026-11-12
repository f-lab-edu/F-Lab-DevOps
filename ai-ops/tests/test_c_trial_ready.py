"""실제 주입 adapter를 사용하되 시계·Kubernetes·GitHub는 메모리 대역이다."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json
import tempfile
import unittest

from ai_ops.c_trial_ready import (ReadyTrialPlan, ReadyApproval, ReadyActivation,
    ReadyReceipt, ReadyTrialController, TARGET_FIELDS, ACTIVATION_KEY, ACK_KEYS)
from ai_ops.c_trial_ready_guard import standby_guard
import test_c_trial_execution as fixtures
from test_c2_active_capture import NOW


class ReadyStartTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TrialExecutionTest(); self.fixture.setUp()
        self.fx, self.port = self.fixture.fixture, self.fixture.transport
        self.root = tempfile.TemporaryDirectory(); self.addCleanup(self.root.cleanup)
        self.intent = ReadyTrialPlan(targets={k: getattr(self.fixture.plan, k) for k in TARGET_FIELDS},
            runtime_manifest_sha256='a'*64, preflight_sha256='b'*64)
        self.fx.at = NOW-timedelta(seconds=10); self.tick = 0
        self.guards = {'local': 'local:123:nonce', 'remote': 'remote:456:nonce'}
        self.ack = True; self.arm_delay = 0
        self.port.require_standby_guards = lambda p: deepcopy(self.guards)
        self.port.require_ready_inputs = lambda p: None
        self.port.arm_ready_guards = self.arm
        self.port.require_ready_guard_acks = self.require_ack
        original_argo = self.port.argo
        def argo(role, command):
            original_argo(role, command)
            if command[1] == 'sync':
                self.fx.snapshot['pods'][0]['metadata']['creationTimestamp'] = self.fx.at.isoformat()
                self.fx.snapshot['application']['status']['operationState']['startedAt'] = self.fx.at.isoformat()
        self.port.argo = argo
        self.controller = ReadyTrialController(self.intent, self.port, Path(self.root.name)/'consumed.json',
            clock=lambda: self.fx.at, monotonic=lambda: self.tick)
        self.receipt = self.controller.prepare()

    def advance(self, seconds):
        self.fx.at += timedelta(seconds=seconds); self.tick += seconds

    def approval(self):
        return ReadyApproval(trial_id=self.intent.trial_id, intent_hash=self.intent.digest(),
            readiness_hash=self.receipt.digest(), actor_github_id=149856960,
            approved_at=self.fx.at, fault_injection_approved=True)

    def arm(self, activation, lease):
        self.activation = activation
        self.advance(self.arm_delay)
        a = self.fx.server.value['metadata'].setdefault('annotations', {})
        a[ACTIVATION_KEY] = activation.model_dump_json()
        for role, key in ACK_KEYS.items():
            a[key] = json.dumps({'activation_hash': activation.digest(), 'guard_id': self.guards[role]})

    def require_ack(self, activation):
        if not self.ack:
            raise ValueError('synthetic_missing_ack')

    def start(self):
        self.advance(9); approval = self.approval(); self.advance(1)
        return self.controller.start(approval)

    def test_ready_then_approval_starts_immediately_without_calendar_wait(self):
        self.assertEqual(self.port.calls, [])
        self.assertFalse(self.fx.server.value['spec'].get('holderIdentity'))
        executor, handoff = self.start()
        self.port.wait_until.assert_not_called()
        self.assertEqual(handoff.injected_at, NOW)
        self.assertEqual(executor.plan.review_cutoff(), NOW+timedelta(seconds=210))
        self.assertEqual(executor.plan.manual_recovery_at, NOW+timedelta(seconds=240))
        self.assertEqual(handoff.rollback_deadline, NOW+timedelta(seconds=300))
        self.assertEqual([c[1][1] for c in self.port.calls], ['set', 'sync'])

    def test_ack_processing_never_extends_the_five_minute_deadline(self):
        self.arm_delay = 2
        executor, handoff = self.start()
        self.assertEqual(handoff.rollback_deadline, NOW+timedelta(seconds=300))
        self.assertEqual((handoff.rollback_deadline-handoff.injected_at).total_seconds(), 298)
        self.assertEqual(executor.plan.minimum_review_seconds, 120)

    def test_stale_approval_or_other_ready_receipt_cannot_start(self):
        self.advance(1); a = self.approval(); self.advance(1)
        for key, value in [('intent_hash', 'c'*64), ('readiness_hash', 'd'*64),
                           ('trial_id', 111), ('approved_at', self.receipt.prepared_at-timedelta(seconds=1)),
                           ('approved_at', self.fx.at+timedelta(seconds=1))]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.controller.start(a.model_copy(update={key: value}))
        self.assertEqual(self.port.calls, [])

    def test_false_or_numeric_approval_is_rejected_even_after_model_copy(self):
        a = self.approval(); self.advance(1)
        for value in (False, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.controller.start(a.model_copy(update={'fault_injection_approved': value}))
        self.assertFalse(self.controller.journal.exists())

    def test_new_capture_and_owned_restore_share_the_same_executor(self):
        executor, handoff = self.start()
        job = executor.investigate()
        self.assertEqual(job['uid'], 'job-uid')
        executor.restore()
        self.assertTrue(executor.finished)
        self.assertFalse(self.fx.server.value['spec'].get('holderIdentity'))

    def test_slow_recheck_does_not_turn_expired_readiness_into_an_execution(self):
        self.advance(1); a = self.approval(); self.advance(1)
        self.port.require_ready_inputs = lambda p: self.advance(300)
        with self.assertRaises(ValueError): self.controller.start(a)
        self.assertFalse(self.controller.journal.exists()); self.assertEqual(self.port.calls, [])

    def test_expired_ready_receipt_and_clock_jump_do_not_reschedule(self):
        a = self.approval(); self.advance(301)
        with self.assertRaises(ValueError): self.controller.start(a)
        self.assertFalse(self.controller.journal.exists())
        self.fx.at -= timedelta(seconds=300)
        with self.assertRaises(ValueError): self.controller.start(a)
        self.assertEqual(self.port.calls, [])

    def test_changed_target_or_guard_stops_before_consumption(self):
        self.advance(1); a = self.approval(); self.advance(1)
        self.guards['remote'] = 'remote:789:new'
        with self.assertRaises(ValueError): self.controller.start(a)
        self.guards['remote'] = self.receipt.guards['remote']
        self.fx.snapshot['deployment']['metadata']['uid'] = 'replacement'
        with self.assertRaises(ValueError): self.controller.start(a)
        self.assertFalse(self.controller.journal.exists()); self.assertEqual(self.port.calls, [])

    def test_missing_ack_or_slow_activation_does_not_inject_and_releases_own_lock(self):
        self.ack = False
        with self.assertRaises(ValueError): self.start()
        self.assertTrue(self.controller.journal.exists())
        self.assertFalse(self.fx.server.value['spec'].get('holderIdentity'))
        self.assertEqual(self.port.calls, [])

    def test_late_activation_is_not_retried_or_shifted(self):
        self.arm_delay = 16
        with self.assertRaises(ValueError): self.start()
        self.assertTrue(self.controller.journal.exists()); self.assertEqual(self.port.calls, [])

    def test_duplicate_and_restarted_process_cannot_reuse_consumed_approval(self):
        self.start()
        before = deepcopy(self.port.calls)
        with self.assertRaises(ValueError): self.controller.start(self.approval())
        other = ReadyTrialController(self.intent, self.port, self.controller.journal)
        with self.assertRaises(ValueError): other.prepare()
        self.assertEqual(self.port.calls, before)

    def test_handshake_chain_cannot_change_targets_or_relative_deadlines(self):
        self.start(); record = self.activation.model_dump(mode='json')
        record['execution']['rollback_deadline'] = (NOW+timedelta(seconds=301)).isoformat()
        with self.assertRaises(ValueError): ReadyActivation.model_validate(record)
        for key in ['minimum_review_seconds', 'rollback_after_start_seconds']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                ReadyTrialPlan.model_validate({**self.intent.model_dump(), key: 1})


class StandbyGuardTest(unittest.TestCase):
    def setUp(self):
        f = ReadyStartTest(); f.setUp(); self.addCleanup(f.doCleanups)
        self.fx = f; f.start(); self.activation = f.activation
        self.now = NOW-timedelta(seconds=5); self.tick = 0
        self.lease = deepcopy(f.fx.server.value)
        self.lease['spec'] = {}; self.lease['metadata']['annotations'] = {}
        self.calls = []; self.ready = []; self.conflict = False; self.armed = False

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds); self.tick += seconds
        if not self.armed and self.now >= NOW:
            self.armed = True
            self.lease['spec']['holderIdentity'] = self.activation.owner_holder
            self.lease['metadata']['annotations'][ACTIVATION_KEY] = self.activation.model_dump_json()

    def replace(self, value):
        if self.conflict:
            self.conflict = False
            self.lease['metadata']['annotations'][ACK_KEYS['remote']] = 'other-ack'
            return False
        self.lease = deepcopy(value); return True

    def run_guard(self, get_lease=None):
        return standby_guard(self.activation.intent, role='local',
            guard_id=self.activation.readiness.guards['local'],
            get_lease=get_lease or (lambda: deepcopy(self.lease)), replace_lease=self.replace,
            rollback=lambda p, l: self.calls.append((p, self.now)), publish_ready=self.ready.append,
            clock=lambda: self.now, monotonic=lambda: self.tick, sleep=self.sleep)

    def test_no_rollback_before_approval_then_one_immutable_deadline(self):
        result = self.run_guard()
        self.assertEqual(self.ready[0]['state'], 'standby')
        self.assertEqual(len(self.calls), 1)
        self.assertGreaterEqual(self.calls[0][1], NOW+timedelta(seconds=300))
        self.assertLess(self.calls[0][1], NOW+timedelta(seconds=301))
        self.assertEqual(result['activation_hash'], self.activation.digest())

    def test_activation_published_during_lease_read_is_not_mistaken_for_future_data(self):
        calls = 0
        def read():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.sleep(5.01)
            return deepcopy(self.lease)
        result = self.run_guard(get_lease=read)
        self.assertTrue(result['rollback_started'])
        self.assertEqual(len(self.calls), 1)

    def test_clock_jump_during_lease_read_cannot_arm(self):
        calls = 0
        def read():
            nonlocal calls
            calls += 1
            if calls == 2: self.now += timedelta(seconds=20)
            return deepcopy(self.lease)
        with self.assertRaises(ValueError): self.run_guard(get_lease=read)
        self.assertEqual(self.calls, [])

    def test_cas_retry_preserves_the_other_guard_ack(self):
        self.conflict = True; self.run_guard()
        self.assertEqual(self.lease['metadata']['annotations'][ACK_KEYS['remote']], 'other-ack')

    def test_no_activation_expires_without_rollback(self):
        self.armed = True
        result = self.run_guard()
        self.assertFalse(result['rollback_started']); self.assertEqual(self.calls, [])

    def test_other_intent_and_wrong_guard_identity_cannot_arm(self):
        self.activation.readiness.guards['local'] = 'changed'
        with self.assertRaises(ValueError): self.run_guard()
        self.assertEqual(self.calls, [])

    def test_activation_changes_after_ack_cannot_postpone_the_frozen_deadline(self):
        original_sleep = self.sleep
        def change(seconds):
            original_sleep(seconds)
            if self.now > NOW+timedelta(seconds=1):
                self.lease['metadata']['annotations'][ACTIVATION_KEY] = '{}'
        self.sleep = change
        result = self.run_guard()
        self.assertEqual(result['deadline_utc'], (NOW+timedelta(seconds=300)).isoformat())
        self.assertLess(self.calls[0][1], NOW+timedelta(seconds=301))

    def test_standby_uid_replacement_stops_without_ack_or_rollback(self):
        original_sleep = self.sleep
        def replace(seconds):
            original_sleep(seconds); self.lease['metadata']['uid'] = 'replacement'
        self.sleep = replace
        with self.assertRaises(ValueError): self.run_guard()
        self.assertEqual(self.calls, [])


if __name__ == '__main__': unittest.main()
