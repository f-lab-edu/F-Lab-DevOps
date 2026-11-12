"""관측보다 이른 요청을 막고 실제 시각·고정 마감 안에서만 기다린다."""
from datetime import datetime,timedelta,timezone
import unittest
from unittest.mock import Mock,patch
from pathlib import Path
import subprocess,tempfile,threading

from ai_ops.c_recovery import ImageFailureBinding,create_request
from ai_ops.c_trial_request_time import wait_for_request_projection


def projection(at):
    from test_c_recovery import projection as base_projection
    value=base_projection()
    ids=['ev-deployment','ev-event','ev-pod']
    failure=ImageFailureBinding(pod_uid_hash='1'*64,deployment_uid_hash='2'*64,
        image_hash='3'*64,observed_at=at,evidence_ids=ids)
    return value.model_copy(update={'image_failure':failure,'cited_evidence_ids':ids})


class RequestTimeTest(unittest.TestCase):
    def test_factory_refuses_future_observation_without_backdating_request(self):
        now=datetime(2026,11,12,13,47,33,376914,tzinfo=timezone.utc)
        value=projection(now+timedelta(seconds=2,microseconds=9996))
        with self.assertRaisesRegex(ValueError,'incident_observation_in_future'):
            create_request(value,149856960,now=now)
        self.assertEqual(create_request(value,149856960,now=value.image_failure.observed_at).requested_at,
                         value.image_failure.observed_at)

    def test_authenticated_operator_does_not_persist_future_request(self):
        from ai_ops.c_operator import OperatorServer,_client
        value=projection(datetime.now(timezone.utc)+timedelta(seconds=60))
        runner=lambda *a,**k:subprocess.CompletedProcess([],0,'149856960\n','')
        with tempfile.TemporaryDirectory() as folder, \
             patch('ai_ops.c_operator.read_incident_projection',return_value=value):
            root=Path(folder)
            with OperatorServer(root/'state',root/'incident',149856960,github_runner=runner) as server:
                thread=threading.Thread(target=server.handle_request);thread.start()
                try:reply=_client(root/'state/operator.sock','request',value.incident_id,value.run_id)
                finally:thread.join(timeout=5)
                self.assertNotEqual(reply['status'],'ok')
                with server.drafts._connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM requests').fetchone()[0],0)


class ObservationWaitTest(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,12,13,47,33,376914,tzinfo=timezone.utc)
        self.tick=0.;self.sleeps=[]
        self.value=projection(self.now+timedelta(seconds=2,microseconds=9996))
        self.fetch=Mock(return_value=self.value)
        self.deadline=self.now+timedelta(seconds=90)

    def sleep(self,seconds):
        self.sleeps.append(seconds);self.now+=timedelta(seconds=seconds);self.tick+=seconds

    def wait(self,**kwargs):
        return wait_for_request_projection(self.fetch,deadline=self.deadline,
            clock=lambda:self.now,monotonic=lambda:self.tick,sleep=kwargs.get('sleep',self.sleep))

    def test_real_wait_then_unchanged_projection_allows_current_time_request(self):
        value=self.wait();request=create_request(value,149856960,now=self.now)
        self.assertGreaterEqual(request.requested_at,value.image_failure.observed_at)
        self.assertAlmostEqual(sum(self.sleeps),2.009996,places=6)
        self.assertEqual(self.fetch.call_count,2)
        self.assertEqual(self.deadline,self.now+timedelta(seconds=87,microseconds=990004))

    def test_past_or_equal_observation_never_sleeps(self):
        for delta in (-1,0):
            self.fetch.reset_mock();self.fetch.return_value=projection(self.now+timedelta(seconds=delta))
            self.wait();self.assertEqual(self.fetch.call_count,1);self.assertEqual(self.sleeps,[])

    def test_large_future_or_insufficient_notice_window_stops_without_waiting(self):
        self.fetch.return_value=projection(self.now+timedelta(seconds=6))
        with self.assertRaisesRegex(ValueError,'incident_observation_outside_wait_window'):self.wait()
        self.assertEqual(self.sleeps,[])
        self.fetch.return_value=self.value;self.deadline=self.now+timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError,'incident_observation_outside_wait_window'):self.wait()
        self.assertEqual(self.sleeps,[])

    def test_changed_projection_or_failed_refetch_never_returns_old_evidence(self):
        self.fetch.side_effect=[self.value,self.value.model_copy(update={'report_hash':'f'*64})]
        with self.assertRaisesRegex(ValueError,'incident_projection_changed_while_waiting'):self.wait()
        self.setUp();self.fetch.side_effect=[self.value,OSError('synthetic read failure')]
        with self.assertRaises(OSError):self.wait()
        self.setUp();calls=0
        def mutable_refetch():
            nonlocal calls
            calls+=1
            if calls==2:self.value.report_hash='f'*64
            return self.value
        self.fetch.side_effect=mutable_refetch
        with self.assertRaisesRegex(ValueError,'incident_projection_changed_while_waiting'):self.wait()

    def test_clock_jump_or_stalled_wall_clock_cannot_bypass_bounded_wait(self):
        def jump(seconds):self.sleep(seconds);self.now+=timedelta(seconds=2)
        with self.assertRaisesRegex(ValueError,'incident_request_clock_changed'):self.wait(sleep=jump)
        self.setUp()
        def stalled(seconds):self.tick+=seconds
        with self.assertRaisesRegex(ValueError,'incident_request_clock_changed'):self.wait(sleep=stalled)

    def test_initial_fetch_or_refetch_cannot_cross_existing_deadline(self):
        self.deadline=self.now+timedelta(seconds=3)
        def read():self.sleep(4);return self.value
        self.fetch.side_effect=read
        with self.assertRaisesRegex(ValueError,'incident_request_time_budget_exhausted'):self.wait()
        self.setUp();count=0;self.deadline=self.now+timedelta(seconds=3)
        def late_refetch():
            nonlocal count
            count+=1
            if count==2:self.sleep(2)
            return self.value
        self.fetch.side_effect=late_refetch
        with self.assertRaisesRegex(ValueError,'incident_request_time_budget_exhausted'):self.wait()
        self.setUp()
        def slow_fetch():self.sleep(6);return self.value
        self.fetch.side_effect=slow_fetch
        with self.assertRaisesRegex(ValueError,'incident_request_time_budget_exhausted'):self.wait()

    def test_missing_failure_projection_is_not_request_readiness(self):
        self.fetch.return_value=self.value.model_copy(update={'image_failure':None})
        with self.assertRaisesRegex(ValueError,'incident_failure_projection_required'):self.wait()

    def test_actual_operator_boundary_waits_then_persists_exactly_one_request(self):
        from ai_ops.c_operator import OperatorServer,_client
        runner=lambda *a,**k:subprocess.CompletedProcess([],0,'149856960\n','')
        class LocalClock:
            @staticmethod
            def now(_zone):return self.now
        actions=[]
        with tempfile.TemporaryDirectory() as folder, \
             patch('ai_ops.c_operator.read_incident_projection',return_value=self.value), \
             patch('ai_ops.c_recovery.datetime',LocalClock):
            root=Path(folder)
            with OperatorServer(root/'state',root/'incident',149856960,github_runner=runner) as server:
                def exchange(action):
                    actions.append(action)
                    thread=threading.Thread(target=server.handle_request);thread.start()
                    try:return _client(root/'state/operator.sock',action,self.value.incident_id,self.value.run_id)
                    finally:thread.join(timeout=5)
                from ai_ops.c_recovery import IncidentProjection
                self.fetch=lambda:IncidentProjection.model_validate(exchange('show')['projection'])
                ready=self.wait()
                reply=exchange('request');self.assertEqual(reply['status'],'ok')
                request=server.drafts.read_request(reply['request']['request_id'])
                self.assertGreaterEqual(request.requested_at,ready.image_failure.observed_at)
                self.assertEqual(actions,['show','show','request'])
                with server.drafts._connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM requests').fetchone()[0],1)
                    self.assertEqual(db.execute('SELECT count(*) FROM proposals').fetchone()[0],0)


if __name__=='__main__':unittest.main()
