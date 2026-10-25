"""캠페인에 고정할 v1·v2 계약과 프롬프트 함수를 명시적으로 선택한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ai_ops.model_ci_projection import (CIModelView, CIProjection, CI_POLICY,
    parse_ci_projection)
from ai_ops.model_projection import ModelView, POLICY, Projection, parse_projection
from ai_ops.projected_ci_model_engine import (CI_PROMPT_VERSION, request_ci_templates,
    validate_ci_report)
from ai_ops.projected_model_engine import (PROMPT_VERSION, request_templates,
    validate_projected_report)


@dataclass(frozen=True)
class PolicySpec:
    policy: str
    prompt: str
    view_type: type[ModelView]
    projection_type: type[Projection]
    parse_projection: Callable
    request_templates: Callable
    validate_report: Callable


def policy_spec(policy: str) -> PolicySpec:
    """알 수 없는 버전은 기본 v1로 낮추지 않고 거부한다."""
    if policy == POLICY:
        return PolicySpec(POLICY, PROMPT_VERSION, ModelView, Projection,
                          parse_projection, request_templates, validate_projected_report)
    if policy == CI_POLICY:
        return PolicySpec(CI_POLICY, CI_PROMPT_VERSION, CIModelView, CIProjection,
                          parse_ci_projection, request_ci_templates, validate_ci_report)
    raise ValueError("model_policy_unsupported")
