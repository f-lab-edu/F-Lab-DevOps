"""실제 전송 직전 대상·설정·이미지 재확인이 변경을 차단하는지 검증한다."""

import copy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest

from ai_ops.b_contracts import ReadResponse
from ai_ops.contracts import sha256_json
from ai_ops.model_live_probe import ModelLiveProbe
from test_model_projection import ROOT, TARGET


class FakeKubernetes:
    def __init__(self, responses):
        self.responses = responses
        self.paths = []

    def get(self, request, *, timeout_seconds, max_bytes):
        self.paths.append(request.path)
        return ReadResponse(200, json.dumps(self.responses[request.path]).encode())


class ModelLiveProbeTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ca = Path(temporary.name) / "ca.crt"
        self.ca.write_bytes(b"test-kubernetes-ca")
        config_root = ROOT / "config"
        self.files = {name: (config_root / source).read_text() for name, source in (
            ("bindings.json", "targets.practice.example.json"),
            ("runtime.json", "b-runtime.example.json"),
            ("metric-queries.json", "metric-queries.json"))}
        binding = json.loads(self.files["bindings.json"])
        binding["bindings"][0]["target"] = TARGET
        self.files["bindings.json"] = json.dumps(binding)
        from ai_ops.b_contracts import BindingRegistry, BRuntime, MetricCatalog
        parsed = [model.model_validate_json(self.files[name]) for name, model in (
            ("bindings.json", BindingRegistry), ("runtime.json", BRuntime),
            ("metric-queries.json", MetricCatalog))]
        self.binding = {"cluster_fingerprint": "a" * 64, "deployment_uid": "deployment-1",
                        "config_hash": sha256_json([item.model_dump(mode="json") for item in parsed]),
                        "image_digest": "sha256:" + "b" * 64}
        self.responses = {
            "/apis/apps/v1/namespaces/ai-ops-lab/deployments/lab-api": {
                "metadata": {"uid": "deployment-1"}},
            "/api/v1/namespaces/ai-ci/configmaps/ci-config": {"data": self.files},
            "/apis/apps/v1/namespaces/ai-model/deployments/model-gateway": {
                "spec": {"replicas": 1, "template": {"spec": {"containers": [{"name": "model-gateway",
                    "image": "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/"
                             "urlshortener-practice-ai-ops@" + self.binding["image_digest"]}]}}}},
        }

    def probe(self, responses):
        transport = FakeKubernetes(responses)
        probe = ModelLiveProbe(expected_cluster_fingerprint="a" * 64,
            expected_ca_sha256=hashlib.sha256(self.ca.read_bytes()).hexdigest(),
            ca=self.ca, transport=transport)
        return probe, transport

    def test_current_identity_and_config_match(self):
        probe, transport = self.probe(self.responses)
        probe.verify(self.binding)
        self.assertEqual(len(transport.paths), 3)

    def test_changed_uid_config_image_or_ca_stops(self):
        for path, mutate in (
            ("/apis/apps/v1/namespaces/ai-ops-lab/deployments/lab-api",
             lambda x: x["metadata"].update(uid="another")),
            ("/api/v1/namespaces/ai-ci/configmaps/ci-config",
             lambda x: x["data"].update({"runtime.json": "{}"})),
            ("/apis/apps/v1/namespaces/ai-model/deployments/model-gateway",
             lambda x: x["spec"]["template"]["spec"]["containers"][0].update(image="other:latest")),
        ):
            changed = copy.deepcopy(self.responses)
            mutate(changed[path])
            probe, _ = self.probe(changed)
            with self.subTest(path=path), self.assertRaises(ValueError):
                probe.verify(self.binding)
        probe, _ = self.probe(self.responses)
        self.ca.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            probe.verify(self.binding)


if __name__ == "__main__":
    unittest.main()
