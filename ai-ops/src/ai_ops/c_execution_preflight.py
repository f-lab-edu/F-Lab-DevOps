"""C-03 실행 직전 읽기 재검증. 변경 실행이나 잠금 취득은 이 모듈에 없다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ai_ops.c_approval import (ApprovalGitHubReader, ApprovalRejected, _repo_api,
                               _id, _main_sha, _pr, _require, _reviews, verify_review)
from ai_ops.b6_coordination import LabLease, PURPOSE
from ai_ops.c_live_preflight import assess_live_preflight
from ai_ops.c_pr_link import RecoveryPRLink, require_matching_recheck
from ai_ops.c_recovery import IMAGE_REPOSITORY, ISOLATED_REPOSITORY, REPOSITORY, RecoveryProposal, _encoded_values
from ai_ops.contracts import _utc_datetime


REQUIRED_CHECK = "C03 Main PR Gate"
CHECK_PAGE_SIZE = 100
MAX_LIVE_AGE = timedelta(seconds=15)
MAX_PREFLIGHT_DURATION = timedelta(seconds=180)


def _require_merge_candidate(pull, reader, repository, executor_identity):
    _require(pull.get("mergeable") is True, "pull_request_not_mergeable")
    state = pull.get("mergeable_state")
    if state == "clean":
        return
    _require(repository == ISOLATED_REPOSITORY and state == "blocked",
             "pull_request_not_mergeable")
    # GitHub reports the update-only rule for the human reader. The installation
    # still uses the normal merge endpoint, which enforces review and checks.
    rule = reader.get(f"{_repo_api(repository)}/rulesets/26937610")
    _require(rule.get("target") == "branch" and rule.get("enforcement") == "active"
             and rule.get("conditions") == {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}
             and [item.get("type") for item in rule.get("rules", [])] == ["update"]
             and rule.get("bypass_actors") == [{"actor_id": executor_identity["app_id"],
                                                "actor_type": "Integration", "bypass_mode": "pull_request"}],
             "isolated_update_rule_changed")


def _now():
    return datetime.now(timezone.utc)


def _check_protection(value):
    try:
        reviews = value["required_pull_request_reviews"]
        checks = value["required_status_checks"]
        _require(reviews["required_approving_review_count"] >= 1
                 and reviews["dismiss_stale_reviews"] is True,
                 "required_review_protection_missing")
        bypass = reviews.get("bypass_pull_request_allowances") or {}
        _require(all(not bypass.get(kind) for kind in ("users", "teams", "apps")),
                 "review_bypass_allowed")
        _require(checks["strict"] is True and REQUIRED_CHECK in checks["contexts"],
                 "required_check_protection_missing")
        _require(value["enforce_admins"]["enabled"] is True
                 and value["allow_force_pushes"]["enabled"] is False
                 and value["allow_deletions"]["enabled"] is False,
                 "main_protection_unsafe")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ApprovalRejected("main_protection_unavailable") from exc


def _check_required_run(value, head_sha):
    try:
        runs = value["check_runs"]
        _require(type(value["total_count"]) is int and 0 <= value["total_count"] < CHECK_PAGE_SIZE
                 and len(runs) == value["total_count"], "check_runs_incomplete")
        required = [run for run in runs if run["name"] == REQUIRED_CHECK]
        _require(len(required) == 1, "required_check_missing_or_ambiguous")
        run = required[0]
        _require(run["head_sha"] == head_sha and run["status"] == "completed"
                 and run["conclusion"] == "success"
                 and run["app"]["slug"] == "github-actions", "required_check_not_successful")
        return _id(run["id"])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ApprovalRejected("check_runs_unavailable") from exc


def _check_executor(identity, *, approver_id, author_id, repository=REPOSITORY):
    # 설치 자격의 출처 검증은 호출자 책임이다. 여기서는 분리된 identity 계약만 검사한다.
    try:
        pull_requests = "write" if repository == REPOSITORY else "read"
        _require(identity["kind"] == "github_app_installation"
                 and identity["repository"] == repository
                 and _id(identity["actor_id"]) not in (approver_id, author_id)
                 and _id(identity["app_id"]) > 0 and _id(identity["installation_id"]) > 0
                 and identity["permissions"]["contents"] == "write"
                 and identity["permissions"]["pull_requests"] == pull_requests,
                 "executor_identity_not_separate")
        return identity["actor_id"]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ApprovalRejected("executor_identity_unavailable") from exc


def _require_recheck(draft, original, refreshed, moment):
    try:
        require_matching_recheck(draft, original, refreshed, moment)
    except (KeyError, TypeError, ValueError) as exc:
        raise ApprovalRejected("live_proof_changed_or_stale") from exc


def _check_live(snapshot, draft, link, now, expected_lease=None):
    try:
        app, deployment, lease = (snapshot[name] for name in ("application", "deployment", "lease"))
        values_bytes = snapshot["values_bytes"]
        checked_at = _utc_datetime(snapshot["checked_at"])
        _require(checked_at <= now and now - checked_at <= MAX_LIVE_AGE,
                 "live_snapshot_stale")
        _require(_utc_datetime(snapshot["proof"]["proof_checked_at"]) == checked_at,
                 "live_proof_snapshot_time_mismatch")
        if expected_lease is not None:
            _require(lease["metadata"].get("uid") == expected_lease["uid"]
                     and lease["spec"].get("holderIdentity") == expected_lease["holder"],
                     "shared_lock_lost")
            _require(_utc_datetime(lease["spec"]["acquireTime"]) <= checked_at,
                     "under_lock_proof_not_fresh")
        state = assess_live_preflight(
            app, deployment, lease, values_bytes,
            expected_lease_holder=expected_lease["holder"] if expected_lease else None,
            repository=link.repository)
        _require(state["blockers"] == ["prior_digest_and_failure_proof_required"],
                 "runtime_not_failed_and_unlocked")
        _require(app["metadata"]["uid"] == link.application_uid
                 and deployment["metadata"]["uid"] == link.target_uid
                 and app["spec"]["source"]["targetRevision"] == link.observed_revision_sha
                 and app["status"]["sync"]["revision"] == link.observed_revision_sha
                 and app["status"]["operationState"]["syncResult"]["revision"] == link.observed_revision_sha
                 and app["status"]["health"]["status"] in ("Degraded", "Progressing")
                 and app["status"]["sync"]["status"] == "Synced"
                 and app["status"]["operationState"]["phase"] == "Succeeded"
                 and not app["metadata"].get("deletionTimestamp")
                 and not deployment["metadata"].get("deletionTimestamp")
                 and deployment["status"].get("observedGeneration") == deployment["metadata"].get("generation")
                 and deployment["status"].get("readyReplicas", 0) == 0
                 and len(deployment["spec"]["template"]["spec"]["containers"]) == 1
                 and deployment["spec"]["template"]["spec"]["containers"][0]["name"] == "api"
                 and deployment["spec"]["template"]["spec"]["containers"][0]["image"]
                     == IMAGE_REPOSITORY + "@" + draft.current_digest
                 and values_bytes == _encoded_values(draft.current_digest).encode(),
                 "runtime_target_changed")
        _require(lease.get("apiVersion") == "coordination.k8s.io/v1"
                 and lease.get("kind") == "Lease"
                 and all(lease["metadata"].get("labels", {}).get(key) == value
                         for key, value in PURPOSE.items())
                 and lease["metadata"].get("uid") and lease["metadata"].get("resourceVersion")
                 and lease["spec"].get("leaseDurationSeconds") is None,
                 "lease_identity_unavailable")
        not_before = lease["metadata"].get("annotations", {}).get("ai-ops/b6-not-before")
        _require(not not_before or _utc_datetime(not_before) <= now, "lab_cooldown_active")
    except ApprovalRejected:
        raise
    except (KeyError, TypeError, IndexError, AttributeError, ValueError) as exc:
        raise ApprovalRejected("live_snapshot_unavailable_or_invalid") from exc


def verify_execution_preflight(*, drafts, proposal_id, pull_number, allowed_github_id,
                               pr_author_id, executor_identity, original_recheck,
                               pr_link, live_reader, reader=None, clock=_now,
                               expected_lease=None):
    """독립 원본을 다시 읽어 로컬 실행 후보만 판정한다.

    ``live_reader.read``는 매 호출마다 Kubernetes/ECR/CI 원본에서 새 증거를 만들어야 한다.
    결과는 잠금을 보유하지 않으므로 merge 또는 동기화 권한으로 사용하면 안 된다.
    """
    try:
        _require(isinstance(pr_link, RecoveryPRLink), "pr_link_required")
        repository = pr_link.repository
        api = _repo_api(repository)
        if reader is None:
            reader = ApprovalGitHubReader(max_read_seconds=180, repository=repository)
        first_time = _utc_datetime(clock())
        review = verify_review(drafts=drafts, proposal_id=proposal_id, pull_number=pull_number,
                               allowed_github_id=allowed_github_id, pr_author_id=pr_author_id,
                               reader=reader, clock=clock, pr_link=pr_link)
        draft = drafts.read(proposal_id, actor_github_id=allowed_github_id, now=first_time)
        _require(isinstance(draft, RecoveryProposal) and pr_link.matches_draft(draft),
                 "pr_link_draft_mismatch")
        executor_id = _check_executor(executor_identity, approver_id=allowed_github_id,
                                      author_id=pr_author_id, repository=repository)
        protection_path = f"{api}/branches/main/protection"
        checks_path = f"{api}/commits/{review['head_sha']}/check-runs?per_page=100"
        protection = reader.get(protection_path)
        runs = reader.get(checks_path)
        _check_protection(protection)
        run_id = _check_required_run(runs, review["head_sha"])
        first = live_reader.read(expected_lease=expected_lease)
        _require_recheck(draft, original_recheck, first["proof"], _utc_datetime(clock()))
        _check_live(first, draft, pr_link, _utc_datetime(clock()), expected_lease)
        second = live_reader.read(expected_lease=expected_lease)
        finished = _utc_datetime(clock())
        _require_recheck(draft, original_recheck, second["proof"], finished)
        _check_live(second, draft, pr_link, finished, expected_lease)
        _require(all(first[name] == second[name] for name in (
            "application", "deployment", "lease", "values_bytes")),
            "live_state_changed_during_preflight")
        _require(_main_sha(reader, repository) == review["base_sha"], "base_sha_changed")
        final_pr = reader.get(f"{api}/pulls/{pull_number}")
        head, _ = _pr(final_pr, pull_number, review["base_sha"], pr_author_id, repository)
        _require(head == review["head_sha"], "pull_request_changed_during_preflight")
        final_reviews = _reviews(reader.get(f"{api}/pulls/{pull_number}/reviews?per_page=100"),
                                 allowed_github_id, head)
        _require(final_reviews[0] == review["review_id"]
                 and final_reviews[3] == review["approved_at"],
                 "approval_changed_during_preflight")
        _require_merge_candidate(final_pr, reader, repository, executor_identity)
        _require(reader.get(protection_path) == protection
                 and reader.get(checks_path) == runs,
                 "github_gate_changed_during_preflight")
        _require(drafts.read(proposal_id, actor_github_id=allowed_github_id, now=finished) == draft,
                 "proposal_changed_during_preflight")
        _require(first_time <= finished < review["expires_at"]
                 and finished - review["checked_at"] <= MAX_PREFLIGHT_DURATION
                 and finished - _utc_datetime(second["checked_at"]) <= MAX_LIVE_AGE,
                 "preflight_too_slow_or_expired")
        return {"preflight_ready": True, "execution_allowed": False,
                "reason": ("executor_write_adapter_required" if expected_lease else
                           "shared_lock_and_under_lock_recheck_required"),
                "proposal_id": proposal_id, "pr_link_hash": pr_link.link_hash,
                "pull_number": pull_number, "head_sha": head, "base_sha": review["base_sha"],
                "review_id": review["review_id"], "required_check_run_id": run_id,
                "executor_actor_id": executor_id, "checked_at": finished}
    except ApprovalRejected:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        raise ApprovalRejected("execution_preflight_input_unavailable_or_invalid") from exc


def acquire_locked_preflight(*, lease: LabLease, **preflight_args):
    """공유 잠금을 CAS로 취득하고 그 보유 상태에서 원본을 다시 확인한다.

    반환된 잠금은 호출자가 소유한다. 실패 뒤 잠금을 자동 해제하지 않는다. 실행
    adapter가 연결되기 전에는 이 결과도 merge·동기화를 허용하지 않는다.
    """
    _require(isinstance(lease, LabLease) and "expected_lease" not in preflight_args,
             "invalid_shared_lock_adapter")
    verify_execution_preflight(**preflight_args)
    try:
        held = lease.acquire()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # API 응답을 잃었을 수도 있으므로 취득 실패를 잠금 부재로 해석하지 않는다.
        raise ApprovalRejected("shared_lock_acquire_unconfirmed_inspect_lease") from exc
    try:
        result = verify_execution_preflight(**preflight_args, expected_lease=held)
    except ApprovalRejected as exc:
        # merge는 시작하지 않았지만 외부 상태가 변했을 수 있다. 동일 기준을 확인하기
        # 전에는 잠금을 풀지 않으며 실행자가 현재 Lease와 대상을 조사해야 한다.
        raise ApprovalRejected("locked_preflight_failed_lock_retained") from exc
    return {**result, "locked_preflight_ready": True, "lease": held}
