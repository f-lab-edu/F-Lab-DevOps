"""현재 실습 대상·실제 경보 정의를 읽고 인계 영수증이 있을 때만 수집 계획을 만든다."""

from datetime import datetime, timezone
import time

from ai_ops.contracts import sha256_json
from ai_ops.c_recovery import IMAGE_REPOSITORY
from ai_ops.c_verification import RecoveryBinding
from ai_ops.c_verification_collect import (
    CollectionPlan, SCOPE, SELECTORS, check_anchor, check_location, definition_hash,
    metric_value, read_alert_rule,
)
from ai_ops.c_verification_handoff import C03Handoff
from ai_ops.c_verification_transport import CollectionError


def _target(application, deployment):
    source = application["spec"]["source"]
    binding = RecoveryBinding(application_uid=application["metadata"]["uid"],
                              target_uid=deployment["metadata"]["uid"])
    containers = deployment["spec"]["template"]["spec"]["containers"]
    if (len(containers) != 1 or containers[0].get("name") != "api"
            or not containers[0].get("image", "").startswith(IMAGE_REPOSITORY+"@sha256:")):
        raise CollectionError("target_image_unavailable")
    return {"binding": binding.model_dump(mode="json"), "repository": source["repoURL"],
        "source_revision": source["targetRevision"],
        "applied_revision": application.get("status", {}).get("sync", {}).get("revision"),
        "image": containers[0]["image"], "generation": deployment["metadata"]["generation"],
        "template_hash": sha256_json(deployment["spec"]["template"])}


def prepare_collection(reader, *, handoff: C03Handoff | None = None,
                       clock=lambda: datetime.now(timezone.utc), monotonic=time.monotonic,
                       sleep=time.sleep) -> dict:
    """정상 상태 조회는 C-03 복구가 아니다. 인계가 없으면 실행 입력을 만들지 않는다."""
    output = {"record_type": "c04_collection_preparation", "status": "blocked", "reasons": [],
        "prepared_at": clock().isoformat(), "execution_allowed": False, "external_model_calls": 0,
        "collection_plan": None, "c03_execution_verified_here": False}
    previous_deadline = getattr(reader, "deadline", None)
    try:
        application, deployment = reader.get("application"), reader.get("deployment")
        repository = application["spec"]["source"]["repoURL"]
        if repository not in ("https://github.com/f-lab-edu/F-Lab-DevOps.git",
                              "https://github.com/joungGo/c03-execution-lab.git"):
            raise CollectionError("source_or_destination_mismatch")
        check_location(repository, application, deployment)
        target = _target(application, deployment)
        output["observed_target"] = target
        if handoff is not None:
            candidate = CollectionPlan(execution=handoff.execution, repository=handoff.repository)
            check_anchor(candidate, application, deployment)
            if (handoff.created_at > clock() or repository != handoff.repository
                    or target["source_revision"] != handoff.execution.merge_sha
                    or target["applied_revision"] != handoff.execution.merge_sha
                    or target["image"] != IMAGE_REPOSITORY+"@"+handoff.execution.desired_digest):
                raise CollectionError("c03_handoff_current_target_mismatch")
        prom = reader.get("prometheus")
        if not any(x.get("type") == "Ready" and x.get("status") == "True"
                   for x in prom.get("status", {}).get("conditions", [])):
            raise CollectionError("prometheus_not_ready")
        with reader.forward("monitoring", prom["metadata"]["name"], 9090) as port:
            started = monotonic()
            if handoff is not None and hasattr(reader, "deadline"):
                deadline = time.monotonic()+90
                reader.deadline = min(deadline, previous_deadline) if previous_deadline is not None else deadline
            while True:
                definition, rule = read_alert_rule(reader, port)
                now = clock()
                evaluated = datetime.fromisoformat(rule["lastEvaluation"].replace("Z", "+00:00"))
                if (rule.get("health") != "ok" or rule.get("lastError") or evaluated.tzinfo is None
                        or not 0 <= (now-evaluated).total_seconds() <= 60):
                    raise CollectionError("alert_evaluation_unavailable")
                if handoff is None:
                    break
                try:
                    if evaluated < handoff.created_at:
                        raise CollectionError("sources_not_after_c03_yet")
                    selectors = {**SELECTORS, **{name: name+'{namespace="ai-ops-lab",deployment="lab-api"}'
                        for name in ("kube_deployment_spec_replicas", "kube_deployment_metadata_generation",
                                     "kube_deployment_status_observed_generation", "kube_deployment_status_replicas_updated")}}
                    for name, query in selectors.items():
                        labels = {"namespace": "ai-ops-lab", "service": "lab-api"} if name == "target_up" else SCOPE
                        metric_value(reader, port, query, labels, now, 60, handoff.created_at)
                    break
                except CollectionError as error:
                    # 새 scrape·규칙 평가를 기다리는 시간은 회복 관찰 표본으로 계산하지 않는다.
                    if str(error) not in ("sources_not_after_c03_yet", "metric_source_stale"):
                        raise
                    if monotonic()-started >= 85:
                        raise CollectionError("post_c03_sources_not_ready") from None
                    sleep(5)
            output["source_warmup_seconds"] = round(monotonic()-started, 3)
        if reader.get("prometheus")["metadata"]["uid"] != prom["metadata"]["uid"]:
            raise CollectionError("prometheus_identity_changed")
        output.update(alert=definition.model_dump(mode="json"),
            alert_definition_hash=definition_hash(definition.model_dump(mode="json")),
            alert_evaluated_at=evaluated.isoformat(), prometheus_uid=prom["metadata"]["uid"])
        after_app, after_deployment = reader.get("application"), reader.get("deployment")
        check_location(repository, after_app, after_deployment)
        if target != _target(after_app, after_deployment):
            raise CollectionError("preparation_target_changed")
        if handoff is None:
            output.update(status="waiting_for_c03_handoff", reasons=["fresh_c03_handoff_required"])
        else:
            output.update(status="ready", handoff_hash=sha256_json(handoff.model_dump(mode="json")),
                collection_plan=CollectionPlan(execution=handoff.execution, repository=repository,
                    alert=definition).model_dump(mode="json"))
    except (ValueError, KeyError, TypeError, OSError, IndexError) as error:
        output["reasons"] = [str(error) if isinstance(error, CollectionError) else "preparation_source_unavailable"]
    finally:
        if hasattr(reader, "deadline"):
            reader.deadline = previous_deadline
    output.update(http_requests=getattr(reader, "http_requests", 0),
                  forwards_started=getattr(reader, "forwards_started", 0))
    return output
