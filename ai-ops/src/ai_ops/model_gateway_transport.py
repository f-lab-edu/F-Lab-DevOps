"""클러스터 Secret과 고정 CONNECT proxy를 사용하는 전용 TLS 전송부다."""

from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import ssl
import time

from ai_ops.contracts import canonical_json_bytes
from ai_ops.projected_model_transport import PATHS


KEY_PATH = Path("/etc/ai-model-key/token")
PROXY_HOST = "model-egress-proxy.ai-model.svc.cluster.local"


def read_cluster_credential(path: Path = KEY_PATH) -> str:
    """전용 Secret 파일만 읽고 키 값은 오류·로그에 포함하지 않는다."""
    raw = path.read_bytes()
    if not 20 <= len(raw) <= 512 or any(c < 33 or c > 126 for c in raw):
        raise ValueError("gateway_credential_rejected")
    return raw.decode("ascii")


class _TunnelConnection(http.client.HTTPConnection):
    """proxy에 평문 CONNECT만 보낸 뒤 목적지와 종단 TLS를 수립한다."""

    def __init__(self, *, timeout: float):
        super().__init__(PROXY_HOST, 8080, timeout=timeout)
        self.set_tunnel("api.openai.com", 443, headers={"Host": "api.openai.com:443"})

    def connect(self):
        super().connect()
        self.sock = ssl.create_default_context().wrap_socket(
            self.sock, server_hostname="api.openai.com")


class GatewayOpenAITransport:
    """승인·예산을 상위에서 확정한 뒤에만 호출되는 단회 전송 객체다."""

    comparison_mode = "live"

    def __init__(self, credential_sha256: str, *, before_request=None):
        self.expected_credential = credential_sha256
        self.before_request = before_request
        self.attempts = {path: 0 for path in PATHS}

    def __call__(self, path: str, body: dict, *, timeout: float):
        if path not in PATHS or not 0 < timeout <= 30 or self.attempts[path] != 0:
            raise ValueError("gateway_transport_scope_rejected")
        encoded = canonical_json_bytes(body)
        if len(encoded) > 32000:
            raise ValueError("gateway_transport_size_rejected")
        if self.before_request is not None:
            self.before_request()
        key = read_cluster_credential()
        if hashlib.sha256(key.encode()).hexdigest() != self.expected_credential:
            raise ValueError("gateway_credential_changed")
        self.attempts[path] += 1
        started = time.monotonic()
        connection = _TunnelConnection(timeout=timeout)
        try:
            connection.request("POST", path, body=encoded, headers={
                "Host": "api.openai.com", "Authorization": "Bearer " + key,
                "Content-Type": "application/json", "Accept": "application/json",
                "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status != 200 or response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("gateway_http_rejected")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > 262144):
                raise ValueError("gateway_response_size_rejected")
            raw = bytearray()
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("gateway_deadline")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(8192, 262145 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise ValueError("gateway_response_size_rejected")
            if length is not None and len(raw) != int(length):
                raise ValueError("gateway_response_length_rejected")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("gateway_response_object_required")
            return result
        except Exception:
            # 응답·키·redirect 위치·외부 오류를 상위에 노출하지 않는다.
            raise ValueError("gateway_http_request_failed") from None
        finally:
            connection.close()
