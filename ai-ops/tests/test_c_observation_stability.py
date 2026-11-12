"""대상 변경 없이 발생하는 Argo 관측 메타데이터 갱신을 실제 gate에 재현한다."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

from ai_ops.c_approval import ApprovalRejected
from ai_ops.c_execution_preflight import verify_execution_preflight
from ai_ops.c_failure_handoff import transfer_failure_handoff
from ai_ops.c_recovery_snapshot import same_recovery_resources
from ai_ops.c_trial_transport import C4TrialTransport, SnapshotChanged


class ObservationStabilityTest(unittest.TestCase):
    def bound_preflight(self, mutate=lambda snapshot: None):
        from test_c_failure_handoff_runtime import FailureHandoffRuntimeTest
        f=FailureHandoffRuntimeTest();f.setUp();self.addCleanup(f.doCleanups)
        app=f.fixture.live.value['application']
        app['metadata']['generation']=1
        app['metadata']['resourceVersion']='1'
        app['status']['reconciledAt']=(f.now-timedelta(seconds=20)).isoformat()
        transfer_failure_handoff(f.owner,f.record,read_failure=f.read)
        original=f.fixture.live.read
        def read(**kwargs):
            app['metadata']['resourceVersion']='2'
            app['status']['reconciledAt']=(f.now-timedelta(seconds=10)).isoformat()
            mutate(f.fixture.live.value)
            return original(**kwargs)
        f.fixture.live.read=read
        args=f.fixture.preflight_args()
        args.update(live_reader=f.bound,clock=lambda:f.now,
                    expected_lease={'name':'b6-scenario-lock','uid':f.record.lease_uid,
                                    'holder':f.record.executor_holder})
        return verify_execution_preflight(**args)

    def test_reconcile_metadata_refresh_during_bound_read_preserves_execution_gate(self):
        actual=self.bound_preflight()
        self.assertTrue(actual['preflight_ready'])
        self.assertFalse(actual['execution_allowed'])

    def test_single_status_generation_refresh_also_preserves_bound_gate(self):
        def status_update(snapshot):
            snapshot['application']['metadata']['generation'] = 2
        actual=self.bound_preflight(status_update)
        self.assertTrue(actual['preflight_ready'])
        self.assertFalse(actual['execution_allowed'])

    def test_bound_gate_still_refuses_changed_target_or_lock(self):
        def change_uid(snapshot): snapshot['application']['metadata']['uid']='other'
        def change_source(snapshot): snapshot['application']['spec']['source']['targetRevision']='f'*40
        def change_generation(snapshot): snapshot['application']['metadata']['generation']=999
        def change_lease(snapshot): snapshot['lease']['metadata']['resourceVersion']='other'
        for change in (change_uid, change_source, change_generation, change_lease):
            with self.subTest(change=change.__name__), self.assertRaises((ApprovalRejected, ValueError)):
                self.bound_preflight(change)


class SnapshotComparisonTest(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,12,tzinfo=timezone.utc)
        self.before={'application':{'metadata':{'uid':'app','resourceVersion':'opaque-old',
            'generation':1,'annotations':{'owner':'lab'},'managedFields':[]},
            'spec':{'source':{'targetRevision':'a'*40}},
            'status':{'reconciledAt':(self.now-timedelta(seconds=20)).isoformat(),
                      'health':{'status':'Degraded'}}},
            'deployment':{'metadata':{'uid':'dep','resourceVersion':'7'},'image':'bad'},
            'lease':{'metadata':{'uid':'lease','resourceVersion':'8'},'holder':'c3'}}
        self.after=deepcopy(self.before)
        self.after['application']['metadata']['resourceVersion']='opaque-new'
        self.after['application']['status']['reconciledAt']=(self.now-timedelta(seconds=10)).isoformat()

    def same(self):
        return same_recovery_resources(self.before,self.after,now=self.now)

    def test_coupled_observation_refresh_is_accepted_without_mutating_sources(self):
        before,after=deepcopy(self.before),deepcopy(self.after)
        self.assertTrue(self.same())
        self.assertEqual(self.before,before);self.assertEqual(self.after,after)
        self.assertTrue(same_recovery_resources(before,before,now=self.now))

    def test_observation_refresh_cannot_hide_any_other_change(self):
        paths=[('application','metadata','uid'),('application','metadata','generation'),
               ('application','metadata','annotations'),('application','metadata','managedFields'),
               ('application','spec','source'),('application','status','health'),
               ('deployment','metadata','resourceVersion'),('deployment','image'),
               ('lease','metadata','resourceVersion'),('lease','holder')]
        original=deepcopy(self.after)
        for path in paths:
            self.after=deepcopy(original);value=self.after
            for key in path[:-1]:value=value[key]
            value[path[-1]]='changed'
            with self.subTest(path=path):self.assertFalse(self.same())
        self.after=deepcopy(original)
        self.after['application']['metadata']['deletionTimestamp']=self.now.isoformat()
        self.assertFalse(self.same())

    def test_uncoupled_missing_backward_future_or_malformed_refresh_is_refused(self):
        original=deepcopy(self.after)
        for field,value in [('resourceVersion','opaque-old'),('resourceVersion',''),
                            ('resourceVersion',7),('reconciledAt','not-time'),
                            ('reconciledAt',(self.now-timedelta(seconds=20)).isoformat()),
                            ('reconciledAt',(self.now-timedelta(seconds=30)).isoformat()),
                            ('reconciledAt',(self.now+timedelta(seconds=1)).isoformat()),
                            ('reconciledAt',self.now.replace(tzinfo=None).isoformat())]:
            self.after=deepcopy(original)
            container='metadata' if field=='resourceVersion' else 'status'
            self.after['application'][container][field]=value
            with self.subTest(field=field,value=value):self.assertFalse(self.same())
        for container,field in [('metadata','resourceVersion'),('status','reconciledAt')]:
            self.after=deepcopy(original);del self.after['application'][container][field]
            with self.subTest(missing=field):self.assertFalse(self.same())

    def test_only_single_generation_advance_with_coupled_observation_is_allowed(self):
        self.after['application']['metadata']['generation']=2
        self.assertTrue(self.same())
        for generation in (0,3,True,'2',None):
            self.after['application']['metadata']['generation']=generation
            with self.subTest(generation=generation):self.assertFalse(self.same())
        self.after['application']['metadata']['generation']=2
        self.after['application']['status']['reconciledAt']=self.before['application']['status']['reconciledAt']
        self.assertFalse(self.same())

    def test_transport_keeps_read_barriers_and_rejects_lease_cas_change(self):
        transport=C4TrialTransport.__new__(C4TrialTransport);transport.clock=lambda:self.now
        versions={'application':iter([self.before['application'],self.after['application']]),
                  'deployment':iter([self.before['deployment']]*2),
                  'lease':iter([self.before['lease']]*2)}
        def get(key):return {'items':[]} if key in ('pods','replicasets') else next(versions[key])
        transport.get=Mock(side_effect=get)
        self.assertEqual(transport.snapshot()['application'],self.before['application'])
        self.after['lease']['metadata']['resourceVersion']='changed'
        versions={name:iter([self.before[name],self.after[name]]) for name in versions}
        with self.assertRaises(SnapshotChanged):transport.snapshot()


if __name__=='__main__':unittest.main()
