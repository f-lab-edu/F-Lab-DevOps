"""C-02 일회성 조사 Job의 대상·이미지·저장소·권한 범위를 검사한다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from ai_ops.c2_live_job import build_live_capture_job, validate_s10_live_failure


IMAGE = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops@sha256:" + "a" * 64
BAD = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener@sha256:" + "c" * 64


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
    def test_current_s10_failure_and_run_lock_are_required(self):
        now = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)
        started = (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
        holder = "ci-" + hashlib.sha256(b"123:1").hexdigest()[:32]
        app = {"metadata": {"name": "lab-target", "namespace": "argocd"},
               "spec": {"project": "b6-lab", "source": {"repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git",
                        "path": "ai-ops/lab", "targetRevision": "a" * 40}},
               "status": {"operationState": {"phase": "Succeeded", "startedAt": started,
                          "operation": {"sync": {"revision": "b" * 40}},
                          "syncResult": {"revision": "b" * 40}}}}
        deployment = {"metadata": {"name": "lab-api", "namespace": "ai-ops-lab", "uid": "dep", "generation": 2},
                      "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "api", "image": BAD}]}}},
                      "status": {"observedGeneration": 2, "readyReplicas": 0}}
        replica_set = {"metadata": {"namespace": "ai-ops-lab", "uid": "rs",
                                    "ownerReferences": [{"kind": "Deployment", "uid": "dep"}]}}
        pod = {"metadata": {"namespace": "ai-ops-lab", "creationTimestamp": started,
                            "ownerReferences": [{"kind": "ReplicaSet", "uid": "rs"}]},
               "spec": {"containers": [{"name": "api", "image": BAD}]},
               "status": {"containerStatuses": [{"name": "api", "image": BAD, "ready": False,
                                                 "state": {"waiting": {"reason": "ErrImagePull"}}}]}}
        lease = {"metadata": {"name": "b6-scenario-lock", "namespace": "ai-ops-lab"},
                 "spec": {"holderIdentity": holder}}
        items = [app, deployment, replica_set, [pod], lease]
        def verify(values):
            validate_s10_live_failure(*values, baseline_revision="a" * 40,
                                      failure_revision="b" * 40, failure_image=BAD,
                                      run_id=123, run_attempt=1, now=now)
        verify(items)
        for index, edit in ((0, lambda x: x["spec"]["source"].update(targetRevision="b" * 40)),
                            (1, lambda x: x["status"].update(readyReplicas=1)),
                            (3, lambda x: x[0]["status"]["containerStatuses"][0]["state"]["waiting"].update(reason="Running")),
                            (4, lambda x: x["spec"].update(holderIdentity="other"))):
            changed = deepcopy(items)
            edit(changed[index])
            with self.subTest(index=index), self.assertRaises(ValueError):
                verify(changed)

    def test_fixed_target_and_no_credential_or_network_mount(self):
        job = build_live_capture_job(worker_deployment(), run_id=123, run_attempt=1, failure_revision="b" * 40)
        self.assertEqual(job["metadata"]["namespace"], "ai-ci")
        self.assertEqual(job["metadata"]["name"], "c2-live-capture-123-1")
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 180)
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        pod = job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual([v["name"] for v in pod["volumes"]], ["config", "data", "tmp"])
        self.assertEqual(pod["volumes"][1]["persistentVolumeClaim"]["claimName"], "ci-queue")
        self.assertEqual(pod["containers"][0]["image"], IMAGE)
        args = pod["containers"][0]["args"]
        self.assertEqual(args[args.index("--target") + 1], "url-shortener-b6-lab")
        self.assertEqual(args[args.index("--request-id") + 1], "c2-live-capture-123-1")
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
                    build_live_capture_job(deployment, run_id=123, run_attempt=1, failure_revision="b" * 40)

    def test_invalid_run_and_revision_are_rejected(self):
        for run_id, revision in ((True, "b" * 40), (0, "b" * 40), (123, "short")):
            with self.assertRaises(ValueError):
                build_live_capture_job(deepcopy(worker_deployment()), run_id=run_id, run_attempt=1, failure_revision=revision)


if __name__ == "__main__":
    unittest.main()
