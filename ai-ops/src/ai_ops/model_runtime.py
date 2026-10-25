"""승인된 실습 캠페인에서 대기 입력을 순서대로 한 번만 처리한다."""

from __future__ import annotations

import re

from ai_ops.continuous_model import ContinuousModelStore, implementation_hash
from ai_ops.model_gateway_transport import GatewayOpenAITransport, read_cluster_credential
from ai_ops.model_live_probe import ModelLiveProbe
from ai_ops.model_policy import policy_spec
from ai_ops.projected_model_engine import CallPlan, ProjectionModelFailure


class ModelGatewayRuntime:
    """매 건 전송 전에 승인·현재 대상·키·이미지 일치를 다시 확인한다."""

    def __init__(self, store: ContinuousModelStore, campaign_id: str, image_digest: str,
                 probe: ModelLiveProbe, *, transport_factory=GatewayOpenAITransport):
        if (not re.fullmatch(r"campaign-[0-9a-f]{32}", campaign_id) or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest)):
            raise ValueError("model_runtime_scope_rejected")
        self.store = store
        self.campaign_id = campaign_id
        self.image_digest = image_digest
        self.probe = probe
        self.transport_factory = transport_factory

    def verify(self) -> CallPlan:
        campaign = self.store.campaign(self.campaign_id)
        binding = campaign["binding"]
        selected = policy_spec(binding["policy"])
        if (campaign["state"] != "approved" or
                not campaign["created"] <= self.store._now() < campaign["expires"] or
                binding["image_digest"] != self.image_digest or
                binding["implementation_hash"] != implementation_hash() or
                binding["prompt"] != selected.prompt):
            raise ValueError("model_runtime_campaign_unavailable")
        plan = self.store._live_plan(CallPlan.model_validate(binding["plan"]))
        from hashlib import sha256
        if sha256(read_cluster_credential().encode()).hexdigest() != plan.credential_fingerprint:
            raise ValueError("model_runtime_credential_changed")
        self.probe.verify(binding)
        return plan

    def run_once(self) -> str | None:
        """미전송 대기 건 하나를 처리한다. 불명 전송·보류는 재전송하지 않는다."""
        identifier = self.store.next_prepared(self.campaign_id)
        if identifier is None:
            return None
        plan = self.verify()
        transport = self.transport_factory(
            plan.credential_fingerprint, before_request=self._before_request)
        self.store.process_live(identifier, transport, image_digest=self.image_digest)
        return identifier

    def _before_request(self):
        """중지·만료 후 토큰 계산이나 생성의 다음 HTTP 호출을 시작하지 않는다."""
        campaign = self.store.campaign(self.campaign_id)
        if (campaign["state"] != "approved" or
                not campaign["created"] <= self.store._now() < campaign["expires"]):
            raise ProjectionModelFailure("campaign_stopped")
