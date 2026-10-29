"""실습 GitOps 범위와 공유 잠금의 경쟁·복구 실패·소유권 충돌을 검증한다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import contextlib
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.b6_coordination import LabLease, NOT_BEFORE_KEY, OWNER_KEY, require_manual_deployment
from ai_ops.b6_gitops import build_gitops, validate_gitops
from ai_ops.b6_lab import ECR, LabInputs, NAMESPACE, build_lab
from ai_ops.b6_scenarios import LabClient
from ai_ops.contracts import canonical_json_bytes
import hashlib


NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def bundle():
    return build_lab(LabInputs(app_image=ECR + "urlshortener@sha256:" + "1" * 64,
        collector_image=ECR + "urlshortener-practice-ai-ops@sha256:" + "2" * 64,
        postgres_image="docker.io/library/postgres@sha256:" + "3" * 64,
        redis_image="docker.io/library/redis@sha256:" + "4" * 64, api_server_cidrs=["10.0.0.1/32"]), password="a" * 48)


class LeaseServer:
    def __init__(self):
        self.value = None
        self.writes = 0
        self.race = False

    def command(self, *args, payload=None, **kwargs):
        if "get" in args:
            return json.dumps(self.value) if self.value else ""
        desired = json.loads(payload)
        if args[0] == "create":
            if self.value is not None:
                raise ValueError("already exists")
            desired["metadata"].update(uid="lock-uid", resourceVersion="1")
        elif args[0] == "replace":
            if (self.race or desired["metadata"]["uid"] != self.value["metadata"]["uid"]
                    or desired["metadata"]["resourceVersion"] != self.value["metadata"]["resourceVersion"]):
                raise ValueError("conflict")
            desired["metadata"]["resourceVersion"] = str(int(self.value["metadata"]["resourceVersion"]) + 1)
        else:
            raise AssertionError("예상 밖 쓰기")
        self.value = desired
        self.writes += 1
        return json.dumps(desired)

    def lease(self, suffix="1", now=NOW):
        return LabLease(self.command, "manual-" + suffix * 32, clock=lambda: now)


class B6GitOpsTest(unittest.TestCase):
    def test_definition_manages_one_deployment_without_secret_values_or_new_pod_spec(self):
        original = bundle()
        plan = build_gitops(original, "a" * 40)
        result = validate_gitops(plan, original)
        before = next(item for item in original["items"] if item["kind"] == "Deployment" and item["metadata"]["name"] == "lab-api")
        self.assertEqual(plan["deployment"]["spec"], before["spec"])
        self.assertEqual(plan["deployment"]["metadata"]["annotations"][OWNER_KEY], "gitops")
        self.assertNotIn("a" * 48, json.dumps(plan))
        self.assertEqual(result["managed_resources"], ["apps/Deployment:ai-ops-lab/lab-api"])
        self.assertFalse(result["automatic_sync_enabled"])
        self.assertFalse(plan["credentials_created"])

    def test_expanded_source_destination_rbac_automation_and_policy_rejected(self):
        original = bundle()
        plan = build_gitops(original, "a" * 40)
        changes = [
            lambda p: p["deployment"]["metadata"].update(name="other"),
            lambda p: p["bootstrap"][-1]["spec"]["syncPolicy"].update(automated={"selfHeal": True}),
            lambda p: p["bootstrap"][-1]["spec"]["destination"].update(namespace="url-shortener"),
            lambda p: p["bootstrap"][-1]["spec"]["source"].update(path="url-shortener/url-shortener-chart"),
            lambda p: p["bootstrap"][2]["spec"]["roles"][0]["policies"].append("p, role, applications, *, */*, allow"),
            lambda p: p["bootstrap"][2]["spec"].update(namespaceResourceWhitelist=[{"group": "*", "kind": "*"}]),
            lambda p: p["bootstrap"][0]["spec"]["validations"][0].update(expression="true"),
            lambda p: p["bootstrap"][0]["spec"]["matchConditions"][0].update(expression="false"),
        ]
        for change in changes:
            mutated = deepcopy(plan); change(mutated)
            with self.assertRaises(ValueError):
                validate_gitops(mutated, original)

    def test_project_roles_and_coordinator_have_no_app_write_secret_exec_or_lease_create(self):
        plan = build_gitops(bundle(), "a" * 40)
        project = next(item for item in plan["bootstrap"] if item["kind"] == "AppProject")
        self.assertEqual(len(project["spec"]["roles"][0]["policies"]), 2)
        for rule in next(item for item in plan["bootstrap"] if item["kind"] == "Role" and item["metadata"]["name"] == "b6-s10-coordinator")["rules"]:
            self.assertTrue(set(rule["verbs"]) <= {"get", "update", "list"})
            self.assertNotIn("secrets", rule["resources"])
            if rule["resources"] == ["deployments"]:
                self.assertEqual(rule["verbs"], ["get"])
            if rule["resources"] == ["pods"]:
                self.assertEqual(rule["verbs"], ["list"])
            elif rule["resources"] == ["replicasets"]:
                self.assertEqual(rule["verbs"], ["get"])
            else:
                self.assertEqual(len(rule["resourceNames"]), 1)

    def test_repository_deployment_is_exact_generated_lab_app(self):
        document = json.loads((Path(__file__).resolve().parents[1] / "lab/deployment.json").read_text())
        original = bundle()
        app = next(item for item in original["items"] if item["kind"] == "Deployment" and item["metadata"]["name"] == "lab-api")
        app["spec"]["template"]["spec"]["containers"][0]["image"] = document["spec"]["template"]["spec"]["containers"][0]["image"]
        self.assertEqual(document, build_gitops(original, "a" * 40)["deployment"])

    def test_binding_uses_existing_argo_namespace_and_invalid_revision_is_rejected(self):
        original = bundle()
        plan = build_gitops(original, "a" * 40)
        self.assertEqual(plan["collector_bindings"]["bindings"][0]["argo"]["namespace"], "argocd")
        for revision in ("main", "a" * 7, "a" * 40 + "/other"):
            with self.assertRaises(ValueError):
                build_gitops(original, revision)

    def test_manual_owner_tracking_or_instance_blocks_direct_image_patch(self):
        for metadata in ({"annotations": {OWNER_KEY: "gitops"}},
                         {"annotations": {"argocd.argoproj.io/tracking-id": "lab-target:apps/Deployment:ai-ops-lab/lab-api"}},
                         {"labels": {"app.kubernetes.io/instance": "lab-target"}}):
            with self.assertRaises(ValueError):
                require_manual_deployment({"metadata": metadata})
            client = LabClient()
            with patch.object(client, "get", return_value={"metadata": metadata}), patch.object(client, "command") as command:
                with self.assertRaises(ValueError):
                    client.replace_image({"uid": "api"}, ECR + "urlshortener@sha256:" + "1" * 64,
                                         ECR + "urlshortener:b6-missing-" + "2" * 32)
                command.assert_not_called()
        require_manual_deployment({"metadata": {}})

    def test_manual_guard_rejects_registered_application_before_any_mutation(self):
        original = bundle()
        objects = {(item["kind"].lower(), item["metadata"]["name"]): item for item in original["items"]}
        def get(namespace, kind, name):
            obj = deepcopy(objects[(kind, name)])
            obj["metadata"].update(uid=name, generation=1)
            if kind == "deployment":
                obj["status"] = {"readyReplicas": 1}
            return obj
        client = LabClient()
        with patch.object(client, "get", side_effect=get), patch.object(client, "command", return_value='{"kind":"Application"}') as command:
            with self.assertRaisesRegex(ValueError, "Application"):
                client.guard(require_quality=False)
            self.assertEqual(command.call_count, 1)

    def script(self, name):
        path = Path(__file__).resolve().parents[1] / "scripts" / name
        spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_source_readiness_requires_exact_committed_deployment(self):
        module = self.script("prepare_b6_gitops.py")
        plan = build_gitops(bundle(), "a" * 40)
        plan["deployment"] = json.loads((Path(__file__).resolve().parents[1] / "lab/deployment.json").read_text())
        original_run = subprocess.run
        def committed_source(args, **kwargs):
            if args[:2] == ["git", "show"]:
                source = args[2].split(":", 1)[1]
                return subprocess.CompletedProcess(args, 0, (module.ROOT / source).read_bytes(), b"")
            return original_run(args, **kwargs)
        with patch.object(module.subprocess, "run", side_effect=committed_source):
            self.assertTrue(module.source_matches(plan))
            altered = deepcopy(plan)
            altered["deployment"]["spec"]["replicas"] = 2
            self.assertFalse(module.source_matches(altered))

    def install_inputs(self):
        original = bundle()
        namespace = deepcopy(original["items"][0])
        namespace["metadata"]["annotations"] = {"ai-ops/b6-bundle-sha256": hashlib.sha256(canonical_json_bytes(original)).hexdigest()}
        return original, namespace

    def test_registered_application_blocks_whole_lab_reinstall_before_apply(self):
        module = self.script("deploy_b6_lab.py")
        original, namespace = self.install_inputs()
        with patch.object(module, "production_identity", return_value={}), \
             patch.object(module, "command", side_effect=[module.CONTEXT, json.dumps(namespace), '{"kind":"Application"}']) as command:
            with self.assertRaisesRegex(ValueError, "GitOps"):
                module.deploy(original)
            self.assertEqual(command.call_count, 3)
            self.assertFalse(any("apply" in call.args for call in command.call_args_list))

    def test_failed_install_keeps_lease_for_recovery(self):
        module = self.script("deploy_b6_lab.py")
        original, namespace = self.install_inputs()
        with patch.object(module, "production_identity", return_value={}), \
             patch.object(module, "command", side_effect=[module.CONTEXT, json.dumps(namespace), "", '{"metadata":{}}']), \
             patch.object(module, "LabLease") as factory, \
             patch.object(module, "_finish_deployment", side_effect=ValueError("installation failed")):
            with self.assertRaises(ValueError):
                module.deploy(original)
            factory.return_value.acquire.assert_called_once()
            factory.return_value.release.assert_not_called()


class LabLeaseTest(unittest.TestCase):
    def test_create_competing_acquire_and_verified_release(self):
        server = LeaseServer(); first = server.lease(); second = server.lease("2")
        first.acquire()
        with self.assertRaisesRegex(ValueError, "다른 실행"):
            second.acquire()
        self.assertEqual(server.writes, 1)
        with self.assertRaises(ValueError):
            first.release(recovery_verified=False)
        first.release(recovery_verified=True)
        self.assertEqual(server.value["spec"]["holderIdentity"], "")
        self.assertEqual(server.value["metadata"]["annotations"][NOT_BEFORE_KEY], (NOW + timedelta(seconds=305)).isoformat())

    def test_shared_cooldown_blocks_even_with_new_local_state_and_next_run_uses_same_uid(self):
        server = LeaseServer(); first = server.lease()
        uid = first.acquire()["uid"]; first.release(recovery_verified=True)
        with self.assertRaisesRegex(ValueError, "지표 창"):
            server.lease("2", NOW + timedelta(seconds=304)).acquire()
        self.assertEqual(server.lease("2", NOW + timedelta(seconds=306)).acquire()["uid"], uid)

    def test_abandoned_holder_is_never_stolen_by_age(self):
        server = LeaseServer(); server.lease().acquire()
        with self.assertRaisesRegex(ValueError, "미복구"):
            server.lease("2", NOW + timedelta(days=30)).acquire()
        self.assertEqual(server.writes, 1)

    def test_cas_conflict_and_foreign_or_replaced_lock_cannot_be_released(self):
        server = LeaseServer(); first = server.lease()
        first.acquire(); first.release(recovery_verified=True, scenario_started=False)
        server.race = True
        with self.assertRaisesRegex(ValueError, "conflict"):
            server.lease("2").acquire()
        server.race = False; first.acquire()
        for key, value in (("uid", "other-uid"),):
            server.value["metadata"][key] = value
            with self.assertRaises(ValueError):
                first.release(recovery_verified=True)
        server.value["metadata"]["uid"] = "lock-uid"
        server.value["spec"]["holderIdentity"] = "manual-" + "2" * 32
        with self.assertRaises(ValueError):
            first.release(recovery_verified=True)

    def test_unowned_expiring_or_wrong_namespace_lease_rejected(self):
        for change in (lambda v: v["metadata"].update(namespace="url-shortener"),
                       lambda v: v["metadata"].update(labels={}),
                       lambda v: v["spec"].update(leaseDurationSeconds=1)):
            server = LeaseServer(); server.lease().acquire()
            server.value["spec"]["holderIdentity"] = ""; change(server.value)
            with self.assertRaises(ValueError):
                server.lease("2").acquire()
            self.assertEqual(server.writes, 1)

    def test_invalid_holder_never_calls_cluster(self):
        with self.assertRaises(ValueError):
            LabLease(lambda *args, **kwargs: self.fail("호출 금지"), "other-holder")


class ScenarioLeaseIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.state = self.root / "state"; self.state.mkdir(mode=0o700)
        self.output = self.root / "result.json"
        script = Path(__file__).resolve().parents[1] / "scripts/run_b6_scenario.py"
        spec = importlib.util.spec_from_file_location("scenario_with_lease", script)
        self.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.module)
        self.args = [str(script), "--scenario", "S1", "--state-dir", str(self.state), "--output", str(self.output)]

    def run_cli(self, cleanup, *, execute_error=False, release_error=False):
        with patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.root)}), patch("sys.argv", self.args), \
             patch.object(self.module, "LabClient"), patch.object(self.module, "LabLease") as factory, \
             patch.object(self.module, "execute") as execute, contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            lease = factory.return_value
            lease.acquire.return_value = {"name": "b6-scenario-lock", "uid": "lease-uid", "holder": "manual-run"}
            lease.release.return_value = {"released": True, "not_before": "2026-10-21T12:05:05Z"}
            execute.return_value = {"status": "passed", "cleanup": cleanup}
            if execute_error:
                execute.side_effect = RuntimeError("private-error-sentinel")
            if release_error:
                lease.release.side_effect = ValueError("conflict")
            if execute_error or release_error:
                with self.assertRaises(SystemExit) as stopped:
                    self.module.main()
                self.assertEqual(stopped.exception.code, 1)
            else:
                status = self.module.main()
                self.assertEqual(status, 0 if cleanup and all(v is True for v in cleanup.values()) else 1)
            return lease

    def test_verified_recovery_releases_and_saves_both_lock_states(self):
        lease = self.run_cli({"restored": True})
        lease.release.assert_called_once_with(recovery_verified=True)
        control = json.loads((self.state / "control.json").read_bytes())
        self.assertTrue(control["recovery_complete"])
        self.assertTrue(control["cluster_lock"]["released"])
        self.assertFalse(json.loads(self.output.read_bytes())["cluster_lock"]["retained_for_recovery"])

    def test_failed_or_non_boolean_recovery_keeps_shared_lock(self):
        lease = self.run_cli({"restored": "true"})
        lease.release.assert_not_called()
        control = json.loads((self.state / "control.json").read_bytes())
        self.assertFalse(control["recovery_complete"])
        result = json.loads(self.output.read_bytes())
        self.assertTrue(result["cluster_lock"]["retained_for_recovery"])
        self.assertEqual(result["status"], "recovery_failed")

    def test_empty_recovery_record_keeps_shared_lock(self):
        lease = self.run_cli({})
        lease.release.assert_not_called()
        self.assertEqual(json.loads(self.output.read_bytes())["status"], "recovery_failed")

    def test_unexpected_stop_preserves_active_control_and_shared_lock(self):
        lease = self.run_cli({"restored": True}, execute_error=True)
        lease.release.assert_not_called()
        self.assertTrue(json.loads((self.state / "control.json").read_bytes())["active"])
        self.assertFalse(self.output.exists())

    def test_release_conflict_never_saves_success(self):
        self.run_cli({"restored": True}, release_error=True)
        self.assertTrue(json.loads((self.state / "control.json").read_bytes())["active"])
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
