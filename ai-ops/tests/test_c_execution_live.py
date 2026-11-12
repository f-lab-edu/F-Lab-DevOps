"""C-03 원본 조회가 잠금 경계마다 새 장애 근거를 요구하는지 확인한다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from ai_ops.c_approval import ApprovalRejected
from ai_ops.c_execution_live import C3LiveReader
from ai_ops.c_recovery_proof import PROPOSAL_IDENTITY_FIELDS


NOW = datetime(2026, 11, 10, 6, 30, tzinfo=timezone.utc)


class ExecutionLiveReaderTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.change_at = None
        self.identity = {key: "fixed" for key in PROPOSAL_IDENTITY_FIELDS}

    def collect(self, expected_lease):
        number = len(self.calls) + 1
        self.calls.append(expected_lease)
        identity = deepcopy(self.identity)
        if number == self.change_at:
            identity["target_uid"] = "changed"
        checked = (NOW - timedelta(seconds=12-number)).isoformat()
        result = {**identity, "proof_checked_at": checked,
                  "incident_ready": True, "proposal_allowed": False,
                  "cluster_or_github_write_performed": False,
                  "blockers": ["fresh_recheck_and_proposal_contract_required"]}
        snapshot = {"application": {"id": number}, "deployment": {"id": number},
                    "lease": {"spec": {"acquireTime": (NOW - timedelta(seconds=9)).isoformat()}},
                    "values_bytes": b"values"}
        return result, snapshot

    def test_each_read_uses_new_source_and_lock_starts_new_pair(self):
        reader = C3LiveReader(self.collect, clock=lambda: NOW)
        first = reader.read()
        self.assertTrue(first["proof"]["proposal_ready"])
        self.assertEqual(len(self.calls), 2)
        second = reader.read()
        self.assertEqual(len(self.calls), 3)
        self.assertNotEqual(first["checked_at"], second["checked_at"])
        held = {"holder": "c3-" + "a" * 32, "uid": "lease"}
        locked = reader.read(expected_lease=held)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.calls[-2:], [held, held])
        self.assertTrue(locked["proof"]["proposal_ready"])

    def test_changed_identity_or_old_lock_proof_is_rejected(self):
        reader = C3LiveReader(self.collect, clock=lambda: NOW)
        self.change_at = 2
        with self.assertRaisesRegex(ApprovalRejected, "fresh_live_proof_unavailable"):
            reader.read()
        self.assertEqual(len(self.calls), 2)
        self.change_at = None
        self.calls.clear()
        held = {"holder": "c3-" + "b" * 32, "uid": "lease"}
        reader = C3LiveReader(self.collect, clock=lambda: NOW)
        with self.assertRaisesRegex(ApprovalRejected, "fresh_live_proof_unavailable"):
            reader.read(expected_lease=held)


if __name__ == "__main__":
    unittest.main()
