"""실제 명령 adapter가 고정 대상·명령·자격 전달 범위를 지키는지 확인한다."""

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from ai_ops.c_trial_transport import C4TrialTransport, SnapshotChanged


class TrialTransportTest(unittest.TestCase):
    def transport(self):
        transport = C4TrialTransport.__new__(C4TrialTransport)
        transport._call = Mock(return_value=b"{}")
        transport._kube = Mock(return_value=b"{}")
        return transport

    def test_argo_secret_is_only_in_environment_and_empty_config_is_used(self):
        transport = self.transport()
        with patch.dict(os.environ, {"C04_SYNC_ARGOCD_TOKEN": "synthetic-token"}, clear=True):
            transport.argo("sync", ["app", "set", "lab-target", "--revision", "a"*40])
        args, kwargs = transport._call.call_args
        self.assertNotIn("synthetic-token", args[0])
        self.assertEqual(kwargs["env"]["ARGOCD_AUTH_TOKEN"], "synthetic-token")
        self.assertIn("--config", args[0])
        self.assertIn("--server", args[0])

    def test_missing_token_or_other_app_is_refused(self):
        transport = self.transport()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                transport.argo("sync", ["app", "set", "lab-target", "--revision", "a"*40])
        with self.assertRaises(ValueError):
            transport.argo("sync", ["app", "set", "production", "--revision", "a"*40])
        transport._call.assert_not_called()

    def test_lease_create_and_foreign_replace_are_refused(self):
        transport = self.transport()
        with self.assertRaises(ValueError):
            transport.lease_command("create", "-f", "-", "-o", "json", payload="{}")
        lease = {"kind": "Lease", "apiVersion": "coordination.k8s.io/v1",
                 "metadata": {"name": "other", "namespace": "default"}}
        with self.assertRaises(ValueError):
            transport.lease_command("replace", "-f", "-", "-o", "json", payload=json.dumps(lease))
        transport._kube.assert_not_called()

    def test_wrong_capture_job_namespace_is_refused(self):
        transport = self.transport()
        with self.assertRaises(ValueError):
            transport.create_capture_job({"kind": "Job", "metadata": {
                "name": "c2-source-capture-123", "namespace": "url-shortener"}})
        transport._kube.assert_not_called()

    def test_truncated_kubernetes_list_is_refused(self):
        transport = self.transport()
        transport._kube.return_value = b'{"metadata":{"continue":"next"},"items":[]}'
        with self.assertRaises(ValueError):
            transport.get("pods")

    def test_parallel_snapshot_keeps_second_read_barrier_and_refuses_change(self):
        transport=self.transport()
        transport.clock=lambda:datetime.now(timezone.utc)
        counts={};lock=threading.Lock();active=0;maximum=0;change=False
        def get(key):
            nonlocal active,maximum
            with lock:
                counts[key]=counts.get(key,0)+1
                number=counts[key];active+=1;maximum=max(maximum,active)
            time.sleep(.01)
            with lock:active-=1
            if key in ('pods','replicasets'):return {'items':[]}
            return {'version':2 if change and key=='application' and number==2 else 1}
        transport.get=get
        self.assertEqual(transport.snapshot()['application'],{'version':1})
        self.assertGreater(maximum,1)
        self.assertEqual(counts,{'application':2,'deployment':2,'lease':2,'pods':1,'replicasets':1})
        counts.clear();change=True
        with self.assertRaises(ValueError):transport.snapshot()

    def test_normal_wait_retries_only_an_inconsistent_snapshot(self):
        transport=self.transport();transport.monotonic=lambda:0;transport.sleep=Mock()
        normal={'application':{'spec':{'source':{'targetRevision':'a'*40}},'status':{
            'sync':{'revision':'a'*40,'status':'Synced'},'health':{'status':'Healthy'},
            'operationState':{'phase':'Succeeded','syncResult':{'revision':'a'*40}}}},
            'deployment':{'metadata':{'generation':1},'spec':{'template':{'spec':{'containers':[{'image':'normal'}]}}},
                          'status':{'readyReplicas':1,'observedGeneration':1}}}
        transport.snapshot=Mock(side_effect=[SnapshotChanged('synthetic'),normal])
        self.assertEqual(transport.wait_healthy('a'*40,'normal'),normal)
        transport.sleep.assert_called_once_with(1)
        transport.snapshot=Mock(side_effect=PermissionError('synthetic'))
        with self.assertRaises(PermissionError):transport.wait_healthy('a'*40,'normal')


if __name__ == "__main__":
    unittest.main()
