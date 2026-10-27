"""정상 CI의 제한 권한·원본 내부 보관·실패 원복·정제 영수증을 검증한다."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_ops.b_contracts import ReadResponse
from ai_ops.b6_lab import CONTEXT, ECR
from ai_ops.b6_sequence_capture import RequestObservation
from ai_ops.collection import CapturedSnapshot
from ai_ops.contracts import build_evidence_bundle, canonical_json_bytes, sha256_json
from ai_ops.model_projection import project_snapshot
from ai_ops.model_sequence_projection import ProbeRead, ProbeRecord, probe_log_hash, project_probe
from ai_ops.normal_ci_contract import (NormalCIBinding, NormalCIIdentity, NormalCIReceipt,
                                      executor_hash)
from ai_ops.normal_ci_rbac import build_normal_rbac
from ai_ops.normal_ci_runner import run_normal_ci, verify_cluster, workflow_identity
from ai_ops.normal_ci_source import NormalSourceStore, SourceRequest
from test_b6_sequence_capture import FakeSequenceClient
from test_live_collectors import AT
from test_model_sequence_projection import case


def fixture():
    snapshot, settings, base, probe, logs = case()
    reads = []
    counters = {}
    for index, outcome in enumerate(("miss", "hit", "miss", "hit")):
        started = AT+timedelta(milliseconds=100+index*150)
        before = dict(counters); counters[outcome] = counters.get(outcome, 0)+1
        reads.append(ProbeRead(started_at=started, log_at=started+timedelta(milliseconds=20),
            finished_at=started+timedelta(milliseconds=40), http_status=200, response_item_id=42,
            counters_before=before, counters_after=dict(counters),
            log_message=f"cache_{outcome} endpoint=get_item item_id=42"))
    logs = ("\n".join(read.log_at.isoformat().replace("+00:00", "Z")+
        " INFO:app.api.routes.item:"+read.log_message for read in reads)+"\n").encode()
    image = ECR+"urlshortener@sha256:"+"1"*64
    evidence = [item.model_copy(deep=True) for item in snapshot.bundle.evidence]
    deployment = next(item for item in evidence if item.kind == "deployment")
    deployment.payload["image"] = image
    deployment.content_hash = sha256_json(deployment.payload)
    bundle = build_evidence_bundle(target=snapshot.bundle.target, window=snapshot.bundle.window,
        created_at=snapshot.bundle.created_at, evidence=evidence)
    snapshot = CapturedSnapshot(bundle, snapshot.audit)
    identity = base.identity.model_copy(update={"exporter_sha256": executor_hash()})
    base = project_snapshot(snapshot, settings, identity)
    probe = probe.model_copy(update={"source_bundle_hash": base.source_bundle_hash,
        "image_reference_hash": sha256_json(image), "reads": reads,
        "log_source_hash": probe_log_hash(reads), "raw_log_sha256": hashlib.sha256(logs).hexdigest()})
    projection = project_probe(base, probe, snapshot, settings, raw_logs=logs,
                               log_pod_uid="pod-1", logs_truncated=False)
    binding = NormalCIBinding.model_validate({"cluster_arn": CONTEXT,
        "cluster_fingerprint": identity.cluster_fingerprint, "config_hash": settings.config_hash,
        "executor_sha256": executor_hash(),
        "app": {"name": "lab-api-abc-123", "uid": "pod-1", "deployment_uid": "deployment-1", "image": image},
        "collector": {"name": "lab-collector-abc-456", "uid": "collector-1", "deployment_uid": "collector-deployment",
                      "image": ECR+"urlshortener-practice-ai-ops@sha256:"+"2"*64}})
    ci = NormalCIIdentity(run_id="12345", run_attempt="1", source_revision="a"*40)
    receipt = NormalCIReceipt.from_projection(projection, identity=ci, binding=binding,
                                             started_at=AT-timedelta(seconds=5), finished_at=AT+timedelta(seconds=5))
    return snapshot, settings, projection, binding, ci, receipt


class FakeNormalClient(FakeSequenceClient):
    def __init__(self, receipt, fail=None, recovery_fail=False):
        super().__init__(fail=fail, recovery_fail=recovery_fail)
        self.receipt = receipt
        self.requests = []

    def source(self, request):
        self.requests.append(request.model_copy(deep=True))
        if request.action == self.fail:
            raise ValueError("PRIVATE_SOURCE_SENTINEL")
        if request.action == "seal": return {"state": "sealed"}
        if request.action == "finish": return self.receipt.model_dump(mode="json")
        return {"state": "started"}


class NormalCITest(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.settings, self.projection, self.binding, self.identity, self.receipt = fixture()

    def run_case(self, client):
        lease = Mock()
        lease.release.return_value = {"released": True}
        result = run_normal_ci(client, self.identity, self.binding, sleep=lambda _: None,
            lease_factory=lambda *_: lease, clock=lambda: AT-timedelta(seconds=5))
        return result, lease

    def test_four_reads_then_cleanup_and_receipt_without_original(self):
        client = FakeNormalClient(self.receipt)
        result, lease = self.run_case(client)
        self.assertEqual(client.actions, ["create", "cold", "hit", "cold", "hit", "cleanup"])
        self.assertEqual([r.action for r in client.requests],
                         ["begin", "checkpoint", "checkpoint", "checkpoint", "checkpoint", "checkpoint", "seal", "finish"])
        self.assertEqual(result["record_type"], "b6_normal_ci_observation")
        raw = canonical_json_bytes(result)
        for forbidden in (b"item_id", b"raw_logs", b"counters_before", b"pod_uid", b'"probe":', b"PRIVATE_SOURCE_SENTINEL"):
            self.assertNotIn(forbidden, raw)
        lease.release.assert_called_once_with(recovery_verified=True, scenario_started=True)

    def test_creation_response_loss_log_seal_failure_and_checkpoint_failure_cleanup(self):
        for failure in ("create", "cold", "checkpoint", "seal"):
            with self.subTest(failure=failure):
                client = FakeNormalClient(self.receipt, fail=failure)
                result, lease = self.run_case(client)
                self.assertEqual(result["record_type"], "b6_normal_ci_failure")
                self.assertIn("cleanup", client.actions)
                lease.release.assert_called_once()
                self.assertNotIn("PRIVATE_SOURCE_SENTINEL", json.dumps(result))

    def test_begin_failure_does_not_create_data_and_releases_without_cooldown(self):
        client = FakeNormalClient(self.receipt, fail="begin")
        result, lease = self.run_case(client)
        self.assertFalse(client.actions)
        self.assertEqual(result["record_type"], "b6_normal_ci_failure")
        lease.release.assert_called_once_with(recovery_verified=True, scenario_started=False)

    def test_cleanup_or_baseline_failure_keeps_lease_and_never_accepts_receipt(self):
        for client in (FakeNormalClient(self.receipt, fail="cleanup"),
                       FakeNormalClient(self.receipt, recovery_fail=True)):
            result, lease = self.run_case(client)
            self.assertEqual(result["reason"], "recovery_unverified")
            lease.release.assert_not_called()

    def test_process_interrupt_still_cleans_owned_data_and_releases_verified_lease(self):
        client = FakeNormalClient(self.receipt)
        original = client.app
        def app(action, run_id):
            if action == "hit": raise KeyboardInterrupt()
            return original(action, run_id)
        client.app = app
        lease = Mock(); lease.release.return_value = {"released": True}
        with self.assertRaises(KeyboardInterrupt):
            run_normal_ci(client, self.identity, self.binding, sleep=lambda _: None,
                lease_factory=lambda *_: lease)
        self.assertIn("cleanup", client.actions)
        lease.release.assert_called_once()

    def test_other_run_receipt_and_code_change_rejected(self):
        client = FakeNormalClient(self.receipt.model_copy(update={
            "identity": self.identity.model_copy(update={"run_attempt": "2"})}))
        result, _ = self.run_case(client)
        self.assertEqual(result["reason"], "source_finalize_failed")
        changed = self.binding.model_copy(update={"executor_sha256": "0"*64})
        with self.assertRaises(ValueError):
            run_normal_ci(client, self.identity, changed)

    def test_roles_only_allow_exact_lab_exec_and_named_lease_update(self):
        docs = build_normal_rbac(self.binding)["items"]
        self.assertFalse(any(item["kind"].startswith("Cluster") for item in docs))
        roles = [item for item in docs if item["kind"] == "Role"]
        self.assertEqual({item["metadata"]["namespace"] for item in roles}, {"ai-ops-lab", "argocd"})
        rules = roles[0]["rules"]
        execute = next(rule for rule in rules if rule["resources"] == ["pods/exec"])
        self.assertEqual(execute["resourceNames"], [self.binding.app.name, self.binding.collector.name])
        self.assertEqual(execute["verbs"], ["create"])
        for rule in rules:
            self.assertNotIn("secrets", rule["resources"])
            self.assertNotIn("delete", rule["verbs"])
            self.assertNotIn("patch", rule["verbs"])
            if rule["resources"] != ["leases"] and rule is not execute:
                self.assertTrue(set(rule["verbs"]) <= {"get", "list"})

    def test_binding_rejects_other_repository_pod_and_duplicate_uid(self):
        for mutate in (lambda value: value["app"].update(name="production-api"),
                       lambda value: value["collector"].update(uid="pod-1"),
                       lambda value: value["app"].update(image="registry.invalid/app@sha256:"+"1"*64)):
            value = self.binding.model_dump(mode="json"); mutate(value)
            with self.assertRaises(ValueError): NormalCIBinding.model_validate(value)

    def test_workflow_identity_rejects_branch_event_and_wrong_workflow(self):
        env = {"GITHUB_REPOSITORY": "f-lab-edu/F-Lab-DevOps", "GITHUB_REF": "refs/heads/main",
               "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_WORKFLOW_REF":
               "f-lab-edu/F-Lab-DevOps/.github/workflows/ai-ops-normal-ci.yml@refs/heads/main",
               "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_SHA": "a"*40}
        with patch.dict(os.environ, env): self.assertEqual(workflow_identity(), self.identity)
        for key, value in (("GITHUB_REF", "refs/heads/topic"), ("GITHUB_EVENT_NAME", "pull_request"),
                           ("GITHUB_WORKFLOW_REF", "other")):
            with patch.dict(os.environ, {**env, key: value}), self.assertRaises(ValueError): workflow_identity()

    def test_same_cluster_name_with_changed_endpoint_or_ca_is_rejected(self):
        cluster = {"arn": CONTEXT, "status": "ACTIVE", "endpoint": "https://example.invalid", "certificateAuthority": {"data": "ca"}}
        binding = self.binding.model_copy(update={"cluster_fingerprint": sha256_json({"arn": CONTEXT,
            "endpoint": cluster["endpoint"], "ca": "ca"})})
        def execute(args, **kwargs):
            data = {"cluster": cluster} if args[0] == "aws" else {
                "clusters": [{"cluster": {"server": cluster["endpoint"], "certificate-authority-data": "ca"}}]}
            return Mock(returncode=0, stdout=(CONTEXT.encode() if args[-1] == "current-context" else json.dumps(data).encode()))
        verify_cluster(binding, execute=execute)
        with self.assertRaises(ValueError): verify_cluster(self.binding, execute=execute)


class NormalSourceTest(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.settings, self.projection, self.binding, self.identity, _ = fixture()
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = NormalSourceStore(self.settings, self.root, clock=lambda: AT+timedelta(seconds=5))

    def request(self, action, **kwargs):
        return SourceRequest(action=action, identity=self.identity, binding=self.binding,
                             started_at=AT-timedelta(seconds=5), **kwargs)

    def test_duplicate_session_different_binding_and_fabricated_recovery_rejected(self):
        self.assertEqual(self.source.handle(self.request("begin")), {"state": "started"})
        with self.assertRaises(FileExistsError): self.source.handle(self.request("begin"))
        wrong = self.request("checkpoint").model_copy(update={"binding": self.binding.model_copy(update={"cluster_fingerprint": "0"*64})})
        with self.assertRaises(ValueError): self.source.handle(wrong)
        self.assertEqual(self.source.handle(self.request("finish", owned_data_removed=True,
            baseline_unchanged=True, lock_released=True)), {"state": "failed"})
        with self.assertRaises(ValueError): self.source.handle(self.request("checkpoint"))

    def test_source_retains_original_privately_but_receipt_excludes_it(self):
        self.source.handle(self.request("begin"))
        path = self.root/"normal-ci"/self.identity.session/"source.json"
        value = json.loads(path.read_bytes())
        value.update(state="sealed", raw_logs="PRIVATE_SOURCE_SENTINEL", projection=self.projection.model_dump(mode="json"))
        from ai_ops.incidents import _atomic_write
        _atomic_write(path, canonical_json_bytes(value))
        result = self.source.handle(self.request("finish", owned_data_removed=True,
                                   baseline_unchanged=True, lock_released=True))
        self.assertEqual(NormalCIReceipt.model_validate(result).view, self.projection.view)
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", json.dumps(result))
        self.assertIn("PRIVATE_SOURCE_SENTINEL", path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_internal_logs_rules_report_projection_and_recovery_form_receipt(self):
        self.source.clock = Mock(side_effect=[AT+timedelta(milliseconds=800), AT+timedelta(seconds=5)])
        self.source.collector = Mock(return_value=self.snapshot)
        observations = [RequestObservation(**{
            key: value for key, value in read.model_dump().items()
            if key in RequestObservation.model_fields},
            request_started_at=read.started_at+timedelta(milliseconds=5),
            request_finished_at=read.finished_at-timedelta(milliseconds=5))
            for read in self.projection.probe.reads]
        logs = ("\n".join(read.log_at.isoformat().replace("+00:00", "Z")+
            " INFO:app.api.routes.item:"+read.log_message for read in self.projection.probe.reads)+"\n").encode()
        self.source.handle(self.request("begin"))
        self.source.handle(self.request("checkpoint", item_id=42, observations=observations))
        with patch.object(self.source, "_pin", return_value={"pod": "pod-1"}), patch.object(
            self.source, "_read", return_value=logs):
            self.assertEqual(self.source.handle(self.request("seal")), {"state": "sealed"})
        result = self.source.handle(self.request("finish", owned_data_removed=True,
            baseline_unchanged=True, lock_released=True))
        receipt = NormalCIReceipt.model_validate(result)
        self.assertEqual([read.cache_outcome for read in receipt.view.items[-1].payload.reads],
                         ["miss", "hit", "miss", "hit"])
        incident, run, _ = receipt.rules_ref.split("/")
        self.assertTrue((self.root/incident/run/"report.md").is_file())
        self.assertNotIn("raw_logs", result)

    def test_missing_or_changed_checkpoint_cannot_be_sealed(self):
        self.source.handle(self.request("begin"))
        with self.assertRaises(ValueError): self.source.handle(self.request("seal"))
        fake = FakeSequenceClient()
        observation = RequestObservation.model_validate(fake.app("cold", "3"*32))
        self.source.handle(self.request("checkpoint", item_id=42, observations=[observation]))
        with self.assertRaises(ValueError): self.source.handle(self.request("checkpoint", item_id=43, observations=[observation]))
        with self.assertRaises(ValueError): self.source.handle(self.request("checkpoint", item_id=42))

    def test_failed_log_read_does_not_publish_projection(self):
        fake = FakeSequenceClient()
        observations = [RequestObservation.model_validate(fake.app(action, "3"*32))
                        for action in ("cold", "hit", "cold", "hit")]
        self.source.handle(self.request("begin"))
        self.source.handle(self.request("checkpoint", item_id=42, observations=observations))
        with patch.object(self.source, "_pin", return_value={"pod": "pod-1"}), patch.object(
            self.source.transport, "get", return_value=ReadResponse(403, b"PRIVATE_SOURCE_SENTINEL")):
            with self.assertRaises(ValueError): self.source.handle(self.request("seal"))
        path = self.root/"normal-ci"/self.identity.session/"source.json"
        self.assertNotIn("projection", json.loads(path.read_bytes()))
        self.assertNotIn("PRIVATE_SOURCE_SENTINEL", path.read_text())


if __name__ == "__main__":
    unittest.main()
