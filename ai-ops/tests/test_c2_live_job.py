"""C-02 일회성 조사 Job의 대상·이미지·저장소·권한 범위를 검사한다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from ai_ops.c2_live_job import build_live_capture_job, validate_live_preflight


IMAGE = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops@sha256:" + "a" * 64


def worker_deployment() -> dict:
    return {"metadata": {"name": "ci-worker", "namespace": "ai-ci", "uid": "worker-uid"},
            "spec": {"replicas": 1, "template": {"metadata": {"labels": {"app": "ci-worker"}}, "spec": {"serviceAccountName": "ci-worker",
                "containers": [{"name": "ci-worker", "image": IMAGE},
                               {"name": "projection-read", "image": IMAGE}],
                "volumes": [{"name": "config", "projected": {"sources": [
                    {"configMap": {"name": "ci-config"}}, {"configMap": {"name": "ci-normal-binding"}}]}},
                    {"name": "data", "persistentVolumeClaim": {"claimName": "ci-queue"}},
                    {"name": "identity", "secret": {"secretName": "do-not-copy"}}]}}}}


class C2LiveJobTest(unittest.TestCase):
    def test_fresh_verified_failure_is_required(self):
        now = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)
        proof = {"target": "urlshortener/ai-ops-lab/lab-api/practice", "base_sha": "b" * 40,
                 "proof_ready": True, "blockers": ["incident_correlation_and_request_required"],
                 "proposal_allowed": False, "cluster_or_github_write_performed": False,
                 "current_digest": "sha256:" + "c" * 64,
                 "proof_checked_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")}
        validate_live_preflight(proof, failure_revision="b" * 40, now=now)
        for field, value in (("proof_ready", False), ("blockers", ["already_recovered"]),
                             ("base_sha", "a" * 40), ("proposal_allowed", True),
                             ("proof_checked_at", (now - timedelta(seconds=61)).isoformat().replace("+00:00", "Z"))):
            changed = {**proof, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_live_preflight(changed, failure_revision="b" * 40, now=now)

    def test_fixed_target_and_no_credential_or_network_mount(self):
        job = build_live_capture_job(worker_deployment(), run_id=123, failure_revision="b" * 40)
        self.assertEqual(job["metadata"]["namespace"], "ai-ci")
        self.assertEqual(job["metadata"]["name"], "c2-live-capture-123")
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 180)
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual([v["name"] for v in pod["volumes"]], ["config", "data", "tmp"])
        self.assertEqual(pod["volumes"][1]["persistentVolumeClaim"]["claimName"], "ci-queue")
        self.assertEqual(pod["containers"][0]["image"], IMAGE)
        args = pod["containers"][0]["args"]
        self.assertEqual(args[args.index("--target") + 1], "url-shortener-b6-lab")
        self.assertEqual(args[args.index("--request-id") + 1], "c2-live-capture-123")
        self.assertEqual(pod["affinity"]["podAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][0]["labelSelector"]["matchLabels"], {"app": "ci-worker"})

    def test_other_namespace_image_pvc_and_service_account_are_rejected(self):
        for mutate in (lambda d: d["metadata"].update(namespace="production"),
                       lambda d: d["spec"].update(replicas=0),
                       lambda d: d["spec"]["template"]["spec"].update(serviceAccountName="default"),
                       lambda d: d["spec"]["template"]["metadata"]["labels"].update(app="other"),
                       lambda d: d["spec"]["template"]["spec"]["containers"][0].update(image="other"),
                       lambda d: d["spec"]["template"]["spec"]["containers"][1].update(image="other"),
                       lambda d: d["spec"]["template"]["spec"].update(containers=[{"name": "ci-worker", "image": IMAGE}]),
                       lambda d: d["spec"]["template"]["spec"]["volumes"][1]["persistentVolumeClaim"].update(claimName="other"),
                       lambda d: d["spec"]["template"]["spec"]["volumes"][0].update(projected={"sources": []})):
            with self.subTest(mutate=mutate):
                deployment = worker_deployment()
                mutate(deployment)
                with self.assertRaises(ValueError):
                    build_live_capture_job(deployment, run_id=123, failure_revision="b" * 40)

    def test_invalid_run_and_revision_are_rejected(self):
        for run_id, revision in ((True, "b" * 40), (0, "b" * 40), (123, "short")):
            with self.assertRaises(ValueError):
                build_live_capture_job(deepcopy(worker_deployment()), run_id=run_id, failure_revision=revision)


if __name__ == "__main__":
    unittest.main()
