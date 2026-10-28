"""실습 CI worker PVC에서 원문 없는 사건 요약만 읽는 TLS 서버."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import threading
import time

from ai_ops.alert_receiver import read_token, tls_context
from ai_ops.c_recovery import read_incident_projection
from ai_ops.contracts import canonical_json_bytes
from ai_ops.path_policy import cli_paths


class ProjectionServer(HTTPServer):
    """별도 인증값·TLS·30회/분으로 실습 사건 요약만 반환한다."""

    request_queue_size = 8
    allow_reuse_address = True

    def __init__(self, store: Path, identity: Path, *, address=('0.0.0.0', 9444)):
        self.store = store
        self.identity = identity
        read_token(identity)
        self.tls = tls_context(identity)
        self.window = time.monotonic()
        self.calls = 0
        super().__init__(address, ProjectionHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        try:
            return self.tls.wrap_socket(connection, server_side=True), address
        except BaseException:
            connection.close()
            raise

    def allowed(self):
        now = time.monotonic()
        if now - self.window >= 60:
            self.window, self.calls = now, 0
        self.calls += 1
        return self.calls <= 30

    def handle_error(self, *_):
        # 원본 요청 경로나 저장 오류를 출력하지 않는다.
        pass


class ProjectionHandler(BaseHTTPRequestHandler):
    """ID 두 개와 인증을 검증하고 민감한 보고서 본문 없이 응답한다."""

    protocol_version = 'HTTP/1.0'

    def log_message(self, *_):
        pass

    def reply(self, code):
        payload = json.dumps({'error': 'rejected'}, separators=(',', ':')).encode('ascii')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(payload)

    def send_error(self, code, *_):
        self.reply(code)

    def do_GET(self):
        match = re.fullmatch(r'/v1/incidents/(inc-[0-9a-f]{32})/runs/(run-[0-9a-f]{32})', self.path)
        if match is None:
            self.reply(404)
            return
        try:
            authorizations = self.headers.get_all('Authorization', [])
            expected = 'Bearer ' + read_token(self.server.identity)
            if (len(authorizations) != 1 or len(authorizations[0]) > 300
                    or not hmac.compare_digest(authorizations[0].encode('utf-8'), expected.encode('ascii'))):
                self.reply(401)
                return
            if self.headers.get_all('Content-Length') or self.headers.get_all('Transfer-Encoding'):
                self.reply(400)
                return
            if not self.server.allowed():
                self.reply(429)
                return
            projection = read_incident_projection(self.server.store, *match.groups())
            payload = canonical_json_bytes(projection.model_dump(mode='json'))
            if len(payload) > 4096:
                raise ValueError('사건 요약 크기가 유효하지 않습니다')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.close_connection = True
            self.wfile.write(payload)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            # 사건 부재·저장 오류·다른 대상을 구별해 노출하지 않는다.
            self.reply(404)

    def do_POST(self):
        self.reply(405)


@contextmanager
def projection_server(store: Path, identity: Path, *, address=('0.0.0.0', 9444)):
    server = ProjectionServer(store, identity, address=address)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='격리 실습 사건의 제한된 요약 조회')
    parser.add_argument('--store', type=Path, default=Path('/data/incidents'))
    args = cli_paths(parser, parser.parse_args(argv), {'store': 'store'})
    os.umask(0o077)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    with projection_server(args.store, Path('/etc/ai-ci-projection')):
        stopped.wait()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
