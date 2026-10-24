"""실습 정제 입력만 검증하는 기본 비활성 gateway 진입점이다."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os

from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.model_projection import ModelView


MAX_REQUEST = 32768


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
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        # M6의 gateway는 실제 전송 기능이 없다. M7 활성화 작업에서만 변경한다.
        if self.path != "/v1/analyze":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "-1"))
            if not 0 < size <= MAX_REQUEST:
                raise ValueError("gateway_request_size_rejected")
            with open("/etc/ai-model-client/token", "rb") as file:
                credential = file.read(257).strip()
            validate_request(self.rfile.read(size), self.headers.get("Authorization", ""), credential)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.send_error(400)
            return
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format, *_args):
        # 인증 헤더·입력 본문이 서버 로그에 남지 않도록 한다.
        return


def main():
    if os.environ.get("AI_MODEL_ENABLED") != "false":
        raise ValueError("gateway_not_activated")
    with ThreadingHTTPServer(("0.0.0.0", 8081), _Handler) as server:
        server.serve_forever(poll_interval=1)


if __name__ == "__main__":
    main()
