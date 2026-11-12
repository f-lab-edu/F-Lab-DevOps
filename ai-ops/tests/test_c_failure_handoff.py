"""실패 잠금의 원자적 인계와 기한·대상·소유권 거부를 로컬에서 재현한다."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import unittest

from ai_ops.b6_coordination import LabLease
from ai_ops.c2_active_capture import ISOLATED_REPOSITORY, validate_active_failure
from ai_ops.c_failure_handoff import (HANDOFF_KEY, FailureHandoff, FailureHandoffReader, offer_failure_handoff,
                                      transfer_failure_handoff, validate_failure_lease)
from test_b6_gitops import LeaseServer
from test_c2_active_capture import BAD, NOW, REVISION, fixture


class FailureHandoffTest(unittest.TestCase):
    def setUp(self):
        self.server = LeaseServer()
        self.at = NOW-timedelta(seconds=60)
        self.owner = LabLease(self.server.command, "manual-"+"1"*32, clock=lambda: self.at)
        self.owner.acquire()
        self.at = NOW
        app, dep, rs, pods, _ = fixture()
        app["spec"]["source"]["repoURL"] = ISOLATED_REPOSITORY
        pods[0]["metadata"]["uid"] = "failed-pod"
        self.snapshot = {"application": app, "deployment": dep, "replica_set": rs, "pods": pods}
        self.args = dict(trial_id=123, executor_holder="c3-"+"2"*32,
            normal_revision="a"*40, normal_image=BAD.replace("0"*64, "1"*64),
            failure_revision=REVISION, failure_image=BAD, application_uid="app-uid",
            deployment_uid="dep", injected_at=NOW-timedelta(seconds=40),
            rollback_deadline=NOW+timedelta(seconds=260))

    def read(self):
        return {**deepcopy(self.snapshot), "lease": deepcopy(self.server.value),
                "checked_at": self.at.isoformat()}

    def offer(self, **changes):
        return offer_failure_handoff(self.owner, read_failure=self.read, **(self.args | changes))

    def validate(self, record):
        current = self.read()
        validate_active_failure(current["application"], current["deployment"],
            current["replica_set"], current["pods"], current["lease"],
            failure_revision=REVISION, failure_image=BAD, application_uid="app-uid",
            deployment_uid="dep", now=self.at, source_repository=ISOLATED_REPOSITORY,
            failure_handoff=record)

    def test_offer_keeps_injection_owner_and_c02_requires_matching_receipt(self):
        record = self.offer()
        self.assertEqual(FailureHandoff.model_validate_json(record.model_dump_json()), record)
        self.assertEqual(self.server.value["spec"]["holderIdentity"], self.owner.holder)
        self.assertEqual(self.server.value["metadata"]["annotations"][HANDOFF_KEY], record.digest())
        self.validate(record)
        with self.assertRaises(ValueError):
            self.validate(None)

    def test_transfer_has_no_empty_holder_and_does_not_claim_recovery(self):
        record = self.offer()
        original = self.server.command
        holders = []

        def command(*args, **kwargs):
            result = original(*args, **kwargs)
            holders.append(self.server.value["spec"]["holderIdentity"])
            return result

        self.owner.command = command
        recipient = transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.assertNotIn("", holders)
        self.assertIsNone(self.owner.uid)
        self.assertEqual(recipient.uid, record.lease_uid)
        self.assertEqual(recipient.holder, record.executor_holder)
        self.validate(record)
        with self.assertRaises(ValueError):
            recipient.release(recovery_verified=False)
        with self.assertRaises(ValueError):
            self.owner.release(recovery_verified=True)
        with self.assertRaises(ValueError):
            self.server.lease("3").acquire()

    def test_other_injection_owner_and_replayed_transfer_are_rejected(self):
        record = self.offer()
        other = LabLease(self.server.command, "manual-"+"3"*32, clock=lambda: NOW)
        other.uid = record.lease_uid
        with self.assertRaises(ValueError):
            transfer_failure_handoff(other, record, read_failure=self.read)
        transfer_failure_handoff(self.owner, record, read_failure=self.read)
        writes = self.server.writes
        with self.assertRaises(ValueError):
            transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.assertEqual(self.server.writes, writes)

    def test_invalid_schedule_normal_baseline_or_recipient_never_writes(self):
        for change in ({"rollback_deadline": NOW+timedelta(seconds=261)},
                       {"rollback_deadline": NOW}, {"injected_at": NOW+timedelta(seconds=1)},
                       {"normal_revision": REVISION}, {"normal_image": BAD},
                       {"executor_holder": "manual-"+"4"*32}, {"trial_id": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.offer(**change)
        self.assertEqual(self.server.writes, 1)

    def test_wrong_target_or_recovered_runtime_never_offers(self):
        for key, value in (("application_uid", "other"), ("deployment_uid", "other"),
                           ("failure_revision", "c"*40), ("failure_image", BAD.replace("0"*64, "2"*64))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.offer(**{key: value})
        self.snapshot["deployment"]["status"]["readyReplicas"] = 1
        with self.assertRaises(ValueError):
            self.offer()
        self.assertEqual(self.server.writes, 1)

    def test_preceding_failure_or_stale_snapshot_is_not_offered(self):
        self.snapshot["pods"][0]["metadata"]["creationTimestamp"] = (NOW-timedelta(minutes=3)).isoformat()
        with self.assertRaises(ValueError):
            self.offer()
        fresh = self.read()
        fresh["checked_at"] = (NOW-timedelta(seconds=16)).isoformat()
        with self.assertRaises(ValueError):
            offer_failure_handoff(self.owner, read_failure=lambda: fresh, **self.args)
        self.assertEqual(self.server.writes, 1)

    def test_exact_deadline_and_earlier_clock_reject_without_transfer(self):
        record = self.offer()
        for moment in (record.rollback_deadline, NOW-timedelta(seconds=1)):
            self.at = moment
            with self.subTest(moment=moment), self.assertRaises(ValueError):
                transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.assertEqual(self.server.writes, 2)
        self.assertEqual(self.server.value["spec"]["holderIdentity"], record.owner_holder)

    def test_record_edit_lease_recreation_or_other_owner_rejects(self):
        record = self.offer()
        original = deepcopy(self.server.value)
        for mutate in (
            lambda lease: lease["metadata"].update(uid="new"),
            lambda lease: lease["metadata"]["annotations"].update({HANDOFF_KEY: "f"*64}),
            lambda lease: lease["metadata"].update(namespace="default"),
            lambda lease: lease["metadata"].update(deletionTimestamp=NOW.isoformat()),
            lambda lease: lease["spec"].update(holderIdentity="manual-"+"3"*32),
            lambda lease: lease["spec"].update(acquireTime=(NOW-timedelta(seconds=1)).isoformat()),
            lambda lease: lease["spec"].update(leaseDurationSeconds=300),
        ):
            self.server.value = deepcopy(original)
            mutate(self.server.value)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.server.value = original
        edited = record.model_dump(mode="json")
        edited["trial_id"] += 1
        with self.assertRaises(ValueError):
            self.validate(FailureHandoff.model_validate(edited))
        self.assertEqual(self.server.writes, 2)

    def test_target_changed_after_offer_keeps_injection_lock(self):
        record = self.offer()
        self.snapshot["deployment"]["metadata"]["uid"] = "new-deployment"
        with self.assertRaises(ValueError):
            transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.assertEqual(self.server.value["spec"]["holderIdentity"], record.owner_holder)

    def test_pod_replacement_or_generation_change_invalidates_c02_and_transfer(self):
        record = self.offer()
        original = deepcopy(self.snapshot)
        for mutate in (
            lambda data: data["pods"][0]["metadata"].update(uid="replacement"),
            lambda data: data["deployment"]["metadata"].update(generation=3),
            lambda data: data["pods"][0]["metadata"].update(deletionTimestamp=NOW.isoformat()),
        ):
            self.snapshot = deepcopy(original)
            mutate(self.snapshot)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.validate(record)
            with self.assertRaises(ValueError):
                transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.assertEqual(self.server.value["spec"]["holderIdentity"], record.owner_holder)

    def test_cas_conflict_preserves_owner(self):
        record = self.offer()
        self.server.race = True
        with self.assertRaises(ValueError):
            transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.assertEqual(self.server.value["spec"]["holderIdentity"], record.owner_holder)
        self.assertEqual(self.owner.uid, record.lease_uid)

    def test_lost_transfer_response_keeps_actual_recipient_and_requires_inspection(self):
        record = self.offer()
        original = self.server.command

        def lose_response(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == "replace":
                raise OSError("response lost")
            return result

        self.owner.command = lose_response
        with self.assertRaises(OSError):
            transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.assertEqual(self.server.value["spec"]["holderIdentity"], record.executor_holder)
        with self.assertRaises(ValueError):
            self.owner.release(recovery_verified=True)
        with self.assertRaises(ValueError):
            transfer_failure_handoff(self.owner, record, read_failure=self.read)

    def test_runtime_change_after_transfer_retains_recipient_lock(self):
        record = self.offer()
        original = self.server.command

        def change_target(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0] == "replace":
                self.snapshot["deployment"]["metadata"]["uid"] = "replacement"
            return result

        self.owner.command = change_target
        with self.assertRaises(ValueError):
            transfer_failure_handoff(self.owner, record, read_failure=self.read)
        self.assertEqual(self.server.value["spec"]["holderIdentity"], record.executor_holder)
        self.assertIsNone(self.owner.uid)

    def test_verified_release_removes_offer_and_old_record_cannot_be_reused(self):
        record = self.offer()
        recipient = transfer_failure_handoff(self.owner, record, read_failure=self.read)
        recipient.release(recovery_verified=True)
        self.assertNotIn(HANDOFF_KEY, self.server.value["metadata"]["annotations"])
        with self.assertRaises(ValueError):
            self.validate(record)
        self.at += timedelta(seconds=306)
        fresh_owner = LabLease(self.server.command, "manual-"+"3"*32, clock=lambda: self.at)
        fresh_owner.acquire()
        self.assertNotIn(HANDOFF_KEY, self.server.value["metadata"]["annotations"])

    def test_c03_reader_cannot_use_donor_or_another_expected_lease(self):
        record = self.offer()
        reader = FailureHandoffReader(record, SimpleNamespace(read=lambda **kwargs: self.read()),
                                       self.read, clock=lambda: self.at)
        held = {"name": "b6-scenario-lock", "uid": record.lease_uid, "holder": record.executor_holder}
        with self.assertRaises(ValueError):
            reader.read(expected_lease=held)
        transfer_failure_handoff(self.owner, record, read_failure=self.read)
        with self.assertRaises(ValueError):
            reader.read(expected_lease={**held, "uid": "other"})
        self.assertEqual(reader.read(expected_lease=held)["lease"]["spec"]["holderIdentity"],
                         record.executor_holder)


if __name__ == "__main__":
    unittest.main()
