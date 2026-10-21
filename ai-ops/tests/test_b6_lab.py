"""실습 정의가 운영 연결·쓰기 권한·외부 노출로 변형되는 것을 검사한다."""

import copy
import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ai_ops.b6_lab import ECR, LabInputs, NAMESPACE, build_lab, validate_lab


def inputs():
    return LabInputs(app_image=ECR + "urlshortener@sha256:" + "1" * 64,
                     collector_image=ECR + "urlshortener-practice-ai-ops@sha256:" + "2" * 64,
                     postgres_image="docker.io/library/postgres@sha256:" + "3" * 64,
                     redis_image="docker.io/library/redis@sha256:" + "4" * 64,
                     api_server_cidrs=["10.0.0.1/32"])


class B6LabTest(TestCase):
    def test_only_lab_namespace_and_no_persistent_or_external_resources(self):
        bundle = build_lab(inputs())
        summary = validate_lab(bundle)
        self.assertEqual(summary["database_topology"], "single_primary_no_replica")
        self.assertFalse(summary["external_model_enabled"])
        self.assertFalse(summary["persistent_storage"])
        for item in bundle["items"]:
            self.assertEqual(item["metadata"].get("namespace", item["metadata"]["name"]), NAMESPACE)
            self.assertNotIn(item["kind"], {"PersistentVolumeClaim", "Ingress", "ClusterRole", "Application"})

    def test_mutations_cannot_point_at_production_or_expand_authority(self):
        original = build_lab(inputs())
        cases = [
            ("Role", lambda d: d["rules"].append({"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]})),
            ("Service", lambda d: d["spec"].update(type="LoadBalancer")),
            ("Deployment", lambda d: d["metadata"].update(namespace="url-shortener")),
            ("Secret", lambda d: d["stringData"].update(DATABASE_URL="postgresql://production.invalid/db")),
            ("NetworkPolicy", lambda d: d["spec"].update(egress=[{}])),
        ]
        for kind, mutate in cases:
            with self.subTest(kind=kind):
                bundle = copy.deepcopy(original)
                mutate(next(d for d in bundle["items"] if d["kind"] == kind))
                with self.assertRaises(ValueError):
                    validate_lab(bundle)

    def test_registry_tags_and_broad_api_network_are_rejected(self):
        for updates in ({"app_image": ECR + "urlshortener:latest"},
                        {"postgres_image": "unknown.invalid/postgres@sha256:" + "3" * 64},
                        {"api_server_cidrs": ["0.0.0.0/0"]}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                LabInputs.model_validate({**inputs().model_dump(), **updates})

    def test_each_preparation_gets_its_own_password_and_config_never_contains_it(self):
        first, second = build_lab(inputs()), build_lab(inputs())
        secret = next(d for d in first["items"] if d["kind"] == "Secret")["stringData"]["POSTGRES_PASSWORD"]
        other = next(d for d in second["items"] if d["kind"] == "Secret")["stringData"]["POSTGRES_PASSWORD"]
        self.assertNotEqual(secret, other)
        for item in first["items"]:
            if item["kind"] != "Secret":
                self.assertNotIn(secret, str(item))

    def test_wrong_context_blocks_before_any_cluster_mutation(self):
        script = Path(__file__).resolve().parents[1] / "scripts/deploy_b6_lab.py"
        spec = importlib.util.spec_from_file_location("deploy_lab", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, "command", return_value="other-cluster") as command:
            with self.assertRaisesRegex(ValueError, "context"):
                module.deploy(build_lab(inputs()))
        command.assert_called_once_with("kubectl", "config", "current-context")

    def test_unowned_namespace_is_never_overwritten(self):
        script = Path(__file__).resolve().parents[1] / "scripts/deploy_b6_lab.py"
        spec = importlib.util.spec_from_file_location("deploy_lab", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, "production_identity", return_value={}), \
                patch.object(module, "command", side_effect=[module.CONTEXT, '{"metadata":{"labels":{}}}']) as command:
            with self.assertRaisesRegex(ValueError, "namespace"):
                module.deploy(build_lab(inputs()))
        self.assertEqual(command.call_count, 2)

    def test_probe_rejects_unknown_or_unexpected_authorization_answers(self):
        script = Path(__file__).resolve().parents[1] / "scripts/probe_b6_lab.py"
        spec = importlib.util.spec_from_file_location("probe_lab", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for answer in ("", "yes"):
            with self.subTest(answer=answer), patch.object(module, "command", return_value=answer):
                with self.assertRaisesRegex(ValueError, "권한"):
                    module.permissions()
