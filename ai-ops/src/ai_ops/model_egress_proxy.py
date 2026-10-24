"""고정 OpenAI TLS 목적지만 중계하는 실습 전용 CONNECT proxy다."""

from __future__ import annotations

import ipaddress
import select
import socket
import socketserver


TARGET = "api.openai.com:443"
MAX_HEADER = 4096


def parse_connect(raw: bytes) -> None:
    """CONNECT 한 줄과 Host만 허용하고 다른 목적지·헤더를 거부한다."""
    if len(raw) > MAX_HEADER or not raw.endswith(b"\r\n\r\n"):
        raise ValueError("proxy_header_rejected")
    try:
        lines = raw[:-4].decode("ascii").split("\r\n")
    except UnicodeDecodeError:
        raise ValueError("proxy_header_rejected") from None
    if lines[0] != f"CONNECT {TARGET} HTTP/1.1" or len(lines) > 3:
        raise ValueError("proxy_target_rejected")
    headers = {}
    for line in lines[1:]:
        if ":" not in line:
            raise ValueError("proxy_header_rejected")
        key, value = line.split(":", 1)
        key = key.lower()
        if key != "host" or key in headers or value.strip().lower() != TARGET:
            raise ValueError("proxy_header_rejected")
        headers[key] = value
    if headers.keys() != {"host"}:
        raise ValueError("proxy_header_rejected")


def public_addresses(resolver=socket.getaddrinfo) -> list[tuple]:
    """DNS 응답 전체가 공개 주소일 때만 선택한다. 사설 응답 혼입도 거부한다."""
    answers = resolver("api.openai.com", 443, family=socket.AF_INET,
                       type=socket.SOCK_STREAM)
    if not answers:
        raise ValueError("proxy_dns_empty")
    for family, _kind, _proto, _canon, sockaddr in answers:
        address = ipaddress.ip_address(sockaddr[0])
        if family != socket.AF_INET or not address.is_global:
            raise ValueError("proxy_dns_rejected")
    return answers


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(5)
        raw = bytearray()
        try:
            while not raw.endswith(b"\r\n\r\n") and len(raw) <= MAX_HEADER:
                part = client.recv(1)
                if not part:
                    return
                raw.extend(part)
            parse_connect(bytes(raw))
            addresses = public_addresses()
            family, kind, proto, _canon, sockaddr = addresses[0]
            with socket.socket(family, kind, proto) as upstream:
                upstream.settimeout(5)
                upstream.connect(sockaddr)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                client.settimeout(None)
                upstream.settimeout(None)
                sockets = (client, upstream)
                while True:
                    readable, _, _ = select.select(sockets, [], [], 35)
                    if not readable:
                        break
                    for current in readable:
                        data = current.recv(16384)
                        if not data:
                            return
                        (upstream if current is client else client).sendall(data)
        except (OSError, ValueError):
            # 요청·TLS·응답 원문을 기록하지 않는다.
            try:
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    with ProxyServer(("0.0.0.0", 8080), _Handler) as server:
        server.serve_forever(poll_interval=1)


if __name__ == "__main__":
    main()
