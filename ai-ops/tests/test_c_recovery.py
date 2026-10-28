"""C-02 초안의 허용 대상·정확한 이미지 차이·비공개 사건 조회를 검증한다."""

from datetime import datetime, timezone
from copy import deepcopy
import json
import os
import hashlib
import socket
import subprocess
import threading
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_ops.c_recovery import (
    IMAGE_REPOSITORY, TARGET, IncidentProjection, RecoveryDraftStore, RecoveryProposal,
    create_request, draft_proposal, read_incident_projection,
)
from ai_ops.contracts import sha256_json
from ai_ops.c_local_auth import authenticated_user, peer_uid
from ai_ops.c_operator import OperatorServer, _client


ROOT = Path(__file__).resolve().parents[1]
CURRENT = json.loads((ROOT / "lab/values.yaml").read_text())["api"]["image"]["digest"]
DESIRED = "sha256:" + "1" * 64


def projection():
    return IncidentProjection(
        incident_id="inc-" + "a" * 32, run_id="run-" + "b" * 32,
        target=TARGET, bundle_hash="c" * 64, report_hash="d" * 64,
        audit_hash="e" * 64, report_status="partial",
        diagnosis_status="suspected_cause", current_state="unknown",
        evidence_counts={"event:available": 1}, cited_evidence_ids=["ev-allowed"],
    )


class CRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = create_request(projection(), 12345, now=datetime.now(timezone.utc))
        self.raw = (ROOT / "lab/values.yaml").read_bytes()

    def draft(self, **changes):
        values = dict(target_uid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", base_sha="f" * 40,
                      current_image=IMAGE_REPOSITORY + "@" + CURRENT,
                      desired_digest=DESIRED, values_bytes=self.raw)
        values.update(changes)
        return draft_proposal(self.request, **values)

    def test_only_digest_changes_and_draft_cannot_claim_approval(self):
        proposal = self.draft()
        self.assertEqual(proposal.state, "draft")
        self.assertEqual(proposal.diff.count("-      \"digest\""), 1)
        self.assertEqual(proposal.diff.count("+      \"digest\""), 1)
        self.assertEqual(proposal.values_path, "ai-ops/lab/values.yaml")
        self.assertNotIn("approval", proposal.model_dump())
        with self.assertRaises(ValueError):
            RecoveryProposal.model_validate({**proposal.model_dump(mode="json"), "state": "approved"})

    def test_mismatch_and_extra_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.draft(current_image=IMAGE_REPOSITORY + "@" + DESIRED)
        with self.assertRaises(ValueError):
            self.draft(desired_digest=CURRENT)
        changed=json.loads(self.raw)
        changed["api"]["image"]["tag"]="unapproved"
        with self.assertRaises(ValueError):
            self.draft(values_bytes=(json.dumps(changed, indent=2)+"\n").encode())
        with self.assertRaises(ValueError):
            self.draft(base_sha="main")
        forged=self.draft().model_dump(mode="json")
        forged["diff"]=forged["diff"].replace("ai-ops/lab/values.yaml", "url-shortener/values.prod.yaml")
        forged["diff_hash"]=hashlib.sha256(forged["diff"].encode()).hexdigest()
        forged["proposal_hash"]=sha256_json({k:v for k,v in forged.items() if k!="proposal_hash"})
        with self.assertRaises(ValueError):
            RecoveryProposal.model_validate(forged)
        forged=self.draft().model_dump(mode="json")
        forged["report_hash"]="a"*64
        forged["proposal_hash"]=sha256_json({k:v for k,v in forged.items() if k!="proposal_hash"})
        with self.assertRaisesRegex(ValueError, "요청과 변경안"):
            RecoveryDraftStore(self.root/"mismatch").save(self.request,RecoveryProposal.model_validate(forged))

    def test_draft_store_rejects_replay_with_different_content(self):
        store=RecoveryDraftStore(self.root / "drafts")
        proposal=self.draft()
        store.save(self.request, proposal)
        store.save(self.request, proposal)
        self.assertEqual(store.read(proposal.proposal_id), proposal)
        with self.assertRaises(ValueError):
            store.save(self.request, self.draft(desired_digest="sha256:"+"2"*64))
        self.assertEqual((self.root / "drafts/recovery-drafts.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_draft_store_rejects_proposal_id_collision(self):
        store = RecoveryDraftStore(self.root / "drafts")
        first = self.draft()
        store.save(self.request, first)
        second_request = create_request(projection(), 12345)
        forged = deepcopy(first.model_dump(mode="json"))
        forged["request_id"] = second_request.request_id
        forged["proposal_hash"] = sha256_json({key: value for key, value in forged.items()
                                                if key != "proposal_hash"})
        second = RecoveryProposal.model_validate(forged)
        with self.assertRaisesRegex(ValueError, "동일 변경안 ID"):
            store.save(second_request, second)
        with self.assertRaises(ValueError):
            store.read_request(second_request.request_id)

    def test_projection_excludes_body_and_wrong_target(self):
        data=self.root / "incident"
        data.mkdir(mode=0o700)
        fake=SimpleNamespace(
            request=SimpleNamespace(target=TARGET,source="manual_snapshot"),
            bundle=SimpleNamespace(bundle_hash="c"*64,evidence=[SimpleNamespace(kind="event",status="available")]),
            report=SimpleNamespace(target=TARGET,analysis_status="partial",diagnosis_status="suspected_cause",
                current_state="unknown",facts=[SimpleNamespace(evidence_ids=["ev-allowed"],statement="비공개 본문")],
                hypotheses=[],model_dump=lambda **_: {"status":"partial"}),
            collection_audit=SimpleNamespace(model_dump=lambda **_: {"entries": []}),
        )
        with patch("ai_ops.c_recovery.IncidentStore._read_record",return_value=fake):
            result=read_incident_projection(data,projection().incident_id,projection().run_id)
        self.assertEqual(result.cited_evidence_ids,["ev-allowed"])
        self.assertNotIn("비공개 본문",result.model_dump_json())
        self.assertEqual(result.audit_hash,sha256_json({"bundle_hash":"c"*64,"audit":{"entries":[]}}))
        fake.request.target=fake.request.target.model_copy(update={"environment":"production"})
        with patch("ai_ops.c_recovery.IncidentStore._read_record",return_value=fake),self.assertRaises(ValueError):
            read_incident_projection(data,projection().incident_id,projection().run_id)
        os.chmod(data,0o755)
        with self.assertRaises(ValueError):
            read_incident_projection(data,projection().incident_id,projection().run_id)

    def test_local_identity_requires_kernel_peer_and_live_github_id(self):
        left,right=socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        self.assertEqual(peer_uid(left),os.geteuid())
        def verified(*args,**kwargs):
            self.assertEqual(args[0],["gh","api","user","--jq",".id"])
            return subprocess.CompletedProcess(args[0],0,"12345\n","")
        self.assertEqual(authenticated_user(left,12345,runner=verified),12345)
        with self.assertRaises(ValueError):
            authenticated_user(left,54321,runner=verified)
        with self.assertRaises(ValueError):
            authenticated_user(left,12345,runner=lambda *a,**k: subprocess.CompletedProcess([],1,"", "token"))

    def test_local_socket_show_and_request_have_no_approval_or_execution(self):
        runner=lambda *a,**k: subprocess.CompletedProcess([],0,"12345\n","")
        with patch("ai_ops.c_operator.read_incident_projection",return_value=projection()):
            with OperatorServer(self.root/"state",self.root/"incident",12345,github_runner=runner) as server:
                def exchange(action):
                    thread=threading.Thread(target=server.handle_request)
                    thread.start()
                    try:
                        return _client(self.root/"state/operator.sock",action,
                                       projection().incident_id,projection().run_id)
                    finally:
                        thread.join(timeout=5)
                shown=exchange("show")
                self.assertEqual(shown["status"],"ok")
                self.assertNotIn("approval",shown)
                requested=exchange("request")
                self.assertEqual(requested["status"],"ok")
                self.assertEqual(server.drafts.read_request(requested["request"]["request_id"]).requester_github_id,12345)
                with server.drafts._connect() as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM proposals").fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
