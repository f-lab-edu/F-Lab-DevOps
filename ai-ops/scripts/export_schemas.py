"""Pydantic 데이터 계약에서 공개 JSON 스키마 파일을 생성한다."""

import json
from pathlib import Path

from ai_ops.contracts import AnalysisReport, EvidenceBundle, IncidentRequest
from ai_ops.c_verification import FollowUpScope, VerificationInput
from ai_ops.c_verification_collect import CollectionPlan
from ai_ops.c_verification_handoff import C03Handoff
from ai_ops.c_failure_handoff import FailureHandoff
from ai_ops.c_trial_execution import TrialAuthorization, TrialPlan
from ai_ops.c_trial_ready import ReadyTrialPlan, ReadyReceipt, ReadyApproval, ReadyActivation


SCHEMAS = {
    "incident-request": IncidentRequest,
    "evidence-bundle": EvidenceBundle,
    "analysis-report": AnalysisReport,
    "c4-verification-input": VerificationInput,
    "c4-follow-up-scope": FollowUpScope,
    "c4-collection-plan": CollectionPlan,
    "c03-c04-handoff": C03Handoff,
    "injection-failure-handoff": FailureHandoff,
    "c4-trial-plan": TrialPlan,
    "c4-trial-authorization": TrialAuthorization,
    "c4-ready-intent": ReadyTrialPlan,
    "c4-ready-receipt": ReadyReceipt,
    "c4-ready-approval": ReadyApproval,
    "c4-ready-activation": ReadyActivation,
}


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "schemas"
    destination.mkdir(exist_ok=True)
    for name, model in SCHEMAS.items():
        (destination / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
