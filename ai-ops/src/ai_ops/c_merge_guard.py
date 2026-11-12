"""C-03 보호된 PR의 조건부 병합 계획과 병합 결과를 읽기 전용으로 대조한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from ai_ops.c_approval import (ApprovalRejected, _repo_api, _id, _main_sha, _require,
                               _sha, _verify_values_tree_diff)
from ai_ops.c_pr_link import RecoveryPRLink
from ai_ops.c_recovery import RecoveryProposal
from ai_ops.contracts import _utc_datetime


C3_HOLDER = re.compile(r"c3-[a-f0-9]{32}\Z")
MERGE_PLAN_AGE = timedelta(seconds=15)


def _now():
    return datetime.now(timezone.utc)


def _bound_locked(locked, proposal, link):
    _require(isinstance(proposal, RecoveryProposal) and proposal.schema_version == 2
             and isinstance(link, RecoveryPRLink) and link.matches_draft(proposal),
             "merge_proposal_link_invalid")
    _require(locked["locked_preflight_ready"] is True
             and locked["preflight_ready"] is True
             and locked["execution_allowed"] is False
             and locked["proposal_id"] == proposal.proposal_id
             and locked["pr_link_hash"] == link.link_hash
             and _sha(locked["base_sha"]) == link.pr_base_sha
             and _sha(locked["head_sha"]) != link.pr_base_sha
             and _id(locked["pull_number"]) > 0
             and _id(locked["review_id"]) > 0
             and _id(locked["required_check_run_id"]) > 0
             and _id(locked["executor_actor_id"]) > 0,
             "locked_merge_binding_invalid")
    lease = locked["lease"]
    _require(isinstance(lease, dict) and lease.get("name") == "b6-scenario-lock"
             and isinstance(lease.get("uid"), str) and lease["uid"]
             and isinstance(lease.get("holder"), str)
             and C3_HOLDER.fullmatch(lease["holder"]), "locked_merge_lease_invalid")
    return _utc_datetime(locked["checked_at"])


def plan_exact_merge_request(*, locked, proposal, pr_link, now=None):
    """검토 가능한 요청 형식만 만든다. 자격 사용이나 GitHub 변경은 수행하지 않는다."""
    try:
        checked_at = _bound_locked(locked, proposal, pr_link)
        moment = _utc_datetime(now or _now())
        _require(checked_at <= moment < checked_at + MERGE_PLAN_AGE,
                 "locked_preflight_expired_for_merge")
        return {"method": "PUT", "endpoint": f"{_repo_api(proposal.repository)}/pulls/{locked['pull_number']}/merge",
                "body": {"sha": locked["head_sha"], "merge_method": "squash",
                         "merge_action": "direct_merge", "bypass_rules": False},
                "write_enabled": False}
    except ApprovalRejected:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ApprovalRejected("merge_plan_input_invalid") from exc


def verify_merged_commit(*, reader, locked, proposal, pr_link, merge_response):
    """병합 응답만 믿지 않고 현재 main·PR·두 Git 트리에서 정확한 변경을 확인한다."""
    try:
        _bound_locked(locked, proposal, pr_link)
        _require(isinstance(merge_response, dict) and merge_response["merged"] is True,
                 "merge_not_confirmed")
        merge_sha = _sha(merge_response["sha"])
        _require(_main_sha(reader, proposal.repository) == merge_sha, "merged_main_sha_mismatch")
        endpoint = f"{_repo_api(proposal.repository)}/pulls/{locked['pull_number']}"
        pull = reader.get(endpoint)
        _require(_id(pull["number"]) == locked["pull_number"]
                 and pull["state"] == "closed" and pull["merged"] is True
                 and pull["merge_commit_sha"] == merge_sha
                 and _id(pull["merged_by"]["id"]) == locked["executor_actor_id"]
                 and pull["base"]["ref"] == "main"
                 and pull["base"]["repo"]["full_name"] == proposal.repository
                 and pull["head"]["repo"]["full_name"] == proposal.repository
                 and pull["head"]["sha"] == locked["head_sha"],
                 "merged_pull_request_binding_mismatch")
        base = reader.get(f"{_repo_api(proposal.repository)}/git/commits/{locked['base_sha']}")
        merged = reader.get(f"{_repo_api(proposal.repository)}/git/commits/{merge_sha}")
        _require(base["sha"] == locked["base_sha"] and merged["sha"] == merge_sha
                 and isinstance(merged["parents"], list)
                 and [parent["sha"] for parent in merged["parents"]] == [locked["base_sha"]],
                 "merged_commit_parent_mismatch")
        _verify_values_tree_diff(reader, proposal, base["tree"]["sha"], merged["tree"]["sha"])
        _require(_main_sha(reader, proposal.repository) == merge_sha and reader.get(endpoint) == pull,
                 "merge_changed_during_verification")
        return {"merge_verified": True, "sync_allowed": False,
                "repository": proposal.repository, "pull_number": locked["pull_number"],
                "approved_head_sha": locked["head_sha"], "base_sha": locked["base_sha"],
                "merge_sha": merge_sha, "desired_digest": proposal.desired_digest,
                "pr_link_hash": pr_link.link_hash, "lease_uid": locked["lease"]["uid"]}
    except ApprovalRejected:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        raise ApprovalRejected("merge_evidence_unavailable_or_invalid") from exc
