"""이전 정상 CI와 현재 이미지 취득 장애의 연결 실패를 검증한다."""

from copy import deepcopy
from datetime import timedelta
import json
import unittest

from ai_ops.c_recovery import IMAGE_REPOSITORY
from ai_ops.c_recovery_proof import assess_recovery_proof
from ai_ops.normal_ci_history import VerifiedNormalHistory
from test_normal_ci import fixture


class RecoveryProofTest(unittest.TestCase):
    def setUp(self):
        _, _, _, self.binding, _, receipt = fixture()
        self.history = VerifiedNormalHistory("12345", "1", "99", "sha256:" + "f" * 64,
                                             "a" * 40, receipt)
        self.bad = "sha256:" + "3" * 64
        self.image = IMAGE_REPOSITORY + "@" + self.bad
        later = receipt.finished_at + timedelta(hours=1)
        timestamp = later.isoformat().replace("+00:00", "Z")
        self.app = {"metadata": {"name": "lab-target", "namespace": "argocd", "uid": "argo-1"},
                    "spec": {"project": "b6-lab", "source": {
                        "repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git",
                        "path": "ai-ops/lab", "targetRevision": "b" * 40},
                        "destination": {"server": "https://kubernetes.default.svc",
                                        "namespace": "ai-ops-lab"}},
                    "status": {"sync": {"status": "Synced", "revision": "b" * 40},
                               "health": {"status": "Degraded"},
                               "operationState": {"phase": "Succeeded", "startedAt": timestamp,
                                                  "syncResult": {"revision": "b" * 40}}}}
        self.deployment = {"metadata": {"name": "lab-api", "namespace": "ai-ops-lab",
                                        "uid": self.binding.app.deployment_uid, "generation": 2},
                           "spec": {"replicas": 1, "template": {"spec": {"containers": [
                               {"name": "api", "image": self.image}]}}},
                           "status": {"observedGeneration": 2, "readyReplicas": 0}}
        self.lease = {"metadata": {"name": "b6-scenario-lock", "namespace": "ai-ops-lab"},
                      "spec": {}}
        self.values = (json.dumps({"api": {"image": {
            "repository": IMAGE_REPOSITORY, "digest": self.bad}}}, indent=2) + "\n").encode()
        self.rs = {"metadata": {"name": "lab-api-rs", "namespace": "ai-ops-lab",
                                "uid": "rs-1", "ownerReferences": [{"kind": "Deployment",
                                "uid": self.binding.app.deployment_uid, "controller": True}]},
                   "spec": {"template": {"spec": {"containers": [{"image": self.image}]}}}}
        self.pods = [{"metadata": {"name": "lab-api-rs-1", "namespace": "ai-ops-lab",
                                   "creationTimestamp": timestamp, "ownerReferences": [
                                       {"kind": "ReplicaSet", "name": "lab-api-rs",
                                        "uid": "rs-1", "controller": True}]},
                      "spec": {"containers": [{"name": "api", "image": self.image,
                                               "ports": [{"containerPort": 8000}]}]},
                      "status": {"containerStatuses": [{"name": "api", "image": self.image,
                                                         "ready": False, "state": {"waiting": {
                                                             "reason": "ImagePullBackOff"}}}]}}]
        self.ecr = {"imageDetails": [{"registryId": "716174522908",
                                      "repositoryName": "urlshortener",
                                      "imageDigest": self.binding.app.image.split("@", 1)[1]}]}

    def assess(self, **changes):
        data = dict(application=self.app, deployment=self.deployment, lease=self.lease,
                    values_bytes=self.values, replica_set=self.rs, pods=self.pods,
                    binding=self.binding, history=self.history, ecr_response=self.ecr,
                    bad_digest_absent=True,
                    now=self.history.receipt.finished_at + timedelta(hours=2))
        data.update(changes)
        return assess_recovery_proof(**data)

    def test_proof_can_be_ready_but_cannot_approve_or_create_proposal(self):
        result = self.assess()
        self.assertTrue(result["proof_ready"])
        self.assertFalse(result["proposal_allowed"])
        self.assertEqual(result["blockers"], ["incident_correlation_and_request_required"])
        self.assertEqual(result["prior_digest"], self.binding.app.image.split("@", 1)[1])
        self.assertEqual(result["current_digest"], self.bad)

    def test_healthy_app_and_active_lease_stop_before_historical_proof(self):
        self.deployment["status"]["readyReplicas"] = 1
        self.app["status"]["health"]["status"] = "Healthy"
        self.lease["spec"]["holderIdentity"] = "other-ci"
        result = self.assess(history=None)
        self.assertEqual(result["blockers"], ["already_recovered", "lab_locked"])
        self.assertFalse(result["proof_ready"])

    def test_other_pod_or_missing_ecr_digest_cannot_become_proof(self):
        pod = deepcopy(self.pods)
        pod[0]["metadata"]["ownerReferences"][0]["uid"] = "other-rs"
        result = self.assess(pods=pod)
        self.assertIn("current_pod_binding_unverified", result["blockers"])
        self.assertFalse(result["proof_ready"])
        result = self.assess(ecr_response={"imageDetails": []})
        self.assertIn("prior_digest_not_found_in_ecr", result["blockers"])
        result = self.assess(bad_digest_absent=False)
        self.assertIn("current_digest_absence_unverified", result["blockers"])

    def test_stale_or_unrelated_ci_and_non_pull_failure_cannot_become_proof(self):
        app = deepcopy(self.app)
        app["status"]["operationState"]["syncResult"]["revision"] = "c" * 40
        result = self.assess(application=app)
        self.assertIn("failure_revision_or_prior_baseline_unverified", result["blockers"])
        deployment = deepcopy(self.deployment)
        deployment["metadata"]["uid"] = "new-deployment"
        result = self.assess(deployment=deployment)
        self.assertIn("failure_revision_or_prior_baseline_unverified", result["blockers"])
        pod = deepcopy(self.pods)
        pod[0]["status"]["containerStatuses"][0]["state"]["waiting"]["reason"] = "CrashLoopBackOff"
        result = self.assess(pods=pod)
        self.assertIn("current_image_pull_failure_unverified", result["blockers"])
        history = deepcopy(self.history)
        history = VerifiedNormalHistory("99999", history.run_attempt, history.artifact_id,
                                        history.archive_digest, history.source_revision, history.receipt)
        result = self.assess(history=history)
        self.assertIn("normal_ci_origin_mismatch", result["blockers"])


if __name__ == "__main__":
    unittest.main()
