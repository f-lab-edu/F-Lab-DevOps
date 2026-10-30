"""독립 원복 경계가 고정 앱만 허용하고 낯선 상태에서 멈추는지 검사한다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.c2_rollback_guard import rollback_decision, validate_plan


BASE = "a" * 40
FAIL = "b" * 40
REPO = "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener@sha256:"
GOOD = REPO + "1" * 64
BAD = REPO + "0" * 64
NOW = datetime(2026, 10, 30, 5, tzinfo=timezone.utc)


def fixture():
    plan = {"schema_version": 1, "target": "urlshortener/ai-ops-lab/lab-api/practice",
            "application_uid": "app-uid", "deployment_uid": "dep-uid",
            "baseline_revision": BASE, "failure_revision": FAIL,
            "normal_image": GOOD, "failure_image": BAD,
            "deadline_utc": (NOW + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")}
    app = {"metadata": {"name": "lab-target", "namespace": "argocd", "uid": "app-uid"},
           "spec": {"project": "b6-lab", "source": {
               "repoURL": "https://github.com/f-lab-edu/F-Lab-DevOps.git",
               "path": "ai-ops/lab", "targetRevision": BASE},
               "destination": {"server": "https://kubernetes.default.svc", "namespace": "ai-ops-lab"}},
           "status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}}
    deployment = {"metadata": {"name": "lab-api", "namespace": "ai-ops-lab", "uid": "dep-uid"},
                  "spec": {"replicas": 1, "template": {"spec": {"containers": [{"image": GOOD}]}}},
                  "status": {"readyReplicas": 1}}
    return plan, app, deployment


class RollbackGuardTest(unittest.TestCase):
    @staticmethod
    def runner():
        script = Path(__file__).parents[1] / "scripts/run_c2_rollback_guard.py"
        spec = importlib.util.spec_from_file_location("c2_rollback_runner", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_application_read_uses_existing_argo_scope(self):
        module = self.runner()
        with patch.object(module, "call", return_value=b'{"metadata":{"name":"lab-target"}}') as command:
            self.assertEqual(module.get_application()["metadata"]["name"], "lab-target")
        command.assert_called_once_with("argocd", "--server", "argocd.bidservice.store",
                                        "--grpc-web", "app", "get", "lab-target", "-o", "json")

    def test_local_read_uses_core_and_requires_private_argocd_context(self):
        module = self.runner()
        with patch.object(module, "call", return_value=b'{"metadata":{"name":"lab-target"}}') as command:
            module.get_application(module.CORE_ARGO)
        command.assert_called_once_with("argocd", "--core", "app", "get", "lab-target", "-o", "json")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "kubeconfig"
            path.write_text("test")
            path.chmod(0o600)
            with patch.object(module, "call", return_value=b"argocd"):
                module.verify_local_kubeconfig(str(path))
            with patch.object(module, "call", return_value=b"default"), self.assertRaises(ValueError):
                module.verify_local_kubeconfig(str(path))
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                module.verify_local_kubeconfig(str(path))

    def test_preflight_requires_healthy_ready_baseline(self):
        module = self.runner()
        plan, app, deployment = fixture()
        module.verify_normal(plan, app, deployment)
        deployment["status"]["readyReplicas"] = 0
        with self.assertRaises(ValueError):
            module.verify_normal(plan, app, deployment)

    def test_deadline_and_three_allowed_states(self):
        plan, app, deployment = fixture()
        self.assertEqual(validate_plan(plan, now=NOW), NOW + timedelta(minutes=10))
        self.assertEqual(rollback_decision(plan, app, deployment), "verify_normal")
        deployment["spec"]["template"]["spec"]["containers"][0]["image"] = BAD
        self.assertEqual(rollback_decision(plan, app, deployment), "sync_baseline")
        app["spec"]["source"]["targetRevision"] = FAIL
        self.assertEqual(rollback_decision(plan, app, deployment), "set_and_sync_baseline")

    def test_other_uid_revision_image_or_scope_is_rejected(self):
        for change in (lambda p,a,d: a["metadata"].update(uid="other"),
                       lambda p,a,d: d["metadata"].update(uid="other"),
                       lambda p,a,d: a["spec"]["source"].update(targetRevision="c" * 40),
                       lambda p,a,d: d["spec"]["template"]["spec"]["containers"][0].update(image=REPO + "2" * 64),
                       lambda p,a,d: a["spec"].update(syncPolicy={"automated": {}}),
                       lambda p,a,d: a["spec"]["destination"].update(namespace="production")):
            plan, app, deployment = deepcopy(fixture())
            change(plan, app, deployment)
            with self.subTest(change=change), self.assertRaises(ValueError):
                rollback_decision(plan, app, deployment)

    def test_long_or_expired_or_other_target_plan_is_rejected(self):
        for change in (lambda p: p.update(target="production"),
                       lambda p: p.update(deadline_utc=(NOW + timedelta(minutes=16)).isoformat()),
                       lambda p: p.update(deadline_utc=(NOW - timedelta(seconds=1)).isoformat()),
                       lambda p: p.update(failure_revision=BASE)):
            plan = fixture()[0]
            change(plan)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_plan(plan, now=NOW)
