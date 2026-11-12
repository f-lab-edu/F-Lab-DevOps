"""병합된 C-03 revision의 GitOps 동기화 계획과 적용 결과를 읽기 전용으로 검사한다."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_ops.c_approval import ApprovalRejected, _main_sha, _require, _sha
from ai_ops.c_execution_preflight import _check_live
from ai_ops.c_merge_guard import _bound_locked, verify_merged_commit
from ai_ops.c_pr_link import RecoveryPRLink
from ai_ops.c_recovery import IMAGE_REPOSITORY, RecoveryProposal
from ai_ops.contracts import _utc_datetime


def _now():
    return datetime.now(timezone.utc)


def _binding(locked, proposal, pr_link, merged):
    _bound_locked(locked, proposal, pr_link)
    _require(isinstance(merged, dict) and merged.get("merge_verified") is True
             and merged.get("sync_allowed") is False
             and merged.get("proposal_id", proposal.proposal_id) == proposal.proposal_id
             and merged.get("pr_link_hash") == pr_link.link_hash
             and merged.get("base_sha") == locked["base_sha"]
             and merged.get("approved_head_sha") == locked["head_sha"]
             and merged.get("pull_number") == locked["pull_number"]
             and merged.get("lease_uid") == locked["lease"]["uid"]
             and merged.get("desired_digest") == proposal.desired_digest,
             "sync_merge_binding_invalid")
    return _sha(merged["merge_sha"])


def plan_exact_sync(*, reader, live_reader, locked, proposal: RecoveryProposal,
                    pr_link: RecoveryPRLink, merge_response, now=None):
    """현재 main·실습 장애·공유 잠금을 다시 읽고 고정 revision 명령 형태만 반환한다."""
    try:
        moment = _utc_datetime(now or _now())
        _require(moment >= _bound_locked(locked, proposal, pr_link),
                 "sync_clock_before_preflight")
        held = locked["lease"]
        first = live_reader.read(expected_lease=held)
        _check_live(first, proposal, pr_link, _utc_datetime(now or _now()), held)
        merged = verify_merged_commit(reader=reader, locked=locked, proposal=proposal,
                                      pr_link=pr_link, merge_response=merge_response)
        merge_sha = _binding(locked, proposal, pr_link, merged)
        second = live_reader.read(expected_lease=held)
        _check_live(second, proposal, pr_link, _utc_datetime(now or _now()), held)
        _require(all(first[key] == second[key] for key in
                     ("application", "deployment", "lease", "values_bytes")),
                 "sync_runtime_changed_during_plan")
        _require(_main_sha(reader, proposal.repository) == merge_sha, "sync_main_changed_during_plan")
        return {"sync_plan_ready": True, "write_enabled": False,
                "merge_sha": merge_sha, "application": "lab-target",
                "lease_uid": held["uid"], "lease_holder": held["holder"],
                "commands": [["app", "set", "lab-target", "--revision", merge_sha],
                             ["app", "sync", "lab-target", "--revision", merge_sha,
                              "--timeout", "150"]]}
    except ApprovalRejected:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        raise ApprovalRejected("sync_plan_evidence_unavailable_or_invalid") from exc


def verify_sync_result(*, reader, snapshot, locked, proposal: RecoveryProposal,
                       pr_link: RecoveryPRLink, merge_response, now=None):
    """Argo가 고정 SHA를 실제 적용했는지만 판정한다. 서비스 회복 판정은 C-04다."""
    try:
        merged = verify_merged_commit(reader=reader, locked=locked, proposal=proposal,
                                      pr_link=pr_link, merge_response=merge_response)
        merge_sha = _binding(locked, proposal, pr_link, merged)
        moment = _utc_datetime(now or _now())
        checked = _utc_datetime(snapshot["checked_at"])
        _require(checked <= moment and (moment - checked).total_seconds() <= 15,
                 "sync_result_snapshot_stale")
        _require(_main_sha(reader, proposal.repository) == merge_sha, "sync_result_main_changed")
        app, deployment, lease = (snapshot[key] for key in
                                  ("application", "deployment", "lease"))
        _require(lease["metadata"]["name"] == "b6-scenario-lock"
                 and lease["metadata"]["namespace"] == "ai-ops-lab"
                 and lease["metadata"]["uid"] == locked["lease"]["uid"]
                 and lease["spec"]["holderIdentity"] == locked["lease"]["holder"],
                 "sync_result_lock_lost")
        source = app["spec"]["source"]
        _require(app["metadata"]["name"] == "lab-target"
                 and app["metadata"]["namespace"] == "argocd"
                 and app["metadata"]["uid"] == pr_link.application_uid
                 and app["spec"]["project"] == "b6-lab"
                 and source == {"repoURL": f"https://github.com/{proposal.repository}.git",
                                "path": "ai-ops/lab", "targetRevision": merge_sha}
                 and app["spec"]["destination"] == {
                     "server": "https://kubernetes.default.svc", "namespace": "ai-ops-lab"}
                 and app["spec"].get("syncPolicy", {}).get("automated") is None
                 and app["status"]["sync"]["status"] == "Synced"
                 and app["status"]["sync"]["revision"] == merge_sha
                 and app["status"]["operationState"]["phase"] == "Succeeded"
                 and app["status"]["operationState"]["syncResult"]["revision"] == merge_sha
                 and not app["metadata"].get("deletionTimestamp"),
                 "sync_result_argo_mismatch")
        _require(deployment["metadata"]["name"] == "lab-api"
                 and deployment["metadata"]["namespace"] == "ai-ops-lab"
                 and deployment["metadata"]["uid"] == proposal.target_uid
                 and not deployment["metadata"].get("deletionTimestamp")
                 and deployment["status"]["observedGeneration"] == deployment["metadata"]["generation"]
                 and len(deployment["spec"]["template"]["spec"]["containers"]) == 1
                 and deployment["spec"]["template"]["spec"]["containers"][0]["name"] == "api"
                 and deployment["spec"]["template"]["spec"]["containers"][0]["image"]
                     == IMAGE_REPOSITORY + "@" + proposal.desired_digest,
                 "sync_result_deployment_mismatch")
        _require(_main_sha(reader, proposal.repository) == merge_sha, "sync_result_main_changed")
        return {"sync_verified": True, "service_resolved": False,
                "merge_sha": merge_sha, "desired_digest": proposal.desired_digest,
                "application_uid": pr_link.application_uid,
                "target_uid": proposal.target_uid, "lease_uid": locked["lease"]["uid"]}
    except ApprovalRejected:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, OSError) as exc:
        raise ApprovalRejected("sync_result_evidence_unavailable_or_invalid") from exc
