"""신뢰된 연결 별칭에만 GET으로 접속하는 표준 라이브러리 전송부."""

from __future__ import annotations

import ssl
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from ai_ops.b_contracts import BRuntime, ReadRequest, ReadResponse


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPReadTransport:
    """환경 proxy·리다이렉트·압축을 끄고 CA 검증과 응답 읽기 상한을 적용한다.

    내부 API 전용이다. 모델 egress proxy는 이 연결부를 사용하지 않는다.
    실제 인증값은 매 요청마다 마운트 파일에서 읽어 회전을 반영한다.
    """

    def __init__(self, runtime: BRuntime):
        self.runtime = runtime

    def get(self, request: ReadRequest, *, timeout_seconds: float, max_bytes: int) -> ReadResponse:
        if max_bytes < 1 or timeout_seconds <= 0:
            raise ValueError("invalid read limits")
        connection = self.runtime.connections.get(request.connection)
        if connection is None or connection.base_url is None:
            raise ValueError("connection is not configured")
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if connection.token_file is not None:
            if not connection.base_url.startswith("https://"):
                raise ValueError("credentials require TLS")
            with Path(connection.token_file).open("rb") as stream:
                raw = stream.read(8193)
            if len(raw) > 8192:
                raise ValueError("credential file exceeds limit")
            token = raw.decode("ascii").rstrip("\r\n")
            if not token or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token):
                raise ValueError("invalid credential file")
            headers["Authorization"] = "Bearer " + token
        context = ssl.create_default_context(cafile=connection.ca_file)
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
        url = connection.base_url.rstrip("/") + request.path
        if request.params:
            url += "?" + urlencode(request.params)
        req = Request(url, headers=headers, method="GET")
        started = time.monotonic()
        try:
            response = opener.open(req, timeout=timeout_seconds)
        except HTTPError as error:
            # 오류 본문·Location·인증 도전값은 읽거나 감사에 저장하지 않는다.
            code = error.code
            error.close()
            return ReadResponse(code, b"")
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise TimeoutError("read deadline exceeded") from None
            raise ValueError("connection failed") from None
        with response:
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise ValueError("encoded response is not supported")
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdigit() or int(declared) > max_bytes):
                return ReadResponse(response.status, b"x" * (max_bytes + 1))
            body = bytearray()
            while len(body) <= max_bytes:
                if time.monotonic() - started >= timeout_seconds:
                    raise TimeoutError("read deadline exceeded")
                chunk = response.read1(min(8192, max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
            if declared is not None and len(body) <= max_bytes and len(body) != int(declared):
                raise ValueError("incomplete response")
            return ReadResponse(response.status, bytes(body))
