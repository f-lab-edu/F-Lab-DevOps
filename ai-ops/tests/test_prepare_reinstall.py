"""재설치 입력이 과거 주소·미등록 이미지를 그대로 사용하지 않는지 검사한다."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_reinstall", ROOT / "scripts/prepare_reinstall.py")
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)


class PrepareReinstallTest(unittest.TestCase):
    def setUp(self):
        text = (ROOT / "config/b3-values.preflight.yaml").read_text()
        self.base = json.loads("\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")))
        self.base["network"]["apiServerCIDRs"] = ["10.99.99.99/32"]
        self.registration = {"published": True, "repository": "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops",
                             "registry_digest": "sha256:" + "a" * 64, "image_id": "sha256:" + "b" * 64, "source_revision": "c" * 40}
        self.service = {"spec": {"clusterIP": "172.20.0.1", "ports": [{"port": 443, "protocol": "TCP"}]}}
        self.endpoints = {"items": [{"addressType": "IPv4", "ports": [{"port": 443}],
                                    "endpoints": [{"addresses": ["10.0.7.11"], "conditions": {"ready": True}}]}]}

    def test_fresh_values_replace_addresses_and_image_without_enabling_base(self):
        original = deepcopy(self.base)
        values = PREPARE.build_values(self.base, self.registration, self.service, self.endpoints)
        self.assertEqual(values["network"]["apiServerCIDRs"], ["10.0.7.11/32", "172.20.0.1/32"])
        self.assertEqual(values["image"]["digest"], self.registration["registry_digest"])
        self.assertFalse(values["enabled"])
        self.assertEqual(values["persistence"]["storageClassName"], "ai-ops-gp3-encrypted")
        self.assertEqual(values["config"]["bindings"]["bindings"][0]["target"]["environment"], "production")
        self.assertEqual(self.base, original)

    def test_unpublished_or_foreign_image_is_rejected(self):
        for changes in ({"published": False}, {"repository": "foreign/repo"}, {"registry_digest": "latest"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                PREPARE.build_values(self.base, {**self.registration, **changes}, self.service, self.endpoints)

    def test_no_ready_endpoint_or_wrong_port_is_rejected(self):
        for change in (lambda ep: ep["items"][0]["endpoints"][0]["conditions"].update(ready=False),
                       lambda ep: ep["items"][0]["ports"][0].update(port=6443)):
            endpoints = deepcopy(self.endpoints); change(endpoints)
            with self.assertRaises(ValueError):
                PREPARE.build_values(self.base, self.registration, self.service, endpoints)

    def test_public_or_unspecified_api_address_is_rejected(self):
        for address in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "::1"):
            endpoints = deepcopy(self.endpoints)
            endpoints["items"][0]["endpoints"][0]["addresses"] = [address]
            with self.subTest(address=address), self.assertRaises(ValueError):
                PREPARE.build_values(self.base, self.registration, self.service, endpoints)


if __name__ == "__main__":
    unittest.main()
