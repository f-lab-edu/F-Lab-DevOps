"""두 고정 API 경로만 지원하는 TLS 전송부. 승인 뒤 키를 읽고 재시도하지 않는다."""

import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import stat
import time

from ai_ops.contracts import canonical_json_bytes
from ai_ops.path_policy import PERSONAL_ROOT


KEY_FILE = PERSONAL_ROOT / ".openai-api-key"
PATHS = frozenset({"/v1/responses/input_tokens", "/v1/responses"})


def read_credential(path=KEY_FILE):
    """기존 개인 키 파일만 읽으며 환경 변수나 다른 자격으로 우회하지 않는다."""
    path = Path(path)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("projection_credential_path_rejected")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or not 20 <= info.st_size <= 512):
            raise ValueError("projection_credential_file_rejected")
        raw = f.read(513)
    if not 20 <= len(raw) <= 512 or any(c < 33 or c > 126 for c in raw):
        raise ValueError("projection_credential_format_rejected")
    return raw.decode("ascii")


def credential_fingerprint():
    """사용자가 미리 본 계정 자격과 호출 시점의 자격이 같은지 해시로 결합한다."""
    return hashlib.sha256(read_credential().encode()).hexdigest()


class LiveOpenAITransport:
    comparison_mode = "live"

    def __init__(self, credential_sha256):
        self.expected_credential = credential_sha256
        self.attempts = {path: 0 for path in PATHS}

    def __call__(self, path, body, *, timeout):
        # HTTPConnection은 환경 proxy·redirect·자동 재시도를 사용하지 않는다.
        if path not in PATHS or not 0 < timeout <= 30 or self.attempts[path] != 0:
            raise ValueError("projection_transport_scope_rejected")
        encoded = canonical_json_bytes(body)
        if len(encoded) > 32000:
            raise ValueError("projection_transport_size_rejected")
        key = read_credential()
        if hashlib.sha256(key.encode()).hexdigest() != self.expected_credential:
            raise ValueError("projection_credential_changed")
        self.attempts[path] += 1
        started = time.monotonic()
        connection = http.client.HTTPSConnection("api.openai.com", 443, timeout=timeout,
                                                 context=ssl.create_default_context())
        try:
            connection.request("POST", path, body=encoded,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                         "Accept": "application/json", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("projection_http_" + str(response.status))
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise ValueError("projection_encoded_response_rejected")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > 262144):
                raise ValueError("projection_response_size_rejected")
            raw = bytearray()
            while True:
                remaining = timeout-(time.monotonic()-started)
                if remaining <= 0:
                    raise TimeoutError("projection_transport_deadline")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(8192, 262145-len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise ValueError("projection_response_size_rejected")
            if length is not None and len(raw) != int(length):
                raise ValueError("projection_response_length_rejected")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("projection_response_object_required")
            return result
        except Exception:
            # 키·응답 원문·Location·인증 오류 본문은 오류 기록에 포함하지 않는다.
            raise ValueError("projection_http_request_failed") from None
        finally:
            connection.close()
