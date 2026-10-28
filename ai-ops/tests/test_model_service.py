"""모델 배포 정의의 기본 중지·자격·네트워크 경계를 검사한다."""

import json
import unittest

from ai_ops.model_service import build_service, build_prepared_service


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

    def test_prepared_service_adds_only_narrow_read_and_internal_path(self):
        items = build_prepared_service(IMAGE, ["10.2.3.4/32"])["items"]
        def find(kind, name, namespace):
            return next(x for x in items if x["kind"] == kind and
                        x["metadata"]["name"] == name and
                        x["metadata"].get("namespace") == namespace)
        for name in ("model-gateway", "model-egress-proxy"):
            self.assertEqual(find("Deployment", name, "ai-model")["spec"]["replicas"], 0)
        gateway = find("Deployment", "model-gateway", "ai-model")["spec"]["template"]["spec"]
        self.assertEqual(gateway["containers"][0]["env"][-1],
                         {"name": "AI_MODEL_ENABLED", "value": "false"})
        self.assertTrue(any("projected" in volume for volume in gateway["volumes"]))
        for namespace, entries in (
            ("ai-ops-lab", (("deployments", "lab-api"), ("configmaps", "lab-collector"))),
            ("ai-ci", (("configmaps", "ci-config"), ("configmaps", "ci-normal-binding"))),
            ("ai-model", (("deployments", "model-gateway"),)),
        ):
            role = find("Role", "model-gateway-current-read", namespace)
            self.assertEqual(role["rules"], [
                {"apiGroups": ["apps" if resource == "deployments" else ""],
                 "resources": [resource], "resourceNames": [name], "verbs": ["get"]}
                for resource, name in entries])
            self.assertEqual(find("RoleBinding", "model-gateway-current-read", namespace)
                             ["subjects"][0]["namespace"], "ai-model")
        egress = find("NetworkPolicy", "ci-worker-model-gateway", "ai-ci")["spec"]["egress"]
        self.assertEqual(egress[0]["ports"], [{"protocol": "TCP", "port": 8081}])
        self.assertEqual(find("NetworkPolicy", "model-gateway", "ai-model")["spec"]
                         ["egress"][-1]["to"], [{"ipBlock": {"cidr": "10.2.3.4/32"}}])
        self.assertFalse(any(x["kind"] == "Secret" for x in items))

    def test_prepared_service_rejects_public_or_broad_api_network(self):
        for cidr in ("0.0.0.0/0", "8.8.8.8/32", "10.2.3.0/24", "127.0.0.1/32"):
            with self.subTest(cidr=cidr), self.assertRaises(ValueError):
                build_prepared_service(IMAGE, [cidr])
