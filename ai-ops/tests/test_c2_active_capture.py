"""활성 GitOps 실패만 조사 Job의 입력으로 허용하는지 확인한다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from ai_ops.c2_active_capture import build_active_capture_job, validate_active_failure
from test_c2_live_job import worker_deployment


REVISION = "b" * 40
BAD = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener@sha256:" + "0" * 64
NOW = datetime(2026, 10, 30, 5, tzinfo=timezone.utc)
STARTED = (NOW - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")


def fixture():
    app = {"metadata": {"name": "lab-target", "namespace": "argocd", "uid": "app-uid"},
           "spec": {"project": "b6-lab", "source": {
               "repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git",
               "path": "ai-ops/lab", "targetRevision": REVISION},
               "destination": {"server": "https://kubernetes.default.svc", "namespace": "ai-ops-lab"}},
           "status": {"sync": {"status": "Synced", "revision": REVISION},
                      "operationState": {"phase": "Succeeded", "startedAt": STARTED,
                                         "operation": {"sync": {"revision": REVISION}},
                                         "syncResult": {"revision": REVISION}}}}
    dep = {"metadata": {"name": "lab-api", "namespace": "ai-ops-lab", "uid": "dep", "generation": 2},
           "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "api", "image": BAD,
                                                                     "env": []}]}}},
           "status": {"observedGeneration": 2, "readyReplicas": 0}}
    rs = {"metadata": {"namespace": "ai-ops-lab", "uid": "rs",
                       "ownerReferences": [{"kind": "Deployment", "uid": "dep"}]}}
    pod = {"metadata": {"namespace": "ai-ops-lab", "creationTimestamp": STARTED,
                        "ownerReferences": [{"kind": "ReplicaSet", "uid": "rs"}]},
           "spec": {"containers": [{"name": "api", "image": BAD, "resources": {}}]},
           "status": {"containerStatuses": [{"name": "api", "image": BAD, "ready": False,
                                             "state": {"waiting": {"reason": "ImagePullBackOff"}}}]}}
    lease = {"metadata": {"name": "b6-scenario-lock", "namespace": "ai-ops-lab"}, "spec": {}}
    return app, dep, rs, [pod], lease


class ActiveCaptureTest(unittest.TestCase):
    def test_current_active_failure_passes(self):
        validate_active_failure(*fixture(), failure_revision=REVISION, failure_image=BAD,
                                application_uid="app-uid", deployment_uid="dep", now=NOW)

    def test_old_source_locked_or_recovered_is_rejected(self):
        for edit in (lambda x: x[0]["spec"]["source"].update(targetRevision="a" * 40),
                     lambda x: x[0]["metadata"].update(uid="other"),
                     lambda x: x[1]["metadata"].update(uid="other"),
                     lambda x: x[1]["status"].update(readyReplicas=1),
                     lambda x: x[4]["spec"].update(holderIdentity="ci-123"),
                     lambda x: x[3][0]["status"]["containerStatuses"][0]["state"]["waiting"].update(reason="Running")):
            values = deepcopy(fixture())
            edit(values)
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                validate_active_failure(*values, failure_revision=REVISION,
                                        failure_image=BAD, application_uid="app-uid",
                                        deployment_uid="dep", now=NOW)

    def test_job_uses_existing_bounded_worker_without_identity_secret(self):
        job = build_active_capture_job(worker_deployment(), trial_id=123, failure_revision=REVISION)
        self.assertEqual(job["metadata"]["name"], "c2-source-capture-123")
        self.assertEqual(job["metadata"]["namespace"], "ai-ci")
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual([v["name"] for v in pod["volumes"]], ["config", "data", "tmp"])
        self.assertIn("c2-source-capture-123", pod["containers"][0]["args"])
