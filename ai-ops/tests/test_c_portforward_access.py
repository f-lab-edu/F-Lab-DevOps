"""사건 조회 토큰이 현재 Pod의 포트 전달만 허용하는지 검사한다."""

from copy import deepcopy
import unittest

from ai_ops.c_portforward_access import build_portforward_access


class PortforwardAccessTest(unittest.TestCase):
    def setUp(self):
        self.pod = {"metadata": {"name": "ci-worker-bcd4f56ff-gg22s", "namespace": "ai-ci",
                    "uid": "00b6412a-a098-4c42-a2cc-49715d269023", "labels": {"app": "ci-worker"},
                    "ownerReferences": [{"controller": True, "kind": "ReplicaSet",
                                         "name": "ci-worker-bcd4f56ff",
                                         "uid": "11111111-1111-1111-1111-111111111111"}]},
                    "spec": {"serviceAccountName": "ci-worker", "containers": [
                        {"name": "ci-worker"}, {"name": "projection-read", "ports": [{"containerPort": 9444}]}]},
                    "status": {"phase": "Running", "containerStatuses": [
                        {"name": "ci-worker", "ready": True}, {"name": "projection-read", "ready": True}]}}
        self.replica_set = {"metadata": {"name": "ci-worker-bcd4f56ff", "namespace": "ai-ci",
                           "uid": "11111111-1111-1111-1111-111111111111",
                           "ownerReferences": [{"controller": True, "kind": "Deployment",
                                                "name": "ci-worker",
                                                "uid": "22222222-2222-2222-2222-222222222222"}]}}
        self.deployment = {"metadata": {"name": "ci-worker", "namespace": "ai-ci",
                              "uid": "22222222-2222-2222-2222-222222222222"},
                           "spec": {"replicas": 1}, "status": {"readyReplicas": 1}}

    def test_role_grants_only_current_pod_get_and_portforward_create(self):
        items = build_portforward_access(self.pod, self.replica_set, self.deployment)["items"]
        self.assertEqual([item["kind"] for item in items], ["ServiceAccount", "Role", "RoleBinding"])
        self.assertFalse(items[0]["automountServiceAccountToken"])
        self.assertEqual(items[1]["rules"], [
            {"apiGroups": [""], "resources": ["pods"],
             "resourceNames": ["ci-worker-bcd4f56ff-gg22s"], "verbs": ["get"]},
            {"apiGroups": [""], "resources": ["pods/portforward"],
             "resourceNames": ["ci-worker-bcd4f56ff-gg22s"], "verbs": ["create"]}])

    def test_changed_owner_or_unready_sidecar_is_rejected(self):
        for mutation in (
            lambda p, r, d: p["metadata"]["ownerReferences"][0].update(uid="33333333-3333-3333-3333-333333333333"),
            lambda p, r, d: r["metadata"]["ownerReferences"][0].update(name="other"),
            lambda p, r, d: p["status"]["containerStatuses"][1].update(ready=False),
            lambda p, r, d: p["spec"]["containers"][1].update(ports=[{"containerPort": 9443}]),
            lambda p, r, d: d["status"].update(readyReplicas=0),
        ):
            pod, replica_set, deployment = deepcopy((self.pod, self.replica_set, self.deployment))
            mutation(pod, replica_set, deployment)
            with self.assertRaises(ValueError):
                build_portforward_access(pod, replica_set, deployment)


if __name__ == "__main__":
    unittest.main()
