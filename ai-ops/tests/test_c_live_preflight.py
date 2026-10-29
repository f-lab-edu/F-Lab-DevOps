"""복구된 앱과 비활성 values가 변경안으로 이어지지 않는지 확인한다."""

import json
import unittest

from ai_ops.c_live_preflight import assess_live_preflight


class LivePreflightTest(unittest.TestCase):
    def setUp(self):
        self.app = {"metadata": {"name": "lab-target", "namespace": "argocd"},
                    "spec": {"project": "b6-lab", "source": {
                        "repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git",
                        "path": "ai-ops/lab", "targetRevision": "a"*40,
                        "directory": {"include": "deployment.json"}},
                        "destination": {"server": "https://kubernetes.default.svc", "namespace": "ai-ops-lab"}},
                    "status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}}
        self.deployment = {"metadata": {"name": "lab-api", "namespace": "ai-ops-lab", "uid": "b"*36},
                           "spec": {"replicas": 1, "template": {"spec": {"containers": [{
                               "image": "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener@sha256:"+"2"*64}]}}},
                           "status": {"readyReplicas": 1}}
        self.lease = {"metadata": {"name": "b6-scenario-lock", "namespace": "ai-ops-lab"}, "spec": {}}
        self.values = json.dumps({"api": {"image": {
            "repository": "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener",
            "digest": "sha256:"+"1"*64}}}).encode()

    def test_inactive_values_and_recovered_app_cannot_create_proposal(self):
        result = assess_live_preflight(self.app, self.deployment, self.lease, self.values)
        self.assertEqual(result["blockers"], ["values_source_inactive", "values_stale", "already_recovered"])
        self.assertFalse(result["proposal_allowed"])

    def test_even_ready_helm_needs_verified_previous_digest_and_failure(self):
        self.app["spec"]["source"].pop("directory")
        self.app["status"]["health"]["status"] = "Degraded"
        self.deployment["status"]["readyReplicas"] = 0
        self.deployment["spec"]["template"]["spec"]["containers"][0]["image"] = (
            "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener@sha256:"+"1"*64)
        result = assess_live_preflight(self.app, self.deployment, self.lease, self.values)
        self.assertEqual(result["blockers"], ["prior_digest_and_failure_proof_required"])
        self.assertFalse(result["proposal_allowed"])

    def test_other_application_is_rejected(self):
        self.app["spec"]["destination"]["namespace"] = "default"
        with self.assertRaises(ValueError):
            assess_live_preflight(self.app, self.deployment, self.lease, self.values)


if __name__ == "__main__":
    unittest.main()
