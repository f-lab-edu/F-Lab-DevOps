"""검증된 정상 CI 영수증을 승인된 v3 캠페인에만 한 번 전달한다."""

from __future__ import annotations

import hmac
import json
import re
import time

from ai_ops.normal_ci_inbox import NormalCIInbox


class NormalCIModelBridge:
    """전달 시도를 먼저 기록해 응답 유실·worker 재시작 뒤 자동 재전송을 막는다."""

    def __init__(self, inbox: NormalCIInbox, client, campaign_id: str, *, clock=time.time):
        if not re.fullmatch(r"campaign-[0-9a-f]{32}", campaign_id):
            raise ValueError("normal_ci_model_campaign_rejected")
        self.inbox = inbox
        self.client = client
        self.campaign_id = campaign_id
        self.clock = clock
        with inbox.queue.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS normal_ci_model_campaigns ("
                       "campaign_id TEXT PRIMARY KEY, activated_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS normal_ci_model_delivery ("
                       "episode_key TEXT NOT NULL, campaign_id TEXT NOT NULL, "
                       "state TEXT NOT NULL CHECK(state IN ('pending','submitted','needs_review')), "
                       "outbox_id TEXT, updated_at REAL NOT NULL, "
                       "PRIMARY KEY(episode_key,campaign_id))")
            db.execute("INSERT OR IGNORE INTO normal_ci_model_campaigns VALUES (?,?)",
                       (campaign_id, clock()))

    def process_one(self) -> dict | None:
        """활성화 이후 들어온 한 건만 보낸다. 실패한 요청은 자동 재시도하지 않는다."""
        with self.inbox.queue.transaction() as db:
            activated = db.execute("SELECT activated_at FROM normal_ci_model_campaigns "
                                   "WHERE campaign_id=?", (self.campaign_id,)).fetchone()[0]
            row = db.execute("SELECT r.* FROM normal_ci_receipts r LEFT JOIN "
                             "normal_ci_model_delivery d ON d.episode_key=r.episode_key "
                             "AND d.campaign_id=? WHERE r.state='ready' AND r.created_at>=? "
                             "AND d.episode_key IS NULL ORDER BY r.created_at,r.episode_key LIMIT 1",
                             (self.campaign_id, activated)).fetchone()
        if row is None:
            return None
        key = row["episode_key"]
        try:
            payload = json.loads(row["payload"])
            if (self.inbox._key(row["run_id"], row["attempt"]) != key
                    or not hmac.compare_digest(self.inbox._fingerprint(key, payload),
                                               row["fingerprint"])):
                raise ValueError("normal_ci_model_integrity_rejected")
            receipt = self.inbox._validate(payload)
            if row["summary"] is None:
                raise ValueError("normal_ci_model_rules_required")
            campaign = self.client.campaign(self.campaign_id)
            finished = receipt.finished_at.timestamp()
            if campaign["state"] != "approved" or not self.clock() < campaign["expires"]:
                return None
            if (type(campaign["approved_at"]) not in (int, float) or
                    not campaign["approved_at"] <= finished < campaign["expires"]):
                raise ValueError("normal_ci_model_preapproval_record_rejected")
        except (ValueError, TypeError, KeyError, AttributeError):
            with self.inbox.queue.transaction() as db:
                db.execute("INSERT OR IGNORE INTO normal_ci_model_delivery VALUES (?,?,?,NULL,?)",
                           (key, self.campaign_id, "needs_review", self.clock()))
            return {"episode_key": key, "state": "needs_review"}
        with self.inbox.queue.transaction() as db:
            inserted = db.execute("INSERT OR IGNORE INTO normal_ci_model_delivery "
                                  "VALUES (?,?,'pending',NULL,?)",
                                  (key, self.campaign_id, self.clock())).rowcount
        if not inserted:
            return None
        try:
            outbox_id = self.client.enqueue_normal_receipt(self.campaign_id, receipt,
                                                            self.inbox.binding)
            if not re.fullmatch(r"outbox-[0-9a-f]{64}", outbox_id):
                raise ValueError("normal_ci_model_outbox_rejected")
        except Exception:
            # 서버가 수락하고 응답만 유실됐을 수도 있으므로 전송을 재시도하지 않는다.
            with self.inbox.queue.transaction() as db:
                db.execute("UPDATE normal_ci_model_delivery SET state='needs_review',updated_at=? "
                           "WHERE episode_key=? AND campaign_id=? AND state='pending'",
                           (self.clock(), key, self.campaign_id))
            return {"episode_key": key, "state": "needs_review"}
        with self.inbox.queue.transaction() as db:
            db.execute("UPDATE normal_ci_model_delivery SET state='submitted',outbox_id=?,updated_at=? "
                       "WHERE episode_key=? AND campaign_id=? AND state='pending'",
                       (outbox_id, self.clock(), key, self.campaign_id))
        return {"episode_key": key, "state": "submitted", "outbox_id": outbox_id}

    def status(self, episode_key: str) -> dict | None:
        with self.inbox.queue.transaction() as db:
            row = db.execute("SELECT state,outbox_id FROM normal_ci_model_delivery "
                             "WHERE episode_key=? AND campaign_id=?",
                             (episode_key, self.campaign_id)).fetchone()
        return dict(row) if row else None
