"""실습 모델 캠페인의 비활성 검토 자료와 로컬 자격 지문을 만든다."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from ai_ops.continuous_model import (CAMPAIGN_SECONDS, DAILY_CENTS, MAX_CASES,
                                     RESERVE_CENTS, implementation_hash)
from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.model_projection import ModelView, TARGET
from ai_ops.model_policy import policy_spec
from ai_ops.projected_model_engine import CallPlan


def private_credential_fingerprint(path: Path, *, kind: str) -> str:
    """소유자 전용 일반 파일의 값은 반환하지 않고 SHA-256 지문만 반환한다."""
    bounds = {"api": (20, 512), "client": (32, 256)}
    if kind not in bounds:
        raise ValueError("campaign_credential_kind_rejected")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
            raise ValueError("campaign_credential_permissions")
        raw = os.read(fd, bounds[kind][1] + 1)
        if (not bounds[kind][0] <= len(raw) <= bounds[kind][1] or
                any(value < 33 or value > 126 for value in raw)):
            raise ValueError("campaign_credential_format")
        return hashlib.sha256(raw).hexdigest()
    finally:
        os.close(fd)


def build_review_packet(campaign: dict, sample_view: ModelView, *,
                        api_fingerprint: str, client_fingerprint: str,
                        clock=time.time) -> dict:
    """실제 승인 전 확인할 대상·입력·전송·비용을 고정한 자료를 반환한다."""
    binding = campaign["binding"]
    selected = policy_spec(binding["policy"])
    now = clock()
    if (campaign["state"] != "prepared" or
            not campaign["created"] <= now < campaign["expires"] or
            campaign["binding_hash"] != sha256_json(binding) or
            binding["implementation_hash"] != implementation_hash() or
            binding["target"] != TARGET or binding["prompt"] != selected.prompt or
            binding["limit"] != {"seconds": CAMPAIGN_SECONDS, "cases": MAX_CASES,
                                 "case_cents": RESERVE_CENTS, "utc_day_cents": DAILY_CENTS}):
        raise ValueError("campaign_review_scope_changed")
    plan = CallPlan.model_validate(binding["plan"])
    if (plan.execution_mode != "live" or plan.credential_fingerprint != api_fingerprint or
            not re.fullmatch(r"[0-9a-f]{64}", client_fingerprint)):
        raise ValueError("campaign_review_credential_changed")
    checked = datetime.fromisoformat(plan.price.checked_on).date()
    if (datetime.fromtimestamp(now, timezone.utc).date() - checked).days not in range(0, 8):
        raise ValueError("campaign_review_price_expired")
    view = selected.view_type.model_validate(sample_view)
    if binding["sample_view_hash"] != sha256_json(view.model_dump(mode="json")):
        raise ValueError("campaign_review_sample_changed")
    templates = selected.request_templates(view, plan)
    return {"campaign_id": campaign["id"], "binding_hash": campaign["binding_hash"],
            "state": "prepared", "activation_allowed": False,
            "created_utc": datetime.fromtimestamp(campaign["created"], timezone.utc).isoformat(),
            "expires_utc": datetime.fromtimestamp(campaign["expires"], timezone.utc).isoformat(),
            "target": binding["target"], "deployment_uid": binding["deployment_uid"],
            "config_hash": binding["config_hash"],
            "cluster_fingerprint": binding["cluster_fingerprint"],
            "image_digest": binding["image_digest"],
            "implementation_hash": binding["implementation_hash"],
            "sample_view_hash": binding["sample_view_hash"],
            "api_key_sha256": api_fingerprint, "client_token_sha256": client_fingerprint,
            "plan": plan.model_dump(mode="json"), "limit": binding["limit"],
            "automatic_future_inputs": True,
            "requests": {path: {"sha256": sha256_json(body), "bytes": len(canonical_json_bytes(body)),
                                "body": body}
                         for path, body in templates.items()},
            "sample_view": view.model_dump(mode="json"),
            "human_review": ["대상·정제 입력·두 API 요청·미래 자동 입력 범위 확인",
                             "현재 가격·선택적 데이터 공유 해제·제공자 보관 조건 확인",
                             "24시간·10건·건당 $0.10·UTC 일일 $1 한도와 중지 절차 확인",
                             "별도 명시 승인 후에만 캠페인 승인과 배포 실행"]}
