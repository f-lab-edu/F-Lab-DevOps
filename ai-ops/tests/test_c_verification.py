"""복구 판정·보존 자료 연결·후속 범위·로컬 CLI의 경계를 검증한다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from ai_ops.c_verification import (
    FollowUpChange, FollowUpScope, VerificationInput, VerificationPolicy,
    plan_follow_up, verify_recovery,
)
from ai_ops.c_verification_archive import from_c03_archive
from ai_ops.contracts import sha256_json


ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 10, 14, 15, tzinfo=timezone.utc)
BINDING = {"cluster_id": "urlshortener", "namespace": "ai-ops-lab", "workload": "lab-api",
           "application_uid": "11111111-1111-4111-8111-111111111111",
           "target_uid": "22222222-2222-4222-8222-222222222222"}


def fixture():
    """실제 운영 회복 근거와 구분하는 합성 정상 관찰 창이다."""
    execution = {"binding": deepcopy(BINDING), "proposal_id": "cprop-"+"a"*32, "proposal_hash": "b"*64,
        "merge_sha": "c"*40, "desired_digest": "sha256:"+"d"*64, "sync_verified": True,
        "service_resolved": False, "verified_at": (AT-timedelta(seconds=1)).isoformat(),
        "source_hash": "e"*64}
    policy = {"window_start": AT.isoformat(), "window_end": (AT+timedelta(seconds=120)).isoformat(),
        "http_paths": ["/readyz", "/items"], "metrics": [{"name": "error_rate", "definition_hash": "f"*64,
            "comparison": "lte", "threshold": 0.05}],
        "alerts": [{"name": "WorkloadUnavailable", "definition_hash": "1"*64}]}
    runtime = {"kind": "runtime", "revision": execution["merge_sha"], "operation_revision": execution["merge_sha"],
        "argo_sync": "Synced", "argo_health": "Healthy", "operation_phase": "Succeeded",
        "generation": 3, "observed_generation": 3, "replicas": 1, "ready_replicas": 1,
        "pod_count": 1, "ready_pods": 1, "pod_images": [execution["desired_digest"]]}
    series = []
    for kind, name, reading, definition in [
        ("runtime", "lab-api", runtime, None),
        ("http", "/readyz", {"kind": "http", "status_code": 200, "response_contract_valid": True}, None),
        ("http", "/items", {"kind": "http", "status_code": 200, "response_contract_valid": True}, None),
        ("metric", "error_rate", {"kind": "metric", "value": 0.01}, "f"*64),
        ("alert", "WorkloadUnavailable", {"kind": "alert", "rule_present": True,
            "evaluation": "ok", "active_count": 0}, "1"*64),
    ]:
        points = []
        for seconds in range(0, 121, 30):
            at = AT+timedelta(seconds=seconds)
            value = deepcopy(reading)
            if kind == "alert":
                value["evaluated_at"] = at.isoformat()
            points.append({"observed_at": at.isoformat(), "reading": value})
        series.append({"kind": kind, "name": name, "binding": deepcopy(BINDING),
            "definition_hash": definition, "source_hash": sha256_json(points),
            "status": "available", "samples": points})
    return deepcopy({"schema_version": 1, "provenance": "synthetic", "execution": execution,
        "policy": policy, "evaluated_at": (AT+timedelta(seconds=121)).isoformat(), "series": series})


def result(data):
    return verify_recovery(VerificationInput.model_validate(data))


def scope_for(request):
    return FollowUpScope(proposal_id=request.execution.proposal_id,
        proposal_hash=request.execution.proposal_hash, execution_hash=verify_recovery(request)["execution_hash"],
        change=FollowUpChange(binding=request.execution.binding, revision=request.execution.merge_sha,
                              digest=request.execution.desired_digest), issued_at=AT,
        expires_at=AT+timedelta(minutes=10))


class RecoveryVerificationTests(unittest.TestCase):
    def test_complete_window_resolves_without_network_or_writes(self):
        with patch("socket.socket", side_effect=AssertionError("네트워크 호출 금지")):
            report = result(fixture())
        self.assertEqual(report["status"], "resolved")
        self.assertTrue(report["service_resolved"])
        self.assertFalse(report["execution_allowed"])
        self.assertEqual(report["external_model_calls"], 0)

    def test_argo_healthy_and_http_200_alone_are_insufficient(self):
        data = fixture(); data["series"] = data["series"][:3]
        report = result(data)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual([x["kind"] for x in report["checks"] if x["state"] == "unknown"], ["metric", "alert"])

    def test_runtime_mismatches_are_unresolved(self):
        for field, value in [("revision", "a"*40), ("operation_revision", "a"*40),
            ("observed_generation", 2), ("ready_replicas", 0), ("ready_pods", 0),
            ("pod_count", 2), ("pod_images", ["sha256:"+"a"*64]),
            ("argo_health", "Degraded"), ("operation_phase", "Failed")]:
            with self.subTest(field=field):
                data=fixture(); data["series"][0]["samples"][-1]["reading"][field]=value
                self.assertEqual(result(data)["status"], "unresolved")

    def test_http_error_and_bad_body_are_not_masked_by_successes(self):
        for field, value in [("status_code", 500), ("response_contract_valid", False)]:
            with self.subTest(field=field):
                data=fixture(); data["series"][2]["samples"][1]["reading"][field]=value
                report=result(data)
                self.assertEqual(report["status"], "unresolved")
                self.assertEqual(len(report["checks"][2]["failed_readings"]), 1)

    def test_known_failure_with_other_missing_data_is_unresolved(self):
        data=fixture(); data["series"][1]["samples"][0]["reading"]["status_code"]=503
        data["series"] = data["series"][:3]
        self.assertEqual(result(data)["status"], "unresolved")

    def test_metrics_missing_is_not_zero_and_threshold_is_enforced(self):
        for value, status in [(None, "inconclusive"), (0.0, "resolved"), (0.06, "unresolved")]:
            with self.subTest(value=value):
                data=fixture();data["series"][3]["samples"][-1]["reading"]["value"]=value
                self.assertEqual(result(data)["status"], status)
        data=fixture();data["policy"]["metrics"][0].update(comparison="gte", threshold=0.02)
        self.assertEqual(result(data)["status"], "unresolved")

    def test_nonfinite_numeric_and_boolean_readings_are_rejected(self):
        for value in [float("nan"),float("inf"),True,"0"]:
            with self.subTest(value=str(value)):
                data=fixture();data["series"][3]["samples"][0]["reading"]["value"]=value
                with self.assertRaises(ValidationError):VerificationInput.model_validate(data)

    def test_alert_missing_rule_bad_evaluation_or_stale_evaluation_is_unknown(self):
        for changes in [{"rule_present":False},{"evaluation":"error"},
            {"evaluation":"no_data"},{"active_count":None},{"evaluated_at":None},
            {"evaluated_at":(AT-timedelta(minutes=5)).isoformat()},
            {"evaluated_at":(AT+timedelta(minutes=5)).isoformat()}]:
            with self.subTest(changes=changes):
                data=fixture();data["series"][-1]["samples"][0]["reading"].update(changes)
                self.assertEqual(result(data)["status"], "inconclusive")

    def test_active_alert_is_unresolved(self):
        data=fixture();data["series"][-1]["samples"][-1]["reading"]["active_count"]=1
        self.assertEqual(result(data)["status"], "unresolved")

    def test_same_name_new_uid_and_query_or_rule_changes_do_not_pass(self):
        for index in [0,3,4]:
            with self.subTest(index=index):
                data=fixture()
                if index==0:data["series"][index]["binding"]["target_uid"]="33333333-3333-4333-8333-333333333333"
                else:data["series"][index]["definition_hash"]="a"*64
                self.assertEqual(result(data)["status"],"inconclusive")

    def test_missing_collection_failure_or_sparse_series_are_unknown(self):
        for state in ["missing","collection_failed","available"]:
            with self.subTest(state=state):
                data=fixture();data["series"][0].update(status=state,samples=[])
                self.assertEqual(result(data)["status"],"inconclusive")
        data=fixture();data["series"][0]["samples"]=data["series"][0]["samples"][:1]
        self.assertEqual(result(data)["status"],"inconclusive")

    def test_absent_timestamps_future_window_and_old_result_do_not_pass(self):
        for mutation in ["execution_time","sample_time","future_window","old_result"]:
            with self.subTest(mutation=mutation):
                data=fixture()
                if mutation=="execution_time":data["execution"]["verified_at"]=None
                elif mutation=="sample_time":data["series"][0]["samples"][0]["observed_at"]=None
                elif mutation=="future_window":data["evaluated_at"]=AT.isoformat()
                else:data["evaluated_at"]=(AT+timedelta(hours=1)).isoformat()
                self.assertEqual(result(data)["status"],"inconclusive")

    def test_duplicates_gaps_out_of_window_and_wrong_order_do_not_pass(self):
        for change in ["duplicate","gap","outside","order"]:
            with self.subTest(change=change):
                data=fixture(); points=data["series"][0]["samples"]
                if change=="duplicate":points.insert(1,deepcopy(points[0]))
                elif change=="gap":del points[1:3]
                elif change=="outside":points[0]["observed_at"]=(AT-timedelta(seconds=1)).isoformat()
                else:points.reverse()
                self.assertEqual(result(data)["status"],"inconclusive")

    def test_duplicate_series_and_invalid_window_policy_are_rejected(self):
        data=fixture();data["series"].append(deepcopy(data["series"][0]))
        with self.assertRaises(ValidationError):VerificationInput.model_validate(data)
        data=fixture();data["policy"]["window_end"]=AT.isoformat()
        with self.assertRaises(ValidationError):VerificationInput.model_validate(data)

    def test_input_hash_covers_values_policy_and_identity(self):
        first=result(fixture())["input_hash"]
        for key in ["value","policy","identity"]:
            data=fixture()
            if key=="value":data["series"][3]["samples"][0]["reading"]["value"]=0.02
            elif key=="policy":data["policy"]["metrics"][0]["threshold"]=0.04
            else:data["execution"]["proposal_id"]="cprop-"+"b"*32
            self.assertNotEqual(first,result(data)["input_hash"])


class FailureHandlingTests(unittest.TestCase):
    def setUp(self):
        data=fixture();data["series"][2]["samples"][-1]["reading"]["status_code"]=500
        self.request=VerificationInput.model_validate(data)
        self.scope=scope_for(self.request)
        self.now=AT+timedelta(seconds=122)

    def plan(self, scope=None, change=None, now=None):
        return plan_follow_up(self.request,scope=scope,change=change,now=now or self.now)

    def test_default_failure_stops_without_follow_up(self):
        self.assertEqual(self.plan()["decision"],"stop_and_report")

    def test_same_exact_change_is_only_a_candidate_without_execution(self):
        outcome=self.plan(self.scope,self.scope.change)
        self.assertEqual(outcome["decision"],"same_change_candidate")
        self.assertTrue(outcome["requires_execution_revalidation"])
        self.assertFalse(outcome["execution_allowed"])

    def test_other_revision_digest_uid_or_proposal_requires_new_approval(self):
        for kind in ["revision","digest","uid","proposal","source"]:
            with self.subTest(kind=kind):
                scope=self.scope.model_copy(deep=True);change=scope.change.model_copy(deep=True)
                if kind=="revision":change.revision="a"*40
                elif kind=="digest":change.digest="sha256:"+"a"*64
                elif kind=="uid":change.binding.target_uid="33333333-3333-4333-8333-333333333333"
                elif kind=="proposal":scope.proposal_id="cprop-"+"b"*32
                else:scope.execution_hash="a"*64
                outcome=self.plan(scope,change)
                self.assertEqual(outcome["decision"],"request_new_approval")
                self.assertFalse(outcome["execution_allowed"])

    def test_missing_expired_revoked_or_unissued_scope_never_enables_execution(self):
        scenarios=[None,self.scope.model_copy(update={"revoked":True}),
            self.scope.model_copy(update={"expires_at":self.now}),
            self.scope.model_copy(update={"issued_at":self.now+timedelta(seconds=1)})]
        for scope in scenarios:
            outcome=self.plan(scope,self.scope.change)
            self.assertEqual(outcome["decision"],"request_new_approval")
            self.assertFalse(outcome["execution_allowed"])

    def test_retry_limit_and_stale_result_stop(self):
        scope=self.scope.model_copy(update={"attempts_used":1})
        self.assertEqual(self.plan(scope,scope.change)["reason"],"follow_up_attempt_limit_reached")
        self.assertEqual(self.plan(self.scope,self.scope.change,AT+timedelta(minutes=6))["reason"],
                         "verification_result_stale_or_in_future")

    def test_missing_evidence_does_not_become_follow_up_permission(self):
        data=self.request.model_dump(mode="json");data["series"]=data["series"][:-1]
        request=VerificationInput.model_validate(data);scope=scope_for(request)
        outcome=plan_follow_up(request,scope=scope,change=scope.change,now=self.now)
        self.assertEqual(outcome["decision"],"stop_and_report")

    def test_one_failed_sample_does_not_complete_follow_up_evidence(self):
        data=self.request.model_dump(mode="json")
        data["series"][2]["samples"]=data["series"][2]["samples"][-1:]
        request=VerificationInput.model_validate(data);scope=scope_for(request)
        outcome=plan_follow_up(request,scope=scope,change=scope.change,now=self.now)
        self.assertEqual(outcome["verification_status"],"unresolved")
        self.assertEqual(outcome["decision"],"stop_and_report")

    def test_resolved_service_does_not_need_follow_up(self):
        request=VerificationInput.model_validate(fixture())
        outcome=plan_follow_up(request,scope=None,change=None,now=self.now)
        self.assertEqual(outcome["decision"],"report_result")
        self.assertFalse(outcome["execution_allowed"])


class ArchiveAndCLITests(unittest.TestCase):
    def archived(self):
        data=fixture();e=data["execution"]
        proposal={"target":{"cluster_id":"urlshortener","namespace":"ai-ops-lab","workload":"lab-api", "service":"lab-api","environment":"practice"},
            "target_uid":BINDING["target_uid"],"proposal_id":e["proposal_id"],
            "proposal_hash":e["proposal_hash"],"desired_digest":e["desired_digest"]}
        proposal["proposal_hash"]=sha256_json({k:v for k,v in proposal.items() if k!="proposal_hash"})
        http={"/readyz":200,"/items":200}
        receipt={**e,"target_uid":BINDING["target_uid"],"application_uid":BINDING["application_uid"],"http_checks":http}
        runtime={"sha":e["merge_sha"],"image":"764428911194.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener@"+e["desired_digest"],"ready":1}
        return dict(proposal=proposal,receipt=receipt,http=http,runtime=runtime,
                    policy=VerificationPolicy.model_validate(data["policy"]),evaluated_at=AT+timedelta(days=1))

    def test_archive_does_not_invent_times_metrics_or_recovery(self):
        request=from_c03_archive(**self.archived());report=verify_recovery(request)
        self.assertEqual(report["status"],"inconclusive")
        self.assertEqual(report["provenance"],"archived")
        self.assertIsNone(request.execution.verified_at)
        self.assertEqual({x.kind for x in request.series},{"runtime","http"})

    def test_mixed_archive_records_are_rejected(self):
        data=self.archived();data["runtime"]["sha"]="a"*40
        with self.assertRaises(ValueError):from_c03_archive(**data)

    def test_modified_archived_proposal_hash_is_rejected(self):
        data=self.archived();data["proposal"]["desired_digest"]="sha256:"+"a"*64
        with self.assertRaises(ValueError):from_c03_archive(**data)

    def test_cli_writes_private_report_with_kst_and_refuses_overwrite(self):
        spec=importlib.util.spec_from_file_location("verify_c4_recovery",ROOT/"scripts/verify_c4_recovery.py")
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{"AI_OPS_FILES_ROOT":folder}):
            root=Path(folder);source=root/"input.json";output=root/"report.json"
            source.write_text(json.dumps(fixture()))
            with patch("builtins.print"):
                self.assertEqual(cli.main(["--input",str(source),"--output",str(output)]),0)
            saved=json.loads(output.read_bytes())
            self.assertIn("KST",saved["report_markdown"])
            self.assertEqual(saved["verification"]["status"],"resolved")
            self.assertEqual(output.stat().st_mode & 0o777,0o600)
            with self.assertRaises(SystemExit):
                cli.main(["--input",str(source),"--output",str(output)])


if __name__ == "__main__":
    unittest.main()
