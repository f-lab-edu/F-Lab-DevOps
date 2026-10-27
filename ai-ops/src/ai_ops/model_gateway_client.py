"""실습 worker가 내부 gateway에 정제 입력만 제출하는 고정 목적지 클라이언트다."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import re

from ai_ops.contracts import canonical_json_bytes
from ai_ops.model_gateway_contract import (CIModelSubmission, ModelSubmission,
                                           SequenceModelSubmission, sign_submission)
from ai_ops.model_ci_projection import CIProjection
from ai_ops.model_projection import Projection
from ai_ops.model_sequence_projection import SequenceProjection


HOST = "model-gateway.ai-model.svc.cluster.local"
PORT = 8081
KEY_PATH = Path("/etc/ai-model-client/token")


class ModelGatewayClient:
    """고정 클러스터 서비스·짧은 제한 시간·응답 크기로 보조 모델 경로를 격리한다."""

    def __init__(self, credential_path: Path = KEY_PATH):
        self.credential_path = credential_path

    def _request(self, method: str, path: str, value: dict | None = None) -> dict:
        credential = self.credential_path.read_bytes().strip()
        if not 32 <= len(credential) <= 256 or any(c < 33 or c > 126 for c in credential):
            raise ValueError("model_gateway_client_credential_rejected")
        raw = canonical_json_bytes(value) if value is not None else None
        headers = {"Authorization": "Bearer " + credential.decode("ascii"),
                   "Accept": "application/json"}
        if raw is not None:
            headers.update({"Content-Type": "application/json",
                            "X-Model-Signature": sign_submission(raw, credential)})
        connection = http.client.HTTPConnection(HOST, PORT, timeout=5)
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            if response.status != 200 or response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("model_gateway_response_rejected")
            size = response.getheader("Content-Length")
            if size is None or not size.isdigit() or not 0 < int(size) <= 4096:
                raise ValueError("model_gateway_response_size_rejected")
            body = response.read(int(size) + 1)
            if len(body) != int(size):
                raise ValueError("model_gateway_response_size_rejected")
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError("model_gateway_response_contract_rejected")
            return result
        except Exception:
            # 요청·응답 본문이나 자격을 worker 로그에 전달하지 않는다.
            raise ValueError("model_gateway_unavailable") from None
        finally:
            connection.close()

    def campaign(self, identifier: str) -> dict:
        if not re.fullmatch(r"campaign-[0-9a-f]{32}", identifier):
            raise ValueError("model_gateway_campaign_rejected")
        value = self._request("GET", f"/v1/campaign/{identifier}")
        if set(value) != {"state", "created", "expires"}:
            raise ValueError("model_gateway_campaign_contract")
        return value

    def has_source(self, campaign_id: str, source_run: str) -> bool:
        if not re.fullmatch(r"campaign-[0-9a-f]{32}", campaign_id) or not re.fullmatch(r"run-[0-9a-f]{32}", source_run):
            raise ValueError("model_gateway_source_rejected")
        value = self._request("GET", f"/v1/source/{campaign_id}/{source_run}")
        if set(value) != {"exists"} or type(value["exists"]) is not bool:
            raise ValueError("model_gateway_source_contract")
        return value["exists"]

    def has_rejection(self, campaign_id: str, job_id: str) -> bool:
        if not re.fullmatch(r"campaign-[0-9a-f]{32}", campaign_id) or not re.fullmatch(r"job-[0-9a-f]{32}", job_id):
            raise ValueError("model_gateway_rejection_rejected")
        value = self._request("GET", f"/v1/rejection/{campaign_id}/{job_id}")
        if set(value) != {"exists"} or type(value["exists"]) is not bool:
            raise ValueError("model_gateway_rejection_contract")
        return value["exists"]

    def reject_projection(self, campaign_id: str, job_id: str, source_run: str,
                          reason: str = "projection_rejected"):
        if (not re.fullmatch(r"campaign-[0-9a-f]{32}", campaign_id) or
                not re.fullmatch(r"job-[0-9a-f]{32}", job_id) or
                not (re.fullmatch(r"run-[0-9a-f]{32}", source_run) or source_run == "unknown") or
                reason not in {"projection_rejected", "integrity_rejected"}):
            raise ValueError("model_gateway_rejection_rejected")
        value = self._request("POST", "/v1/rejection", {"campaign_id": campaign_id,
            "job_id": job_id, "source_run": source_run, "reason": reason})
        if value != {"recorded": True}:
            raise ValueError("model_gateway_rejection_contract")

    def enqueue(self, campaign_id: str, projection: Projection | CIProjection | SequenceProjection, *, source_run: str,
                rules_ref: str) -> str:
        contract = (CIModelSubmission if type(projection) is CIProjection else
                    SequenceModelSubmission if type(projection) is SequenceProjection else ModelSubmission)
        submission = contract.from_projection(campaign_id, projection, rules_ref=rules_ref)
        if submission.source_run != source_run:
            raise ValueError("model_gateway_source_rejected")
        value = self._request("POST", "/v1/submit", submission.model_dump(mode="json"))
        if set(value) != {"outbox_id"} or not re.fullmatch(r"outbox-[0-9a-f]{64}", value["outbox_id"]):
            raise ValueError("model_gateway_outbox_contract")
        return value["outbox_id"]
