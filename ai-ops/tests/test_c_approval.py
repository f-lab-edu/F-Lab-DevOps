"""복구 리뷰가 현재 PR·초안·정확한 파일 변경에 묶이는지 확인한다."""

import base64
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_ops.c_approval import ApprovalGitHubReader, ApprovalRejected, REPO_API, prepare_pr_link, verify_review
from ai_ops.c_pr_link import RecoveryPRLink, read_private_c_record
from ai_ops.c_recovery_proof import PROPOSAL_IDENTITY_FIELDS
from ai_ops.c_recovery import (
    IMAGE_REPOSITORY, REPOSITORY, TARGET, RecoveryDraftStore, RecoveryProposal,
    RecoveryRequest, _encoded_values, draft_proposal,
)
from ai_ops.contracts import sha256_json
from ai_ops.path_policy import write_private_json


BASE, HEAD = "a" * 40, "b" * 40
CURRENT, DESIRED = "sha256:" + "c" * 64, "sha256:" + "d" * 64
OPERATOR, AUTHOR = 21, 22
NOW = datetime(2026, 11, 9, 6, tzinfo=timezone.utc)
PULL = f"{REPO_API}/pulls/123"
REVIEWS = PULL + "/reviews?per_page=100"
MAIN = f"{REPO_API}/git/ref/heads/main"


def tree_key(char):
    return f"{REPO_API}/git/trees/{char * 40}"


def blob_key(char):
    return f"{REPO_API}/git/blobs/{char * 40}"


def entry(name, char, *, directory=True):
    kind, mode = ("tree", "040000") if directory else ("blob", "100644")
    return {"path": name, "type": kind, "mode": mode, "sha": char * 40}


def blob(char, content):
    return {"sha": char * 40, "encoding": "base64", "size": len(content),
            "content": base64.b64encode(content).decode()}


class FixtureReader:
    def __init__(self, proposal):
        self.hooks = {}
        self.calls = []
        self.data = {
            "user": {"id": OPERATOR, "type": "User"},
            PULL: {
                "number": 123, "state": "open", "draft": False, "merged": False,
                "created_at": (NOW - timedelta(minutes=3)).isoformat(),
                "user": {"id": AUTHOR, "type": "Bot"},
                "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPOSITORY}},
                "head": {"ref": "recovery/proposal", "sha": HEAD, "repo": {"full_name": REPOSITORY}},
            },
            REVIEWS: [{
                "id": 9, "user": {"id": OPERATOR, "type": "User"},
                "state": "APPROVED", "commit_id": HEAD,
                "submitted_at": (NOW - timedelta(minutes=2)).isoformat(),
            }],
            MAIN: {"ref": "refs/heads/main", "object": {"type": "commit", "sha": BASE}},
            f"{REPO_API}/git/commits/{BASE}": {"sha": BASE, "tree": {"sha": "1" * 40}},
            f"{REPO_API}/git/commits/{HEAD}": {
                "sha": HEAD, "parents": [{"sha": BASE}], "tree": {"sha": "2" * 40},
                "message": f"실습 이미지 원복\n\nRecovery-Proposal-SHA256: {proposal.proposal_hash}",
            },
            blob_key("7"): blob("7", _encoded_values(CURRENT).encode()),
            blob_key("8"): blob("8", _encoded_values(DESIRED).encode()),
        }
        for char, next_char, name in (("1", "3", "ai-ops"), ("2", "4", "ai-ops"),
                                      ("3", "5", "lab"), ("4", "6", "lab"),
                                      ("5", "7", "values.yaml"), ("6", "8", "values.yaml")):
            self.data[tree_key(char)] = {
                "sha": char * 40, "truncated": False,
                "tree": [entry(name, next_char, directory=name != "values.yaml"), entry("other", "0")],
            }

    def get(self, endpoint):
        self.calls.append(endpoint)
        if endpoint in self.hooks:
            self.hooks[endpoint]()
        return deepcopy(self.data[endpoint])


class LocalGitReader:
    """로컬 Git 객체를 동일한 API 응답 형식으로 내보낸다."""

    def __init__(self, root):
        self.root = root
        self.responses = {}

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True).stdout

    def commit(self, message):
        self.git("add", "-A")
        self.git("-c", "user.name=Local Test", "-c", "user.email=test@example.invalid",
                 "commit", "--no-gpg-sign", "-m", message)
        return self.git("rev-parse", "HEAD").decode().strip()

    def get(self, endpoint):
        if endpoint in self.responses:
            return deepcopy(self.responses[endpoint])
        if endpoint == MAIN:
            return {"ref": "refs/heads/main", "object": {
                "type": "commit", "sha": self.git("rev-parse", "refs/heads/main").decode().strip()}}
        _, kind, sha = endpoint.rsplit("/", 2)
        if kind == "commits":
            tree, parents, message = self.git("show", "-s", "--format=%T%n%P%n%B", sha).decode().split("\n", 2)
            return {"sha": sha, "tree": {"sha": tree}, "parents": [{"sha": p} for p in parents.split()],
                    "message": message}
        if kind == "trees":
            entries = []
            for row in self.git("ls-tree", "-z", sha).decode().strip("\0").split("\0"):
                meta, name = row.split("\t")
                mode, kind, oid = meta.split()
                entries.append({"path": name, "type": kind, "mode": mode, "sha": oid})
            return {"sha": sha, "truncated": False, "tree": entries}
        if kind == "blobs":
            data = self.git("cat-file", "blob", sha)
            return {"sha": sha, "encoding": "base64", "size": len(data),
                    "content": base64.b64encode(data).decode()}
        raise AssertionError(endpoint)


class CApprovalTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = RecoveryDraftStore(Path(temp.name) / "drafts")
        self.request = RecoveryRequest(
            request_id="creq-" + "0" * 32, requester_github_id=OPERATOR, incident_id="inc-" + "1" * 32,
            run_id="run-" + "2" * 32, bundle_hash="3" * 64, report_hash="4" * 64,
            failure_binding_hash="5" * 64, target=TARGET, requested_at=NOW - timedelta(minutes=41),
        )
        self.offline = draft_proposal(
            self.request, target_uid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", base_sha=BASE,
            current_image=f"{IMAGE_REPOSITORY}@{CURRENT}", desired_digest=DESIRED,
            values_bytes=_encoded_values(CURRENT).encode(), now=NOW - timedelta(minutes=40),
        )
        body = self.offline.model_dump(mode="json", exclude={"proposal_hash"})
        body.update(schema_version=2, failure_binding_hash=self.request.failure_binding_hash,
                    recheck_hash="6" * 64, source_evidence_ids=["ev-a", "ev-b", "ev-c"],
                    impact_scope="ai-ops-lab/lab-api image only",
                    verification_checks=["argo_synced_healthy", "pod_image_matches_desired_digest",
                                         "pod_ready", "readyz_success", "items_flow_success"],
                    failure_action="stop_and_report_without_unapproved_rollback")
        self.proposal = RecoveryProposal.model_validate({**body, "proposal_hash": sha256_json(body)})
        self.store.save(self.request, self.proposal)
        self.reader = FixtureReader(self.proposal)

    def verify(self, **options):
        params = dict(drafts=self.store, proposal_id=self.proposal.proposal_id, pull_number=123,
                      allowed_github_id=OPERATOR, pr_author_id=AUTHOR, reader=self.reader, clock=lambda: NOW)
        return verify_review(**(params | options))

    def setup_link(self, main_digest):
        git_root = self.store.root.parent / "git"
        git_root.mkdir()
        reader = LocalGitReader(git_root)
        reader.git("init", "-b", "main")
        lab = git_root / "ai-ops/lab"
        shutil.copytree(Path(__file__).resolve().parents[1] / "lab", lab)
        values = lab / "values.yaml"
        values.write_text(_encoded_values(CURRENT))
        observed = reader.commit("장애 관측")
        values.write_text(_encoded_values(main_digest))
        (git_root / "README.md").write_text("실습 경로 밖 변경\n")
        main = reader.commit("현재 main")
        prior = self.link_proof(observed, self.proposal.created_at - timedelta(seconds=1))
        refreshed = self.link_proof(observed, NOW - timedelta(seconds=1))
        body = self.proposal.model_dump(mode="json", exclude={"proposal_hash"})
        body.update(base_sha=observed, recheck_hash=prior["proposal_readiness_hash"])
        proposal = RecoveryProposal.model_validate({**body, "proposal_hash": sha256_json(body)})
        return reader, values, proposal, prior, refreshed, main

    def link_proof(self, observed, checked_at):
        identity = {key: "synthetic" for key in PROPOSAL_IDENTITY_FIELDS}
        identity.update(
            base_sha=observed, target_uid=self.proposal.target_uid,
            application_uid="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", current_digest=CURRENT,
            prior_digest=DESIRED, incident_id=self.proposal.incident_id, run_id=self.proposal.run_id,
            request_id=self.proposal.request_id, bundle_hash=self.proposal.bundle_hash,
            report_hash=self.proposal.report_hash, failure_binding_hash=self.proposal.failure_binding_hash,
            proof_checked_at=checked_at.isoformat(),
        )
        return {**identity, "proposal_ready": True, "proposal_allowed": False,
                "cluster_or_github_write_performed": False, "proposal_readiness_hash": sha256_json(identity),
                "blockers": ["draft_only_c3_approval_required"]}

    @unittest.skipUnless(shutil.which("helm"), "실제 차트 렌더에는 Helm이 필요합니다")
    def test_distinct_observed_and_main_shas_link_to_exact_reviewed_diff(self):
        reader, values, proposal, prior, refreshed, main = self.setup_link(CURRENT)
        result, link = prepare_pr_link(proposal, prior, refreshed, reader=reader, clock=lambda: NOW)
        self.assertEqual(result["status"], "pr_candidate")
        self.assertNotEqual(link.observed_revision_sha, link.pr_base_sha)
        self.assertEqual(link.pr_base_sha, main)
        link_path = self.store.root / "pr-link.json"
        with patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(self.store.root)}):
            write_private_json(link_path, link.model_dump(mode="json"))
        link = RecoveryPRLink.model_validate_json(read_private_c_record(link_path))

        reader.git("checkout", "-b", "recovery/proposal")
        values.write_text(_encoded_values(DESIRED))
        head = reader.commit(f"실습 이미지 원복\n\nRecovery-Proposal-SHA256: {link.link_hash}")
        responses = FixtureReader(proposal).data
        pr, reviews = responses[PULL], responses[REVIEWS]
        pr["base"]["sha"], pr["head"]["sha"] = main, head
        pr["created_at"] = (NOW + timedelta(seconds=1)).isoformat()
        reviews[0].update(commit_id=head, submitted_at=(NOW + timedelta(seconds=2)).isoformat())
        reader.responses = {"user": responses["user"], PULL: pr, REVIEWS: reviews}
        store = RecoveryDraftStore(self.store.root / "linked")
        store.save(self.request, proposal)
        checked = self.verify(drafts=store, proposal_id=proposal.proposal_id, reader=reader,
                              pr_link=link, clock=lambda: NOW + timedelta(seconds=3))
        self.assertEqual(checked["pr_link_hash"], link.link_hash)
        self.assertEqual(checked["observed_revision_sha"], proposal.base_sha)
        self.assertEqual(checked["base_sha"], main)
        self.assertFalse(checked["execution_allowed"])

    def test_main_with_desired_image_produces_no_link_or_change(self):
        reader, _, proposal, prior, refreshed, main = self.setup_link(DESIRED)
        result, link = prepare_pr_link(proposal, prior, refreshed, reader=reader, clock=lambda: NOW)
        self.assertEqual(result["status"], "main_already_at_desired_digest")
        self.assertEqual(result["runtime_status"], "observed_failure")
        self.assertEqual(result["pr_base_sha"], main)
        self.assertFalse(result["pr_created"])
        self.assertIsNone(link)
        self.assertEqual(reader.git("rev-parse", "HEAD").decode().strip(), main)

    def test_same_digest_with_different_chart_and_changed_proof_are_denied(self):
        reader, values, proposal, prior, refreshed, _ = self.setup_link(CURRENT)
        chart = values.parent / "Chart.yaml"
        chart.write_text(chart.read_text().replace("version: 0.1.0", "version: 0.1.1"))
        reader.commit("실습 차트 변경")
        result, link = prepare_pr_link(proposal, prior, refreshed, reader=reader, clock=lambda: NOW)
        self.assertEqual(result["status"], "lab_source_diverged")
        self.assertIsNone(link)
        with self.assertRaisesRegex(ValueError, "proposal_recheck_hash_mismatch"):
            prepare_pr_link(proposal, prior, dict(refreshed, application_uid=proposal.target_uid),
                            reader=reader, clock=lambda: NOW)

    def test_github_reader_uses_fixed_get_route_and_rejects_other_paths(self):
        executable = self.store.root / "gh"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "assert sys.argv[1:6] == ['api', '--hostname', 'github.com', '--method', 'GET']\n"
            "print(json.dumps({'endpoint': sys.argv[6]}))\n"
        )
        executable.chmod(0o700)
        with patch.dict(os.environ, {"PATH": str(self.store.root)}):
            reader = ApprovalGitHubReader()
            self.assertEqual(reader.get(REVIEWS), {"endpoint": REVIEWS})
            with self.assertRaisesRegex(ApprovalRejected, "github_read_path_rejected"):
                reader.get(f"{PULL}/merge")

    def test_exact_review_returns_evidence_without_approving_or_executing(self):
        result = self.verify()
        self.assertTrue(result["review_verified"])
        self.assertFalse(result["execution_allowed"])
        self.assertEqual((result["review_id"], result["head_sha"], result["base_sha"]), (9, HEAD, BASE))
        self.assertEqual(result["diff_hash"], self.proposal.diff_hash)
        self.assertEqual(result["expires_at"], NOW + timedelta(minutes=28))
        self.assertEqual(self.store.status(self.proposal.proposal_id, OPERATOR, now=NOW)["state"], "draft")

    def test_non_approval_or_unapproved_identity(self):
        approved = deepcopy(self.reader.data[REVIEWS][0])
        for state in ("COMMENTED", "PENDING", "DISMISSED", "CHANGES_REQUESTED"):
            with self.subTest(state=state):
                self.reader.data[REVIEWS] = [dict(approved, state=state)]
                with self.assertRaises(ApprovalRejected):
                    self.verify()
        self.reader.data[REVIEWS] = [dict(approved, user={"id": 33, "type": "User"})]
        with self.assertRaisesRegex(ApprovalRejected, "eligible_approval_missing"):
            self.verify()
        with self.assertRaisesRegex(ApprovalRejected, "reviewer_is_pull_request_author"):
            self.verify(pr_author_id=OPERATOR)

    def test_dismissal_blocks_earlier_approval_but_comment_does_not(self):
        later = dict(self.reader.data[REVIEWS][0], id=10, state="COMMENTED", submitted_at=NOW.isoformat())
        self.reader.data[REVIEWS].append(later)
        self.assertTrue(self.verify()["review_verified"])
        later["state"] = "DISMISSED"
        with self.assertRaisesRegex(ApprovalRejected, "eligible_approval_missing"):
            self.verify()

    def test_approval_must_match_head_and_is_invalid_at_30_minutes(self):
        review = self.reader.data[REVIEWS][0]
        review["commit_id"] = BASE
        with self.assertRaisesRegex(ApprovalRejected, "review_commit_mismatch"):
            self.verify()
        review["commit_id"] = HEAD
        with self.assertRaisesRegex(ApprovalRejected, "approval_outside_validity_window"):
            self.verify(clock=lambda: NOW + timedelta(minutes=28))

    def test_main_advance_or_head_race_is_rejected(self):
        self.reader.data[MAIN]["object"]["sha"] = "f" * 40
        with self.assertRaisesRegex(ApprovalRejected, "base_sha_changed"):
            self.verify()
        self.reader.data[MAIN]["object"]["sha"] = BASE

        def change_head():
            if self.reader.calls.count(PULL) == 2:
                self.reader.data[PULL]["head"]["sha"] = "f" * 40

        self.reader = FixtureReader(self.proposal)
        self.reader.hooks[PULL] = change_head
        with self.assertRaisesRegex(ApprovalRejected, "pull_request_changed_during_verification"):
            self.verify()

    def test_review_revoked_during_check(self):
        def dismiss():
            if self.reader.calls.count(REVIEWS) == 2:
                self.reader.data[REVIEWS][0]["state"] = "DISMISSED"

        self.reader.hooks[REVIEWS] = dismiss
        with self.assertRaisesRegex(ApprovalRejected, "eligible_approval_missing"):
            self.verify()

    def test_extra_changes_mode_changes_and_truncated_tree_are_denied(self):
        for tree, index, updates, reason in (
            ("2", 1, {"sha": "e" * 40}, "unexpected_git_change"),
            ("6", 0, {"mode": "120000"}, "unexpected_git_change"),
            ("6", 0, {"mode": "100755"}, "unexpected_git_change"),
        ):
            with self.subTest(updates=updates):
                self.reader = FixtureReader(self.proposal)
                self.reader.data[tree_key(tree)]["tree"][index].update(updates)
                with self.assertRaisesRegex(ApprovalRejected, reason):
                    self.verify()
        self.reader = FixtureReader(self.proposal)
        self.reader.data[tree_key("6")]["truncated"] = True
        with self.assertRaisesRegex(ApprovalRejected, "git_tree_incomplete"):
            self.verify()

    def test_diff_and_proposal_binding_both_match(self):
        self.reader.data[blob_key("8")] = blob("8", _encoded_values(CURRENT).encode())
        with self.assertRaisesRegex(ApprovalRejected, "proposal_diff_mismatch"):
            self.verify()
        self.reader = FixtureReader(self.proposal)
        self.reader.data[f"{REPO_API}/git/commits/{HEAD}"]["message"] = "해시 없는 커밋"
        with self.assertRaisesRegex(ApprovalRejected, "proposal_binding_mismatch"):
            self.verify()

    def test_unreadable_or_full_review_page_fails_closed(self):
        self.reader.data[REVIEWS] *= 100
        with self.assertRaisesRegex(ApprovalRejected, "review_list_incomplete"):
            self.verify()
        del self.reader.data[REVIEWS]
        with self.assertRaisesRegex(ApprovalRejected, "approval_input_unavailable_or_invalid"):
            self.verify()

    def test_cancelled_expired_offline_and_foreign_proposals_are_rejected(self):
        with self.assertRaisesRegex(ApprovalRejected, "proposal_not_active_or_not_owned"):
            self.verify(clock=lambda: self.proposal.expires_at)
        offline_store = RecoveryDraftStore(self.store.root / "offline")
        offline_store.save(self.request, self.offline)
        with self.assertRaisesRegex(ApprovalRejected, "verified_v2_draft_required"):
            self.verify(drafts=offline_store)
        self.reader.data["user"]["id"] = 77
        with self.assertRaisesRegex(ApprovalRejected, "proposal_not_active_or_not_owned"):
            self.verify(allowed_github_id=77, drafts=offline_store)
        self.reader.data["user"]["id"] = OPERATOR
        offline_store.cancel(self.proposal.proposal_id, OPERATOR, now=NOW)
        with self.assertRaisesRegex(ApprovalRejected, "proposal_not_active_or_not_owned"):
            self.verify(drafts=offline_store)


if __name__ == "__main__":
    unittest.main()
