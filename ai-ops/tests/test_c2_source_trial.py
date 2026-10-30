"""C-02 활성 소스 전환 준비가 안전한 기준선만 받아들이는지 확인한다."""

from copy import deepcopy
import json
import unittest

from ai_ops.c2_source_trial import prepare_source_trial


REPO = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener"
BASE = "a" * 40
FAIL = "b" * 40
GOOD = "sha256:" + "1" * 64
BAD = "sha256:" + "0" * 64


def values(digest):
    return json.dumps({"api": {"image": {"repository": REPO, "digest": digest}}}).encode()


def inputs():
    app = {"metadata": {"name": "lab-target", "namespace": "argocd", "uid": "app-uid"},
           "spec": {"project": "b6-lab", "source": {
               "repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git",
               "path": "ai-ops/lab", "targetRevision": BASE},
               "destination": {"server": "https://kubernetes.default.svc", "namespace": "ai-ops-lab"}},
           "status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}}
    deployment = {"metadata": {"name": "lab-api", "namespace": "ai-ops-lab", "uid": "dep-uid"},
                  "spec": {"replicas": 1, "template": {"spec": {"containers": [
                      {"image": REPO + "@" + GOOD}]}}}, "status": {"readyReplicas": 1}}
    lease = {"metadata": {"name": "b6-scenario-lock", "namespace": "ai-ops-lab"}, "spec": {}}
    return app, deployment, lease


class SourceTrialTest(unittest.TestCase):
    def test_valid_plan_stays_inactive(self):
        result = prepare_source_trial(*inputs(), values(GOOD), values(BAD),
                                      baseline_revision=BASE, failure_revision=FAIL,
                                      good_present=True, bad_absent=True)
        self.assertEqual(result["maximum_failure_seconds"], 300)
        self.assertEqual(result["normal_image"], REPO + "@" + GOOD)
        self.assertFalse(result["ready_to_inject"])
        self.assertFalse(result["cluster_or_github_write_performed"])

    def test_stale_or_locked_or_unverified_source_is_rejected(self):
        for change in (lambda a, d, l: a["spec"]["source"].update(targetRevision=FAIL),
                       lambda a, d, l: d["status"].update(readyReplicas=0),
                       lambda a, d, l: l["spec"].update(holderIdentity="other"),
                       lambda a, d, l: d["spec"]["template"]["spec"]["containers"][0].update(image=REPO + "@" + BAD)):
            app, deployment, lease = deepcopy(inputs())
            change(app, deployment, lease)
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare_source_trial(app, deployment, lease, values(GOOD), values(BAD),
                                     baseline_revision=BASE, failure_revision=FAIL,
                                     good_present=True, bad_absent=True)
        for good_present, bad_absent in ((False, True), (True, False)):
            with self.assertRaises(ValueError):
                prepare_source_trial(*inputs(), values(GOOD), values(BAD),
                                     baseline_revision=BASE, failure_revision=FAIL,
                                     good_present=good_present, bad_absent=bad_absent)
