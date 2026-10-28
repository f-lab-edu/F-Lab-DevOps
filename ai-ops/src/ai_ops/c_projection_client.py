"""운영자 로컬 서비스가 TLS로 제한된 사건 요약만 읽는 전송 계층."""

from __future__ import annotations

import http.client
import os
from pathlib import Path
import re
import socket
import ssl
import stat

from ai_ops.c_recovery import IncidentProjection


SERVER_NAME = "ci-worker.ai-ci.svc"
ID = re.compile(r"(?:inc|run)-[0-9a-f]{32}\Z")


def _credential(path: Path, *, secret: bool) -> bytes:
    """개인 파일에서 토큰·CA만 읽고 링크·과도한 크기·공유 쓰기 권한을 거부한다."""
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & (0o077 if secret else 0o022)
            or not 1 <= info.st_size <= (256 if secret else 65536)):
        raise ValueError("사건 조회 자격 파일이 안전하지 않습니다")
    return path.read_bytes()


class ProjectionClient:
    """별도 port-forward를 TLS 전송으로만 사용하고 서버 인증·응답 계약을 검증한다."""

    def __init__(self, token_file: Path, ca_file: Path, *, port: int = 9444):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("사건 조회 로컬 포트가 유효하지 않습니다")
        self.token_file = token_file
        self.ca_file = ca_file
        self.port = port

    def fetch(self, incident_id: str, run_id: str) -> IncidentProjection:
        if (not isinstance(incident_id, str) or not incident_id.startswith("inc-")
                or not ID.fullmatch(incident_id) or not isinstance(run_id, str)
                or not run_id.startswith("run-") or not ID.fullmatch(run_id)):
            raise ValueError("사건·실행 ID가 유효하지 않습니다")
        token = _credential(self.token_file, secret=True).decode("ascii").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError("사건 조회 인증값이 유효하지 않습니다")
        ca = _credential(self.ca_file, secret=False).decode("ascii")
        context = ssl.create_default_context(cadata=ca)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        path = f"/v1/incidents/{incident_id}/runs/{run_id}"
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname=SERVER_NAME) as secure:
                secure.settimeout(5)
                request = (f"GET {path} HTTP/1.0\r\nHost: {SERVER_NAME}\r\n"
                           f"Authorization: Bearer {token}\r\nConnection: close\r\n\r\n")
                secure.sendall(request.encode("ascii"))
                response = http.client.HTTPResponse(secure)
                response.begin()
                if (response.status != 200 or response.getheader("Content-Type") != "application/json"
                        or response.getheader("Transfer-Encoding") is not None):
                    raise ValueError("사건 조회 응답을 거부했습니다")
                length = response.getheader("Content-Length")
                if length is None or not re.fullmatch(r"[1-9][0-9]{0,3}", length) or int(length) > 4096:
                    raise ValueError("사건 조회 응답 크기가 유효하지 않습니다")
                raw = response.read(4097)
        if len(raw) != int(length):
            raise ValueError("사건 조회 응답 길이가 일치하지 않습니다")
        result = IncidentProjection.model_validate_json(raw)
        if result.incident_id != incident_id or result.run_id != run_id:
            raise ValueError("사건 조회 응답 대상이 일치하지 않습니다")
        return result
