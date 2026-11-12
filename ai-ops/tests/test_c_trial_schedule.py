"""예약 기동·증거 실패/만료·마감·시계 변경의 비실행 회귀 시험."""

from datetime import timedelta
from copy import deepcopy
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ai_ops.c_trial_schedule import RefreshingProbe, schedule_trial, validate_operator_socket, verify_guard_sources, prepare_operator_kubeconfig
import test_c_trial_execution as trial_fixtures


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        fixture = trial_fixtures.TrialExecutionTest()
        fixture.setUp()
        self.fx, self.executor, self.auth = fixture.fixture, fixture.executor, fixture.authorization
        self.fx.at = self.executor.plan.approval_cutoff-timedelta(seconds=10)
        self.executor.prepared_at = None
        self.auth = self.auth.model_copy(update={'approved_at':self.fx.at-timedelta(seconds=1)})
        self.tick = 0

    def sleep(self, seconds):
        self.fx.at += timedelta(seconds=seconds)
        self.tick += seconds

    def test_automatically_prepares_and_returns_at_launch_without_write(self):
        prepared = []
        schedule_trial(self.executor, self.auth, sleep=self.sleep, monotonic=lambda: self.tick,
                       on_prepared=prepared.append)
        self.assertLess(prepared[0], self.executor.plan.approval_cutoff)
        self.assertEqual(self.fx.at, self.executor.plan.planned_at-timedelta(seconds=30))
        self.assertEqual(self.executor.transport.calls, [])
        self.assertFalse(self.fx.server.value['spec'].get('holderIdentity'))

    def test_missing_guard_until_cutoff_cannot_be_rescued_after_cutoff(self):
        self.executor.transport.guards = False
        with self.assertRaises(ValueError):
            schedule_trial(self.executor, self.auth, sleep=self.sleep, monotonic=lambda: self.tick)
        self.executor.transport.guards = True
        with self.assertRaises(ValueError):
            schedule_trial(self.executor, self.auth, sleep=self.sleep, monotonic=lambda: self.tick)
        self.assertIsNone(self.executor.prepared_at)
        self.assertEqual(self.executor.transport.calls, [])

    def test_lost_image_evidence_after_preparation_stops_before_lock(self):
        def lose(seconds):
            self.sleep(seconds)
            self.executor.transport.images = False
        with self.assertRaises(ValueError):
            schedule_trial(self.executor, self.auth, sleep=lose, monotonic=lambda: self.tick)
        self.assertEqual(self.executor.transport.calls, [])
        self.assertIsNone(self.executor.lease.uid)

    def test_clock_jump_and_suspension_are_not_rescheduled(self):
        def jump(seconds):
            self.sleep(seconds)
            self.fx.at += timedelta(seconds=120)
        with self.assertRaises(ValueError):
            schedule_trial(self.executor, self.auth, sleep=jump, monotonic=lambda: self.tick)
        self.assertIsNone(self.executor.lease.uid)

    def test_wrong_authorization_stops_without_waiting(self):
        sleep = Mock()
        with self.assertRaises(ValueError):
            schedule_trial(self.executor, self.auth.model_copy(update={'plan_hash':'f'*64}), sleep=sleep)
        sleep.assert_not_called()

    def test_automatic_entry_and_real_executor_allow_bounded_final_read(self):
        schedule_trial(self.executor,self.auth,sleep=self.sleep,monotonic=lambda:self.tick)
        transport=self.executor.transport
        original=transport.snapshot
        def delayed_snapshot():
            value=original()
            self.sleep(1.6)
            return value
        transport.snapshot=delayed_snapshot
        transport.wait_until.side_effect=lambda at:self.sleep(max(0,(at-self.fx.at).total_seconds()))
        original_argo=transport.argo
        def argo(role,command):
            original_argo(role,command)
            if command[1]=='sync':
                self.fx.snapshot['pods'][0]['metadata']['creationTimestamp']=self.fx.at.isoformat()
                self.fx.snapshot['application']['status']['operationState']['startedAt']=self.fx.at.isoformat()
        transport.argo=argo
        handoff=self.executor.inject(self.auth)
        self.assertGreaterEqual(handoff.injected_at,self.executor.plan.planned_at)
        self.assertLess((handoff.injected_at-self.executor.plan.planned_at).total_seconds(),5)

    def test_slow_final_read_still_prevents_first_argo_write(self):
        schedule_trial(self.executor,self.auth,sleep=self.sleep,monotonic=lambda:self.tick)
        transport=self.executor.transport
        original=transport.snapshot
        def slow_snapshot():
            value=original();self.sleep(6);return value
        transport.snapshot=slow_snapshot
        transport.wait_until.side_effect=lambda at:self.sleep(max(0,(at-self.fx.at).total_seconds()))
        with self.assertRaises(ValueError):self.executor.inject(self.auth)
        self.assertEqual(transport.calls,[])


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.now = __import__('datetime').datetime(2026,11,11,tzinfo=__import__('datetime').timezone.utc)
        self.tick = 0
        self.plan = SimpleNamespace(digest=lambda: 'a'*64)
        self.reader = Mock(return_value={'failure_absent':True})
        self.probe = RefreshingProbe(self.plan,self.reader,clock=lambda:self.now,monotonic=lambda:self.tick)

    def test_success_time_is_original_query_start_and_not_bumped_by_read(self):
        def delayed(plan):
            self.now += timedelta(seconds=4)
            self.tick += 4
            return {'failure_absent':True}
        start = self.now
        self.reader.side_effect = delayed
        self.probe.refresh()
        self.assertEqual(self.probe.value['checked_at'],start.isoformat())
        self.assertTrue(self.probe.require(self.plan))
        self.tick += 7
        self.now += timedelta(seconds=7)
        with self.assertRaises(ValueError):self.probe.require(self.plan)

    def test_failure_invalidates_previous_success_immediately(self):
        self.probe.refresh()
        self.reader.side_effect = PermissionError('synthetic')
        self.probe.refresh()
        self.assertEqual(self.probe.error_type,'PermissionError')
        with self.assertRaises(ValueError):self.probe.require(self.plan)

    def test_slow_or_future_query_cannot_publish_fresh_success(self):
        def slow(plan):
            self.now += timedelta(seconds=11)
            self.tick += 11
            return {}
        self.reader.side_effect=slow
        self.probe.refresh()
        self.assertIsNone(self.probe.value)
        self.reader.side_effect=None
        self.probe.refresh()
        self.now -= timedelta(seconds=1)
        with self.assertRaises(ValueError):self.probe.require(self.plan)

    def test_real_thread_refreshes_then_stops_without_external_marker(self):
        self.probe.interval=.01
        self.probe.start()
        try:
            limit=time.monotonic()+1
            while self.reader.call_count<2 and time.monotonic()<limit:time.sleep(.01)
            self.assertGreaterEqual(self.reader.call_count,2)
            self.assertTrue(self.probe.require(self.plan))
        finally:self.probe.close()
        self.assertFalse(self.probe.thread.is_alive())

    def test_wrong_plan_and_clock_shift_fail_closed(self):
        self.probe.refresh()
        with self.assertRaises(ValueError):self.probe.require(SimpleNamespace(digest=lambda:'b'*64))
        self.now += timedelta(seconds=2)
        with self.assertRaises(ValueError):self.probe.require(self.plan)


class PreparationPathsTest(unittest.TestCase):
    def test_socket_length_and_permissions_are_checked_before_bind(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            root.chmod(0o700)
            self.assertEqual(validate_operator_socket(root),root/'operator.sock')
            root.chmod(0o755)
            with self.assertRaises(ValueError):validate_operator_socket(root)
            root.chmod(0o700)
            long=root/('x'*100);long.mkdir(mode=0o700)
            with self.assertRaises(ValueError):validate_operator_socket(long)

    def test_old_guard_source_cannot_pass_schema2_preparation(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):verify_guard_sources(folder,{})


class OperatorKubeconfigTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.aws=Path(self.folder.name)/'aws';self.aws.write_text('#!/bin/sh\nexit 0\n');self.aws.chmod(0o700)
        self.config={'current-context':'fixed-eks','users':[{'name':'operator','user':{'exec':{
            'command':str(self.aws),'args':['eks','get-token','--profile','urlshortener-operator']}}}],
            'contexts':[{'name':'fixed-eks','context':{'namespace':'default'}}],
            'clusters':[{'name':'eks','cluster':{'server':'https://example.invalid'}}]}

    def prepare(self,config=None):
        return prepare_operator_kubeconfig(config or self.config,context='fixed-eks',aws_path=self.aws)

    def test_absolute_path_to_the_same_aws_binary_is_normalized_without_changing_original(self):
        original=deepcopy(self.config);prepared=self.prepare()
        self.assertEqual(self.config,original)
        self.assertEqual(prepared['users'][0]['user']['exec']['command'],'aws')
        self.assertEqual(prepared['users'][0]['user']['exec']['args'],original['users'][0]['user']['exec']['args'])
        self.assertEqual(prepared['contexts'][0]['context']['namespace'],'argocd')
        self.config['users'][0]['user']['exec']['command']='aws';self.prepare()

    def test_another_binary_or_relative_command_cannot_replace_aws(self):
        other=Path(self.folder.name)/'other-aws';other.write_text('');other.chmod(0o700)
        for command in (str(other),'./aws','scripts/aws','sh'):
            config=deepcopy(self.config);config['users'][0]['user']['exec']['command']=command
            with self.subTest(command=command),self.assertRaises(ValueError):self.prepare(config)

    def test_static_or_combined_credentials_are_rejected(self):
        for identity in ({'token':'synthetic-test-only'},
                         {**self.config['users'][0]['user'],'token':'synthetic-test-only'}):
            config=deepcopy(self.config);config['users'][0]['user']=identity
            with self.subTest(identity=identity),self.assertRaises(ValueError):self.prepare(config)

    def test_changed_context_or_ambiguous_config_is_rejected(self):
        config=deepcopy(self.config);config['current-context']='another-eks'
        with self.assertRaises(ValueError):self.prepare(config)
        config=deepcopy(self.config);config['contexts'].append(deepcopy(config['contexts'][0]))
        with self.assertRaises(ValueError):self.prepare(config)

    def test_nonexecutable_binary_is_rejected(self):
        self.aws.chmod(0o600)
        with self.assertRaises(ValueError):self.prepare()


if __name__=='__main__':unittest.main()
