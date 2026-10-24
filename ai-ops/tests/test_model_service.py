"""모델 배포 정의의 기본 중지·자격·네트워크 경계를 검사한다."""

import json
import unittest

from ai_ops.model_service import build_service


IMAGE = ("716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/"
         "urlshortener-practice-ai-ops@sha256:" + "a" * 64)


class ModelServiceTests(unittest.TestCase):
    def setUp(self):
        self.items = build_service(IMAGE)["items"]

    def named(self, kind, name):
        return next(x for x in self.items if x["kind"] == kind and x["metadata"]["name"] == name)

    def test_disabled_by_default_and_separate_credentials(self):
        for name in ("model-gateway", "model-egress-proxy"):
            deployment = self.named("Deployment", name)
            self.assertEqual(deployment["spec"]["replicas"], 0)
            pod = deployment["spec"]["template"]["spec"]
            self.assertFalse(pod["automountServiceAccountToken"])
            self.assertEqual(pod["serviceAccountName"], name)
            self.assertFalse(any("projected" in volume for volume in pod["volumes"]))
        gateway = self.named("Deployment", "model-gateway")["spec"]["template"]["spec"]
        proxy = self.named("Deployment", "model-egress-proxy")["spec"]["template"]["spec"]
        self.assertEqual(gateway["containers"][0]["env"][-1],
                         {"name": "AI_MODEL_ENABLED", "value": "false"})
        self.assertEqual({x["secret"]["secretName"] for x in gateway["volumes"] if "secret" in x},
                         {"model-openai-key", "model-client-auth"})
        self.assertFalse(any("secret" in x or "persistentVolumeClaim" in x for x in proxy["volumes"]))
        self.assertFalse(any(x["kind"] in ("Secret", "Role", "RoleBinding", "ClusterRole",
                                            "ClusterRoleBinding") for x in self.items))
        pvc = self.named("PersistentVolumeClaim", "model-ledger")
        self.assertEqual(pvc["spec"]["storageClassName"], "ai-ops-gp3-encrypted")

    def test_gateway_egress_only_proxy_and_proxy_fixed_port(self):
        gateway = self.named("NetworkPolicy", "model-gateway")["spec"]
        self.assertEqual(gateway["egress"][0]["ports"][0]["port"], 8080)
        self.assertEqual(gateway["egress"][0]["to"][0]["podSelector"]["matchLabels"]["app"],
                         "model-egress-proxy")
        self.assertEqual({port["port"] for port in gateway["egress"][1]["ports"]}, {53})
        self.assertEqual(gateway["egress"][1]["to"][0]["podSelector"]["matchLabels"]["k8s-app"],
                         "kube-dns")
        self.assertNotIn("0.0.0.0/0", json.dumps(gateway))
        self.assertEqual(gateway["ingress"][0]["from"][0]["namespaceSelector"]
                         ["matchLabels"]["kubernetes.io/metadata.name"], "ai-ci")
        proxy = self.named("NetworkPolicy", "model-egress-proxy")["spec"]
        self.assertEqual(proxy["egress"][1]["ports"], [{"protocol": "TCP", "port": 443}])
        self.assertNotIn("ci-relay-github", json.dumps(self.items))
        self.assertNotIn("cluster-admin", json.dumps(self.items))

    def test_mutable_or_other_registry_image_rejected(self):
        for image in ("python:latest", IMAGE.replace("@sha256:", ":"),
                      IMAGE.replace("716174522908", "000000000000")):
            with self.assertRaises(ValueError):
                build_service(image)
