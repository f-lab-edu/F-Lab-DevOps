"""실행기 검증 성공 직후 시각, 실제 규칙 고정, 과거 영수증 거부를 검증한다."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ai_ops.c_recovery import IMAGE_REPOSITORY
from ai_ops.c_verification_collect import CollectionPlan
from ai_ops.c_verification_handoff import C03Handoff, verify_c03_handoff
from ai_ops.c_verification_prepare import prepare_collection
from test_c_verification_collect import AT, APP_UID, DEP_UID, DIGEST, Reader, plan


def fixture():
    execution = plan().execution
    proposal = SimpleNamespace(repository="f-lab-edu/F-Lab-DevOps", target_uid=DEP_UID,
        desired_digest=DIGEST, proposal_id=execution.proposal_id, proposal_hash=execution.proposal_hash)
    link = SimpleNamespace(application_uid=APP_UID, link_hash="a"*64)
    result = {"sync_verified": True, "service_resolved": False, "merge_sha": execution.merge_sha,
        "desired_digest": DIGEST, "application_uid": APP_UID, "target_uid": DEP_UID, "lease_uid": "lease"}
    snapshot = {"application": {"uid": APP_UID}, "deployment": {"uid": DEP_UID}, "lease": {"uid": "lease"},
                "checked_at": AT.isoformat()}
    return dict(reader=object(), snapshot=snapshot, locked={}, proposal=proposal, pr_link=link,
                merge_response={"merged": True}), result


def handoff():
    args, result = fixture()
    verifier = Mock(return_value=result)
    with patch.dict("sys.modules", {"ai_ops.c_sync_guard": SimpleNamespace(verify_sync_result=verifier)}):
        return verify_c03_handoff(**args, clock=lambda: AT), verifier


class C03HandoffTests(unittest.TestCase):
    def test_real_verifier_entry_is_required_and_service_remains_unresolved(self):
        record, verifier = handoff()
        verifier.assert_called_once()
        self.assertEqual(record.created_at, AT)
        self.assertEqual(record.execution.verified_at, AT)
        self.assertFalse(record.execution.service_resolved)
        self.assertEqual(C03Handoff.model_validate_json(record.model_dump_json()), record)

    def test_c03_rejection_is_not_converted_to_a_receipt(self):
        args, _ = fixture()
        verifier = Mock(side_effect=ValueError("sync_result_lock_lost"))
        with patch.dict("sys.modules", {"ai_ops.c_sync_guard": SimpleNamespace(verify_sync_result=verifier)}):
            with self.assertRaisesRegex(ValueError, "lock_lost"):
                verify_c03_handoff(**args, clock=lambda: AT)

    def test_slow_validation_and_reversed_clock_are_rejected(self):
        for finish in (AT+timedelta(seconds=16), AT-timedelta(seconds=1)):
            args, result = fixture()
            times = iter([AT, finish])
            with patch.dict("sys.modules", {"ai_ops.c_sync_guard": SimpleNamespace(verify_sync_result=lambda **kw: result)}):
                with self.assertRaises(ValueError):
                    verify_c03_handoff(**args, clock=lambda: next(times))

    def test_wrong_target_and_service_claim_are_not_handed_off(self):
        for change in ({"target_uid": APP_UID}, {"service_resolved": True}, {"sync_verified": False}):
            args, result = fixture(); result.update(change)
            with patch.dict("sys.modules", {"ai_ops.c_sync_guard": SimpleNamespace(verify_sync_result=lambda **kw: result)}):
                with self.assertRaises(ValueError): verify_c03_handoff(**args, clock=lambda: AT)

    def test_old_json_and_accidental_receipt_changes_are_rejected(self):
        record, _ = handoff()
        with self.assertRaises(ValueError): C03Handoff.model_validate({"sync_verified": True})
        data = record.model_dump(mode="json")
        data["execution"]["merge_sha"] = "f"*40
        with self.assertRaises(ValueError): C03Handoff.model_validate(data)


class PreparationTests(unittest.TestCase):
    def reader(self):
        reader = Reader()
        reader.data["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] = IMAGE_REPOSITORY+"@"+DIGEST
        return reader

    def test_real_rule_without_new_handoff_never_creates_an_execution_receipt(self):
        reader = self.reader()
        result = prepare_collection(reader, clock=lambda: AT)
        self.assertEqual(result["status"], "waiting_for_c03_handoff")
        self.assertIsNone(result["collection_plan"])
        self.assertEqual(result["alert"]["query"], "synthetic_test_only")
        self.assertNotIn("execution", result)
        self.assertFalse(result["c03_execution_verified_here"])
        self.assertEqual(reader.forwards_started, reader.closed)

    def test_matching_current_handoff_builds_a_strict_pinned_plan(self):
        record, _ = handoff()
        reader = self.reader(); reader.at += timedelta(seconds=2)
        result = prepare_collection(reader, handoff=record, clock=lambda: reader.at)
        self.assertEqual(result["status"], "ready")
        value = CollectionPlan.model_validate(result["collection_plan"])
        self.assertEqual(value.execution, record.execution)
        self.assertEqual(value.alert.query, "synthetic_test_only")

    def test_changed_target_or_main_is_rejected_before_portforward(self):
        record, _ = handoff()
        for field in ("uid", "revision", "image"):
            reader = self.reader()
            if field == "uid": reader.data["deployment"]["metadata"]["uid"] = APP_UID
            elif field == "revision": reader.data["application"]["spec"]["source"]["targetRevision"] = "f"*40
            else: reader.data["deployment"]["spec"]["template"]["spec"]["containers"][0]["image"] = IMAGE_REPOSITORY+"@sha256:"+"f"*64
            result = prepare_collection(reader, handoff=record, clock=lambda: AT)
            self.assertEqual(result["status"], "blocked")
            self.assertIsNone(result["collection_plan"])
            self.assertEqual(reader.forwards_started, 0)

    def test_missing_rule_does_not_make_a_ready_plan(self):
        reader = self.reader(); reader.rule_missing = True
        result = prepare_collection(reader, clock=lambda: AT)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(reader.closed, reader.forwards_started)

    def test_post_execution_scrape_warmup_is_not_recovery_evidence(self):
        reader = self.reader(); record, _ = handoff()
        result = prepare_collection(reader, handoff=record, clock=lambda: reader.at,
            monotonic=lambda: reader.elapsed, sleep=reader.sleep)
        self.assertEqual(result["status"], "ready")
        self.assertGreater(result["source_warmup_seconds"], 0)
        self.assertNotIn("series", result)

    def test_sources_never_after_c03_stop_with_bounded_wait(self):
        reader = self.reader(); record, _ = handoff()
        result = prepare_collection(reader, handoff=record, clock=lambda: AT,
            monotonic=lambda: reader.elapsed, sleep=lambda seconds: setattr(reader, "elapsed", reader.elapsed+seconds))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reasons"], ["post_c03_sources_not_ready"])
        self.assertLessEqual(reader.elapsed, 90)


if __name__ == "__main__":
    unittest.main()
