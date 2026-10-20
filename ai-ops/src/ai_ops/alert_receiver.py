"""TLS·파일 인증 뒤 경보를 영속 큐에 접수하는 제한된 내부 수신기."""

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
import ssl
import stat
import threading
import time

from ai_ops.alerts import AlertInbox, MAX_BODY
from ai_ops.b_contracts import load_b_settings
from ai_ops.job_queue import JobQueue, QueueFull
from ai_ops.observability import log_event
from ai_ops.path_policy import cli_paths


class IdentityUnavailable(OSError):
    """인증 파일을 안전하게 읽지 못해 발신자의 재전송이 필요한 상태다."""


def read_token(identity: Path) -> str:
    """projected Secret의 링크는 허용하고 실제 파일 권한·길이를 확인한다."""
    path = identity / 'token'
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid()) or stat.S_IMODE(info.st_mode) & 0o037:
        raise IdentityUnavailable('경보 인증 파일 권한이 안전하지 않습니다')
    with path.open('rb') as stream:
        raw = stream.read(259)
    if len(raw) > 258:
        raise IdentityUnavailable('경보 인증 파일 크기가 유효하지 않습니다')
    try:
        value = raw.decode('ascii').rstrip('\r\n')
    except UnicodeError:
        raise IdentityUnavailable('경보 인증 파일이 유효하지 않습니다') from None
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', value):
        raise IdentityUnavailable('경보 인증 파일이 유효하지 않습니다')
    return value


def tls_context(identity: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    for name in ('tls.crt', 'tls.key'):
        info = (identity / name).stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.getuid()) or stat.S_IMODE(info.st_mode) & 0o037:
            raise ValueError('수신 TLS 파일 권한이 안전하지 않습니다')
    context.load_cert_chain(identity / 'tls.crt', identity / 'tls.key')
    return context


class AlertServer(HTTPServer):
    """연결당 2초·본문 64KiB·인증 후 분당 60회로 접수 범위를 제한한다."""

    request_queue_size = 8
    allow_reuse_address = True

    def __init__(self, inbox: AlertInbox, identity: Path, *, address=('0.0.0.0', 9443)):
        self.inbox = inbox
        self.identity = identity
        read_token(identity)
        self.tls = tls_context(identity)
        self.window = time.monotonic()
        self.calls = 0
        super().__init__(address, AlertHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
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
        return self.calls <= 60

    def handle_error(self, *_):
        log_event('alert_rejected', stage='alert', result='failure')


class AlertHandler(BaseHTTPRequestHandler):
    """인증과 저장 결과만 반환하며 경로·헤더·본문을 로그로 복제하지 않는다."""

    protocol_version = 'HTTP/1.0'

    def log_message(self, *_):
        pass

    def reply(self, code, value):
        payload = json.dumps(value, separators=(',', ':')).encode('ascii')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        if code in (429, 503):
            self.send_header('Retry-After', '60')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(payload)

    def send_error(self, code, *_):
        # HTTP parser의 상세 오류도 요청 원문을 포함하지 않는다.
        self.reply(code, {'error': 'invalid_http'})

    def do_POST(self):
        if self.path != '/v1/alerts':
            self.reply(404, {'error': 'not_found'})
            return
        try:
            authorizations = self.headers.get_all('Authorization', [])
            expected = 'Bearer ' + read_token(self.server.identity)
            if (len(authorizations) != 1 or len(authorizations[0]) > 300
                    or not hmac.compare_digest(authorizations[0].encode('utf-8'), expected.encode('ascii'))):
                self.reply(401, {'error': 'unauthorized'})
                return
            if not self.server.allowed():
                self.reply(429, {'error': 'rate_limited'})
                return
            sizes = self.headers.get_all('Content-Length', [])
            if (len(sizes) != 1 or not re.fullmatch(r'[0-9]{1,6}', sizes[0])
                    or self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Content-Encoding')
                    or len(self.headers.get_all('Content-Type', [])) != 1
                    or self.headers['Content-Type'].split(';', 1)[0].strip().lower() != 'application/json'):
                self.reply(400, {'error': 'invalid_request'})
                return
            size = int(sizes[0])
            if not 1 <= size <= MAX_BODY:
                self.reply(413, {'error': 'payload_too_large'})
                return
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError('불완전한 전송입니다')
            receipt = self.server.inbox.accept(raw)
            # DB commit이 끝난 경우에만 성공을 반환한다. 조사 완료를 기다리지 않는다.
            self.reply(202, receipt)
            log_event('alert_received', stage='alert', result='partial' if receipt['rejected'] else 'success')
        except QueueFull:
            self.reply(503, {'error': 'queue_full'})
            log_event('alert_rejected', stage='alert', result='failure')
        except (sqlite3.Error, OSError):
            self.reply(503, {'error': 'storage_or_identity_unavailable'})
            log_event('alert_rejected', stage='alert', result='failure')
        except (ValueError, KeyError, TypeError):
            self.reply(400, {'error': 'invalid_alert'})
            log_event('alert_rejected', stage='alert', result='failure')

    def do_GET(self):
        self.reply(405, {'error': 'method_not_allowed'})


@contextmanager
def receiver_server(inbox: AlertInbox, identity: Path, *, address=('0.0.0.0', 9443)):
    server = AlertServer(inbox, identity, address=address)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='TLS 인증된 내부 경보를 영속 큐에 접수한다')
    parser.add_argument('--config', type=Path, default=Path('/etc/ai-ops'))
    parser.add_argument('--store', type=Path, default=Path('/data/incidents'))
    parser.add_argument('--identity', type=Path, default=Path('/etc/ai-ops-webhook'))
    parser.add_argument('--cluster-id', required=True, help='인증 발신자에게 허용한 등록 클러스터')
    args = cli_paths(parser, parser.parse_args(argv), {'config':'directory','store':'store','identity':'directory'})
    os.umask(0o077)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        settings = load_b_settings(args.config/'bindings.json', args.config/'runtime.json', args.config/'metric-queries.json')
        inbox = AlertInbox(JobQueue(args.store), settings, cluster_id=args.cluster_id)
        with receiver_server(inbox, args.identity):
            log_event('process_started', stage='alert')
            stopped.wait()
        log_event('process_stopped', stage='alert')
        return 0
    except Exception:
        log_event('process_stopped', stage='alert', result='failure')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
