"""C-03 복구 초안에 대한 GitHub PR 리뷰를 읽기 전용으로 검증한다."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import selectors
import sqlite3
import subprocess
import time

from ai_ops.c_pr_link import (RecoveryPRLink, check_rendered_image_change, make_pr_link,
                              read_private_c_record, require_matching_recheck)
from ai_ops.c_recovery import COMMIT, ISOLATED_REPOSITORY, RECOVERY_REPOSITORIES, REPOSITORY, VALUES_PATH, RecoveryDraftStore, RecoveryProposal, _encoded_values, _values
from ai_ops.contracts import _utc_datetime
from ai_ops.path_policy import permitted_path


REPO_API = f"repos/{REPOSITORY}"
APPROVAL_READ_SUFFIX = (
    r"/(?:pulls/[1-9][0-9]{0,19}(?:/reviews\?per_page=100)?|"
    r"branches/main/protection|commits/[a-f0-9]{40}/check-runs\?per_page=100|"
    r"git/(?:ref/heads/main|(?:commits|trees|blobs)/[a-f0-9]{40}))\Z"
)
APPROVAL_READ_PATH = re.compile(re.escape(REPO_API) + APPROVAL_READ_SUFFIX)
REVIEW_TTL = timedelta(minutes=30)
REVIEW_PAGE_SIZE = 100
MAX_API_BYTES = 2 * 1024 * 1024
MAX_READ_SECONDS = 45


class ApprovalRejected(ValueError):
    """검증 거부 사유만 전달한다. API 원문이나 자격을 담지 않는다."""


def _require(condition, reason):
    if not condition:
        raise ApprovalRejected(reason)


def _repo_api(repository):
    _require(repository in RECOVERY_REPOSITORIES, "github_repository_not_allowed")
    return f"repos/{repository}"


def _id(value):
    _require(type(value) is int and 0 < value < 10**20, "invalid_github_id")
    return value


def _sha(value):
    _require(isinstance(value, str) and COMMIT.fullmatch(value), "invalid_git_sha")
    return value


class ApprovalGitHubReader:
    """현재 gh 자격으로 GitHub 원본만 조회하고 응답 크기와 전체 시간을 제한한다."""

    def __init__(self, *, max_read_seconds=MAX_READ_SECONDS, repository=REPOSITORY):
        _require(type(max_read_seconds) is int and 8 <= max_read_seconds <= 180,
                 "invalid_github_read_budget")
        self.deadline = time.monotonic() + max_read_seconds
        self.read_path = re.compile(re.escape(_repo_api(repository)) + APPROVAL_READ_SUFFIX)
        self.update_ruleset_path = (f"{_repo_api(repository)}/rulesets/26937610"
                                    if repository == ISOLATED_REPOSITORY else None)

    def get(self, endpoint):
        _require(endpoint in ("user", self.update_ruleset_path) or self.read_path.fullmatch(endpoint),
                 "github_read_path_rejected")
        deadline = min(self.deadline, time.monotonic() + 8)
        _require(time.monotonic() < deadline, "github_read_timeout")
        # 호출부는 고정 저장소·경로와 검증된 정수 또는 SHA만 전달한다.
        try:
            with subprocess.Popen(
                ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            ) as process:
                try:
                    data = bytearray()
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        while True:
                            remaining = deadline - time.monotonic()
                            _require(remaining > 0 and selector.select(remaining), "github_read_timeout")
                            chunk = os.read(process.stdout.fileno(), min(8192, MAX_API_BYTES + 1 - len(data)))
                            if not chunk:
                                break
                            data.extend(chunk)
                            _require(len(data) <= MAX_API_BYTES, "github_response_too_large")
                    _require(process.wait(timeout=max(0, deadline - time.monotonic())) == 0,
                             "github_read_failed")
                    return json.loads(data)
                finally:
                    if process.poll() is None:
                        process.kill()
        except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
            raise ApprovalRejected("github_read_failed") from exc


def _pr(data, number, base_sha, author_id, repository=REPOSITORY):
    _require(_id(data["number"]) == number and data["state"] == "open"
             and data["draft"] is False and data["merged"] is False,
             "pull_request_not_open_for_review")
    _require(_id(data["user"]["id"]) == author_id and data["user"]["type"] == "Bot",
             "unexpected_pull_request_author")
    base, head = data["base"], data["head"]
    _require(base["ref"] == "main" and head["ref"] != "main"
             and all(side["repo"]["full_name"] == repository for side in (base, head)),
             "unexpected_pull_request_scope")
    _require(_sha(base["sha"]) == base_sha, "base_sha_changed")
    return _sha(head["sha"]), _utc_datetime(data["created_at"])


def _reviews(data, approver_id, head_sha):
    _require(isinstance(data, list) and len(data) < REVIEW_PAGE_SIZE, "review_list_incomplete")
    latest = {}
    seen = set()
    # GitHub은 리뷰를 시간순으로 반환한다. 일반 댓글·작성 중인 리뷰는 판정을 대체하지 않는다.
    for review in data:
        review_id = _id(review["id"])
        _require(review_id not in seen, "invalid_review_list")
        seen.add(review_id)
        state = review["state"]
        if state in ("COMMENTED", "PENDING"):
            continue
        _require(state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"), "unknown_review_state")
        user = review["user"]
        user_id = _id(user["id"])
        entry = (review_id, state, _sha(review["commit_id"]),
                 _utc_datetime(review["submitted_at"]), user["type"])
        _require(user_id not in latest or latest[user_id][3] <= entry[3], "invalid_review_order")
        latest[user_id] = entry
    _require(not any(entry[1] == "CHANGES_REQUESTED" for entry in latest.values()),
             "changes_requested")
    approval = latest.get(approver_id)
    _require(approval is not None and approval[1] == "APPROVED" and approval[4] == "User",
             "eligible_approval_missing")
    _require(approval[2] == head_sha, "review_commit_mismatch")
    return approval


def _tree(reader, sha, repository=REPOSITORY):
    data = reader.get(f"{_repo_api(repository)}/git/trees/{sha}")
    _require(data["sha"] == sha and data["truncated"] is False and isinstance(data["tree"], list),
             "git_tree_incomplete")
    entries = {}
    for entry in data["tree"]:
        path = entry["path"]
        _require(isinstance(path, str) and path and "/" not in path and path not in entries,
                 "invalid_git_tree")
        entries[path] = (entry["type"], entry["mode"], _sha(entry["sha"]))
    return entries


def _blob(reader, sha, *, max_bytes=2048, repository=REPOSITORY):
    data = reader.get(f"{_repo_api(repository)}/git/blobs/{sha}")
    _require(data["sha"] == sha and data["encoding"] == "base64"
             and type(data["size"]) is int and 0 < data["size"] <= max_bytes,
             "unexpected_values_blob")
    content = base64.b64decode(data["content"].replace("\n", ""), validate=True)
    _require(len(content) == data["size"], "unexpected_values_blob")
    return content


def _verify_diff(reader, proposal, base_sha, head_sha, binding_hash):
    repository = proposal.repository
    base = reader.get(f"{_repo_api(repository)}/git/commits/{base_sha}")
    head = reader.get(f"{_repo_api(repository)}/git/commits/{head_sha}")
    _require(base["sha"] == base_sha and head["sha"] == head_sha
             and isinstance(head["parents"], list)
             and [parent["sha"] for parent in head["parents"]] == [base_sha],
             "head_not_single_commit_on_base")
    # PR 본문은 수정할 수 있다. 리뷰가 가리키는 커밋 자체에 초안 해시를 고정한다.
    binding = "Recovery-Proposal-SHA256:"
    lines = [line for line in head["message"].splitlines() if line.lstrip().startswith(binding)]
    _require(lines == [f"{binding} {binding_hash}"], "proposal_binding_mismatch")
    _verify_values_tree_diff(reader, proposal, base["tree"]["sha"], head["tree"]["sha"])


def _verify_values_tree_diff(reader, proposal, before, after):
    repository = proposal.repository
    """PR head와 실제 병합 커밋에 동일한 단일 values 변경 정책을 적용한다."""
    before, after = _sha(before), _sha(after)
    parts = VALUES_PATH.split("/")
    for index, name in enumerate(parts):
        old, new = _tree(reader, before, repository), _tree(reader, after, repository)
        _require(old.keys() == new.keys() and name in old
                 and all(old[key] == new[key] for key in old if key != name), "unexpected_git_change")
        old_type, old_mode, before = old[name]
        new_type, new_mode, after = new[name]
        expected = ("blob", "100644") if index == len(parts) - 1 else ("tree", "040000")
        _require((old_type, old_mode) == expected == (new_type, new_mode) and before != after,
                 "unexpected_git_change")
    _require(_blob(reader, before, repository=repository) == _encoded_values(proposal.current_digest).encode()
             and _blob(reader, after, repository=repository) == _encoded_values(proposal.desired_digest).encode(),
             "proposal_diff_mismatch")


def _now():
    return datetime.now(timezone.utc)


def _main_sha(reader, repository=REPOSITORY):
    main = reader.get(f"{_repo_api(repository)}/git/ref/heads/main")
    _require(main["ref"] == "refs/heads/main" and main["object"]["type"] == "commit",
             "invalid_main_ref")
    return _sha(main["object"]["sha"])


def _entry(tree, path, kind, mode):
    entry = tree.get(path)
    _require(entry is not None and entry[:2] == (kind, mode), "unexpected_lab_source")
    return entry[2]


def _lab_tree(reader, revision, repository=REPOSITORY):
    commit = reader.get(f"{_repo_api(repository)}/git/commits/{revision}")
    _require(commit["sha"] == revision, "unexpected_lab_source_revision")
    root = _tree(reader, _sha(commit["tree"]["sha"]), repository)
    ai_ops = _tree(reader, _entry(root, "ai-ops", "tree", "040000"), repository)
    return _entry(ai_ops, "lab", "tree", "040000")


def prepare_pr_link(draft, original, refreshed, *, reader=None, clock=_now):
    """원본 재조회와 고정 Git 트리로 제안 후보 또는 변경 불필요를 판정한다."""
    repository = draft.repository
    reader = ApprovalGitHubReader(repository=repository) if reader is None else reader
    require_matching_recheck(draft, original, refreshed, _utc_datetime(clock()))
    base_sha = _main_sha(reader, repository)
    lab_sha = _lab_tree(reader, base_sha, repository)
    lab = _tree(reader, lab_sha, repository)
    raw_values = _blob(reader, _entry(lab, "values.yaml", "blob", "100644"), repository=repository)
    main_digest = _values(raw_values)["api"]["image"]["digest"]
    _require(raw_values == _encoded_values(main_digest).encode(), "noncanonical_main_values")
    result = {"pr_base_sha": base_sha, "observed_revision_sha": draft.base_sha,
              "main_digest": main_digest, "runtime_status": "observed_failure",
              "pr_created": False, "execution_allowed": False}
    link = None
    if main_digest == draft.desired_digest:
        result["status"] = "main_already_at_desired_digest"
    elif main_digest != draft.current_digest or _lab_tree(reader, draft.base_sha, repository) != lab_sha:
        result["status"] = "lab_source_diverged"
    else:
        _require(set(lab) == {"Chart.yaml", "values.schema.json", "values.yaml",
                              "templates", "README.md", "deployment.json"}, "unexpected_lab_chart_files")
        templates = _tree(reader, _entry(lab, "templates", "tree", "040000"), repository)
        _require(set(templates) == {"deployment.yaml"}, "unexpected_lab_templates")
        chart_files = {
            name: _blob(reader, _entry(lab, name, "blob", "100644"), max_bytes=65536, repository=repository)
            for name in ("Chart.yaml", "values.schema.json")
        }
        chart_files["templates/deployment.yaml"] = _blob(
            reader, _entry(templates, "deployment.yaml", "blob", "100644"), max_bytes=65536, repository=repository)
        check_rendered_image_change(chart_files, draft.current_digest, draft.desired_digest)
        result["status"] = "pr_candidate"

    _require(_main_sha(reader, repository) == base_sha, "base_sha_changed")
    moment = _utc_datetime(clock())
    require_matching_recheck(draft, original, refreshed, moment)
    if result["status"] == "pr_candidate":
        link = make_pr_link(draft, refreshed, base_sha, lab_sha, moment)
    return result, link


def verify_review(*, drafts, proposal_id, pull_number, allowed_github_id, pr_author_id,
                  reader=None, clock=_now, pr_link=None):
    """검증 시점의 리뷰 근거를 반환한다. 승인 상태를 저장하거나 실행을 허용하지 않는다."""
    try:
        return _verify_review(drafts, proposal_id, pull_number, allowed_github_id, pr_author_id,
                              ApprovalGitHubReader(repository=pr_link.repository if pr_link else REPOSITORY)
                              if reader is None else reader, clock, pr_link)
    except ApprovalRejected:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, OSError, sqlite3.Error) as exc:
        raise ApprovalRejected("approval_input_unavailable_or_invalid") from exc


def _active_draft(drafts, proposal_id, approver_id, moment):
    try:
        return drafts.read(proposal_id, actor_github_id=approver_id, now=moment)
    except ValueError as exc:
        raise ApprovalRejected("proposal_not_active_or_not_owned") from exc


def _verify_review(drafts, proposal_id, pull_number, allowed_github_id, pr_author_id, reader, clock, pr_link):
    number, approver_id, author_id = map(_id, (pull_number, allowed_github_id, pr_author_id))
    _require(approver_id != author_id, "reviewer_is_pull_request_author")
    user = reader.get("user")
    _require(_id(user["id"]) == approver_id and user["type"] == "User", "operator_not_authorized")
    proposal = _active_draft(drafts, proposal_id, approver_id, _utc_datetime(clock()))
    _require(isinstance(proposal, RecoveryProposal) and proposal.schema_version == 2,
             "verified_v2_draft_required")

    repository = proposal.repository
    _require(repository == REPOSITORY or pr_link is not None, "isolated_pr_link_required")
    base_sha = proposal.base_sha
    binding_hash = proposal.proposal_hash
    created_at, expires_at = proposal.created_at, proposal.expires_at
    if pr_link is not None:
        _require(isinstance(pr_link, RecoveryPRLink) and pr_link.matches_draft(proposal),
                 "pr_link_draft_mismatch")
        base_sha, binding_hash = pr_link.pr_base_sha, pr_link.link_hash
        created_at, expires_at = pr_link.created_at, pr_link.expires_at
        _require(_lab_tree(reader, base_sha, repository) == pr_link.lab_tree_sha
                 and _lab_tree(reader, proposal.base_sha, repository) == pr_link.lab_tree_sha, "pr_link_lab_tree_mismatch")

    endpoint = f"{_repo_api(repository)}/pulls/{number}"
    review_path = f"{endpoint}/reviews?per_page={REVIEW_PAGE_SIZE}"
    head_sha, pr_created_at = _pr(reader.get(endpoint), number, base_sha, author_id, repository)
    _require(created_at <= pr_created_at < expires_at, "pull_request_outside_proposal_window")
    review = _reviews(reader.get(review_path), approver_id, head_sha)
    _verify_diff(reader, proposal, base_sha, head_sha, binding_hash)

    # 파일을 읽는 동안 리뷰·PR·main·로컬 초안 상태가 바뀌었으면 닫힌 상태로 끝낸다.
    _require(_reviews(reader.get(review_path), approver_id, head_sha) == review,
             "approval_changed_during_verification")
    _require(_pr(reader.get(endpoint), number, base_sha, author_id, repository) == (head_sha, pr_created_at),
             "pull_request_changed_during_verification")
    _require(_main_sha(reader, repository) == base_sha, "base_sha_changed")
    checked_draft_at = _utc_datetime(clock())
    final = _active_draft(drafts, proposal_id, approver_id, checked_draft_at)
    _require(final == proposal, "proposal_changed_during_verification")
    checked_at = _utc_datetime(clock())
    _require(checked_at >= checked_draft_at, "verification_clock_changed")
    approved_at = review[3]
    expires_at = min(expires_at, approved_at + REVIEW_TTL)
    _require(pr_created_at <= approved_at <= checked_at < expires_at, "approval_outside_validity_window")
    return {
        "review_verified": True, "execution_allowed": False,
        "proposal_id": proposal_id, "proposal_hash": proposal.proposal_hash,
        "repository": repository, "pull_number": number, "review_id": review[0],
        "approver_github_id": approver_id, "head_sha": head_sha, "base_sha": base_sha,
        **({"observed_revision_sha": proposal.base_sha, "pr_link_hash": binding_hash} if pr_link else {}),
        "diff_hash": proposal.diff_hash, "action": "restore_lab_image_digest",
        "approved_at": approved_at, "expires_at": expires_at, "checked_at": checked_at,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="C-03 실습 복구 제안의 GitHub 리뷰 읽기 검증")
    parser.add_argument("--state-root", type=Path,
                        default=Path.home() / ".local/share/url-shortener-ai-ops/c-approvals")
    parser.add_argument("--proposal-id", required=True)
    parser.add_argument("--pr-link", type=Path)
    parser.add_argument("--pull-number", required=True, type=int)
    parser.add_argument("--allowed-github-id", required=True, type=int)
    parser.add_argument("--pr-author-id", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        drafts = RecoveryDraftStore(args.state_root)
        pr_link = None
        if args.pr_link is not None:
            try:
                link_path = permitted_path(args.pr_link, "input")
                pr_link = RecoveryPRLink.model_validate_json(read_private_c_record(link_path))
            except (ValueError, OSError) as exc:
                raise ApprovalRejected("invalid_pr_link") from exc
        result = verify_review(drafts=drafts, proposal_id=args.proposal_id, pull_number=args.pull_number,
                               allowed_github_id=args.allowed_github_id, pr_author_id=args.pr_author_id, pr_link=pr_link)
    except (ValueError, OSError, sqlite3.Error) as exc:
        reason = str(exc) if isinstance(exc, ApprovalRejected) else "draft_store_unavailable"
        print(json.dumps({"review_verified": False, "execution_allowed": False, "reason": reason}))
        return 1
    print(json.dumps(result, default=lambda value: value.isoformat().replace("+00:00", "Z"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
