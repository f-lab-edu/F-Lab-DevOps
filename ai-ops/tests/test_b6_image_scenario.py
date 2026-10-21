"""실습 이미지 변경의 부재 확인·경합 거부·보고서 한계와 항상 복구를 검사한다."""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_ops.b6_lab import PURPOSE
from ai_ops.b6_scenarios import LabClient, execute


REPOSITORY = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener"
RUN = "1" * 32
IMAGE = REPOSITORY + "@sha256:" + "a" * 64
BAD = REPOSITORY + ":b6-missing-" + RUN


class FakeImageLab:
    def __init__(self, failure=None, supported=True):
        self.failure, self.supported = failure, supported
        self.actions = []
        self.identity = {"lab-api": {"uid": "api", "generation": 1, "image": IMAGE},
                         "lab-redis": {"uid": "redis", "generation": 1, "image": "fixed"}}

    def production(self):
        return {"production": "unchanged"}

    def guard(self, require_quality=True):
        return deepcopy(self.identity)

    def missing_image(self, run):
        if self.failure == "absence":
            raise ValueError("access denied, not absence")
        return BAD

    def app(self, action, run):
        return {"http": {"/readyz": 200, "/items": 200}, "redis_ping": True, "http_calls": 2}

    def replace_image(self, baseline, old, new):
        self.actions.append("patch")
        if self.failure == "patch-response":
            self.identity["lab-api"]["image"] = new
            raise ValueError("response lost")
        self.identity["lab-api"]["image"] = new

    def wait_image_failure(self, baseline, bad):
        self.actions.append("observe")
        if self.failure == "observe":
            raise ValueError("no actual image error")
        return {"waiting_reason": "ImagePullBackOff", "ready_replicas": 0, "event_reasons": ["Failed"]}

    def collect(self, run):
        self.actions.append("collect")
        if self.failure == "collect":
            raise ValueError("collection failure")
        return {"diagnosis_status": "suspected_cause" if self.supported else "insufficient_evidence",
                "event_reasons": [{"id": "ev-event-0", "reason": "Failed", "image_failure_marker": True}],
                "hypothesis_evidence_ids": [["ev-event-0"]] if self.supported else []}

    def restore_image(self, baseline, bad):
        self.actions.append("restore")
        if self.failure == "restore":
            raise ValueError("restore failure")
        self.identity["lab-api"]["image"] = baseline["image"]
        self.identity["lab-api"]["generation"] += 2
        return self.app("health", RUN)


class ImageScenarioTest(unittest.TestCase):
    def run_image(self, client, **kwargs):
        return execute("S3", client, run_id=RUN, recovery_plan=kwargs.pop("recovery_plan", Mock()), **kwargs)

    def test_recovery_plan_precedes_mutation_and_truth_precedes_collection(self):
        lab = FakeImageLab()
        def plan(value):
            self.assertEqual(lab.actions, [])
            self.assertEqual(value["baseline"]["image"], IMAGE)
        def truth(value):
            self.assertEqual(lab.actions, ["patch", "observe"])
            self.assertNotIn("report", value)
        result = self.run_image(lab, recovery_plan=plan, checkpoint=truth)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(all(result["cleanup"].values()))
        self.assertEqual(result["http_calls"], 4)

    def test_all_post_mutation_failures_attempt_restore(self):
        for failure in ("patch-response", "observe", "collect"):
            with self.subTest(failure=failure):
                lab = FakeImageLab(failure=failure)
                result = self.run_image(lab)
                self.assertEqual(result["status"], "failed")
                self.assertIn("restore", lab.actions)
                self.assertEqual(lab.identity["lab-api"]["image"], IMAGE)
                self.assertTrue(all(result["cleanup"].values()))

    def test_unsupported_rule_report_is_review_needed_after_successful_recovery(self):
        result = self.run_image(FakeImageLab(supported=False))
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(all(result["cleanup"].values()))

    def test_unrelated_failed_event_is_not_image_cause_support(self):
        lab = FakeImageLab()
        original = lab.collect
        def collect(run):
            report = original(run)
            report["event_reasons"][0]["image_failure_marker"] = False
            return report
        lab.collect = collect
        result = self.run_image(lab)
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(all(result["cleanup"].values()))

    def test_restoration_failure_is_not_success(self):
        result = self.run_image(FakeImageLab(failure="restore"))
        self.assertEqual(result["status"], "recovery_failed")

    def test_ecr_authorization_error_never_becomes_missing_tag(self):
        lab = FakeImageLab(failure="absence")
        result = self.run_image(lab)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(lab.actions, [])

    def test_missing_persistent_recovery_plan_blocks_before_change(self):
        lab = FakeImageLab()
        result = self.run_image(lab, recovery_plan=None)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(lab.actions, [])

    def test_only_explicit_ecr_image_not_found_is_accepted(self):
        client = LabClient()
        valid = {"images": [], "failures": [{"failureCode": "ImageNotFound", "imageId": {"imageTag": "b6-missing-" + RUN}}]}
        with patch("ai_ops.b6_scenarios.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(valid))):
            self.assertEqual(client.missing_image(RUN), BAD)
        for data in ({"images": [], "failures": []}, {"images": [{}], "failures": []},
                     {"images": [], "failures": [{"failureCode": "AccessDenied"}]}):
            with self.subTest(data=data), patch("ai_ops.b6_scenarios.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps(data))):
                with self.assertRaises(ValueError):
                    client.missing_image(RUN)

    def test_replaced_uid_and_concurrent_image_are_not_overwritten(self):
        client = LabClient()
        obj = {"metadata": {"uid": "api", "resourceVersion": "7", "labels": PURPOSE},
               "spec": {"replicas": 1, "strategy": {"type": "Recreate"}, "template": {"spec": {"containers": [{"name": "api", "image": IMAGE}]}}}}
        for mutation in ("uid", "image"):
            changed = deepcopy(obj)
            if mutation == "uid": changed["metadata"]["uid"] = "other"
            else: changed["spec"]["template"]["spec"]["containers"][0]["image"] = REPOSITORY + "@sha256:" + "b" * 64
            with self.subTest(mutation=mutation), patch.object(client, "get", return_value=changed), patch.object(client, "command") as command:
                with self.assertRaises(ValueError):client.replace_image({"uid": "api"}, IMAGE, BAD)
                command.assert_not_called()
        with patch.object(client, "get", return_value=obj), patch.object(client, "command") as command:
            client.replace_image({"uid": "api"}, IMAGE, BAD)
            patch_data=json.loads(command.call_args.args[-1])
            self.assertEqual([p["op"] for p in patch_data], ["test", "test", "test", "replace"])
            self.assertEqual(patch_data[1]["value"], "7")
