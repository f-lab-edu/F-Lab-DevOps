"""C-02 변경안 취소·만료·이력의 저장 및 로컬 인증 경계를 검증한다."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from ai_ops.c_operator import OperatorServer, _proposal_client
from ai_ops.c_recovery import IMAGE_REPOSITORY, RecoveryDraftStore, create_request, draft_proposal
from test_c_recovery import projection


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(timezone.utc)


class ProposalLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = RecoveryDraftStore(self.root / "state")
        self.request = create_request(projection(), 12345, now=NOW)
        values = (ROOT / "lab/values.yaml").read_bytes()
        current = json.loads(values)["api"]["image"]["digest"]
        self.proposal = draft_proposal(
            self.request, target_uid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            base_sha="f" * 40, current_image=IMAGE_REPOSITORY + "@" + current,
            desired_digest="sha256:" + "1" * 64, values_bytes=values, now=NOW)
        self.store.save(self.request, self.proposal)

    def test_requester_cancels_once_and_cannot_restore_draft(self):
        before = self.store.status(self.proposal.proposal_id, 12345, now=NOW)
        self.assertEqual((before["state"], before["version"]), ("draft", 1))
        with self.assertRaises(ValueError):
            self.store.cancel(self.proposal.proposal_id, 67890, now=NOW + timedelta(minutes=1))
        after = self.store.cancel(self.proposal.proposal_id, 12345, now=NOW + timedelta(minutes=1))
        self.assertEqual((after["state"], after["version"]), ("cancelled", 2))
        self.assertEqual(len(after["events"]), 2)
        again = self.store.cancel(self.proposal.proposal_id, 12345, now=NOW + timedelta(minutes=2))
        self.assertEqual(again["events"], after["events"])
        with self.assertRaises(ValueError):
            self.store.save(self.request, self.proposal)
        with self.assertRaises(ValueError):
            self.store.read(self.proposal.proposal_id)

    def test_deadline_records_expiry_and_cancel_cannot_override(self):
        expired = self.store.status(self.proposal.proposal_id, 12345, now=NOW + timedelta(hours=24))
        self.assertEqual((expired["state"], expired["version"]), ("expired", 2))
        self.assertEqual(expired["events"][1]["actor_github_id"], None)
        self.assertEqual(expired["events"][1]["at"], expired["expires_at"])
        again = self.store.cancel(self.proposal.proposal_id, 12345, now=NOW + timedelta(hours=25))
        self.assertEqual(again["events"], expired["events"])
        with self.assertRaises(ValueError):
            self.store.save(self.request, self.proposal)
        with self.assertRaises(ValueError):
            self.store.read(self.proposal.proposal_id)

    def test_old_draft_gets_initial_event_when_first_read(self):
        with self.store._connect() as db:
            db.execute("DELETE FROM proposal_states")
            db.execute("DELETE FROM proposal_events")
        state = self.store.status(self.proposal.proposal_id, 12345, now=NOW)
        self.assertEqual((state["state"], state["version"], len(state["events"])), ("draft", 1, 1))

    def test_inconsistent_state_and_audit_are_rejected(self):
        with self.store._connect() as db:
            db.execute("UPDATE proposal_states SET state='approved' WHERE proposal_id=?",
                       (self.proposal.proposal_id,))
        with self.assertRaises(ValueError):
            self.store.status(self.proposal.proposal_id, 12345, now=NOW)

    def test_authenticated_socket_exposes_only_own_status_and_cancel(self):
        runner = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "12345\n", "")
        with OperatorServer(self.root / "state", self.root / "incident", 12345,
                            github_runner=runner) as server:
            def exchange(action):
                worker = threading.Thread(target=server.handle_request)
                worker.start()
                try:
                    return _proposal_client(self.root / "state/operator.sock", action,
                                            self.proposal.proposal_id)
                finally:
                    worker.join(timeout=5)
            self.assertEqual(exchange("proposal-status")["proposal_status"]["state"], "draft")
            self.assertEqual(exchange("cancel-proposal")["proposal_status"]["state"], "cancelled")
            self.assertEqual(server.drafts.status(self.proposal.proposal_id, 12345)["version"], 2)


if __name__ == "__main__":
    unittest.main()
