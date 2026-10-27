"""실습 정제 입력만 검증하는 기본 비활성 gateway 진입점이다."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import re
from pathlib import Path
import signal
import threading
import time

from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.model_gateway_contract import MAX_SUBMISSION, verify_submission, sign_submission
from ai_ops.model_projection import ModelView


MAX_REQUEST = 32768
CAMPAIGN = r"campaign-[0-9a-f]{32}"
RUN = r"run-[0-9a-f]{32}"
JOB = r"job-[0-9a-f]{32}"


def validate_request(raw: bytes, authorization: str, credential: bytes) -> ModelView:
    """키를 확인한 뒤 정제 view와 그 해시만 받는다. 원본 필드는 거부한다."""
    if not 0 < len(raw) <= MAX_REQUEST or not 32 <= len(credential) <= 256:
        raise ValueError("gateway_request_size_or_credential")
    if not authorization.startswith("Bearer ") or not hmac.compare_digest(
            authorization[7:].encode("ascii", errors="ignore"), credential):
        raise ValueError("gateway_auth_rejected")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {"view", "view_hash"}:
        raise ValueError("gateway_contract_rejected")
    view = ModelView.model_validate(data["view"])
    if sha256_json(view.model_dump(mode="json")) != data["view_hash"]:
        raise ValueError("gateway_hash_rejected")
    if len(canonical_json_bytes(view.model_dump(mode="json"))) > 32000:
        raise ValueError("gateway_view_size_rejected")
    return view


class _Handler(BaseHTTPRequestHandler):
    def _credential(self):
        with open("/etc/ai-model-client/token", "rb") as stream:
            return stream.read(257).strip()

    def _json(self, status: int, value: dict):
        body = canonical_json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, credential: bytes):
        header = self.headers.get("Authorization", "")
        return (header.startswith("Bearer ") and
                hmac.compare_digest(header[7:].encode("ascii", errors="ignore"), credential))

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        store = getattr(self.server, "model_store", None)
        allowed = getattr(self.server, "model_campaign_id", None)
        if store is None or allowed is None:
            self.send_error(503)
            return
        try:
            if not self._authorized(self._credential()):
                raise ValueError("gateway_auth_rejected")
            campaign = re.fullmatch(rf"/v1/campaign/({CAMPAIGN})", self.path)
            source = re.fullmatch(rf"/v1/source/({CAMPAIGN})/({RUN})", self.path)
            rejection = re.fullmatch(rf"/v1/rejection/({CAMPAIGN})/({JOB})", self.path)
            if campaign:
                if campaign[1] != allowed:
                    raise ValueError("gateway_campaign_scope")
                value = store.campaign(campaign[1])
                self._json(200, {"state": value["state"], "created": value["created"],
                                 "expires": value["expires"],
                                 "approved_at": value["approved_at"]})
            elif source:
                if source[1] != allowed:
                    raise ValueError("gateway_campaign_scope")
                self._json(200, {"exists": store.has_source(source[1], source[2])})
            elif rejection:
                if rejection[1] != allowed:
                    raise ValueError("gateway_campaign_scope")
                self._json(200, {"exists": store.has_rejection(rejection[1], rejection[2])})
            else:
                self.send_error(404)
        except (OSError, ValueError, TypeError):
            self.send_error(403)

    def do_POST(self):
        store = getattr(self.server, "model_store", None)
        allowed = getattr(self.server, "model_campaign_id", None)
        if self.path not in {"/v1/analyze", "/v1/submit", "/v1/rejection"}:
            self.send_error(404)
            return
        if self.path != "/v1/analyze" and (store is None or allowed is None):
            self.send_error(503)
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
            if not 0 < size <= min(MAX_REQUEST, MAX_SUBMISSION):
                raise ValueError("gateway_request_size_rejected")
            credential = self._credential()
            raw = self.rfile.read(size)
            if self.path == "/v1/analyze":
                validate_request(raw, self.headers.get("Authorization", ""), credential)
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            signature = self.headers.get("X-Model-Signature", "")
            if self.path == "/v1/submit":
                submission = verify_submission(raw, self.headers.get("Authorization", ""),
                                               signature, credential)
                if submission.campaign_id != allowed:
                    raise ValueError("gateway_campaign_scope")
                identifier = store.enqueue_submission(submission)
                self._json(200, {"outbox_id": identifier})
                return
            if not self._authorized(credential) or not hmac.compare_digest(
                    signature, sign_submission(raw, credential)):
                raise ValueError("gateway_auth_rejected")
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) !=
                    {"campaign_id", "job_id", "source_run", "reason"} or
                    canonical_json_bytes(value) != raw or
                    not re.fullmatch(CAMPAIGN, value["campaign_id"]) or
                    value["campaign_id"] != allowed or
                    not re.fullmatch(JOB, value["job_id"]) or
                    not (re.fullmatch(RUN, value["source_run"]) or value["source_run"] == "unknown")):
                raise ValueError("gateway_rejection_contract")
            store.reject_projection(value["campaign_id"], value["job_id"],
                                    value["source_run"], value["reason"])
            self._json(200, {"recorded": True})
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.send_error(400)
            return

    def log_message(self, _format, *_args):
        # 인증 헤더·입력 본문이 서버 로그에 남지 않도록 한다.
        return


def main():
    enabled = os.environ.get("AI_MODEL_ENABLED", "false")
    if enabled not in {"false", "true"}:
        raise ValueError("gateway_activation_value_rejected")
    if enabled == "false":
        with ThreadingHTTPServer(("0.0.0.0", 8081), _Handler) as server:
            server.serve_forever(poll_interval=1)
        return
    from ai_ops.continuous_model import ContinuousModelStore
    from ai_ops.model_live_probe import ModelLiveProbe
    from ai_ops.model_runtime import ModelGatewayRuntime
    from ai_ops.path_policy import permitted_path
    campaign_id = os.environ["AI_MODEL_CAMPAIGN_ID"]
    image_digest = os.environ["AI_MODEL_IMAGE_DIGEST"]
    cluster_fingerprint = os.environ["AI_MODEL_CLUSTER_FINGERPRINT"]
    ca_sha256 = os.environ["AI_MODEL_CA_SHA256"]
    if not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in
               (cluster_fingerprint, ca_sha256)):
        raise ValueError("gateway_cluster_binding_rejected")
    store = ContinuousModelStore(permitted_path(Path("/data/incidents/model-ledger"), "store"))
    probe = ModelLiveProbe(expected_cluster_fingerprint=cluster_fingerprint,
                           expected_ca_sha256=ca_sha256)
    runtime = ModelGatewayRuntime(store, campaign_id, image_digest, probe)
    runtime.verify()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    with ThreadingHTTPServer(("0.0.0.0", 8081), _Handler) as server:
        server.model_store = store
        server.model_campaign_id = campaign_id
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 1}, daemon=True)
        thread.start()
        last_prune = 0.0
        try:
            while not stopped.wait(2):
                campaign = store.campaign(campaign_id)
                if campaign["state"] != "approved" or store._now() >= campaign["expires"]:
                    break
                runtime.run_once()
                if time.monotonic() - last_prune >= 3600:
                    store.prune()
                    last_prune = time.monotonic()
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
