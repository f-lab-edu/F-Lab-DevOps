"""한 실습 Lease만 노출하는 개인 Unix proxy. 인증·TLS·갱신은 kubectl이 담당한다."""

import http.client
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import signal
import stat
import subprocess
import sys
import tempfile
import time


CONTEXT = 'arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener'
LEASE_PATH = '/apis/coordination.k8s.io/v1/namespaces/ai-ops-lab/leases/b6-scenario-lock'
ACCEPT_PATH = '^'+re.escape(LEASE_PATH)+'$'
LIMIT = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 3


class LeaseRequestError(ValueError):
    """기존 오류 코드를 유지하며 원문 자격/응답 없는 진단만 전달한다."""

    def __init__(self, code, diagnostic):
        super().__init__(code)
        self.diagnostic = diagnostic


def _cause(error):
    # 예외 문자열·args·HTTP 응답/헤더·proxy stderr를 진단에 복사하지 않는다.
    for kind, name in ((TimeoutError, 'timeout'),
                       (http.client.RemoteDisconnected, 'peer_closed'),
                       (http.client.IncompleteRead, 'incomplete_response'),
                       (ConnectionResetError, 'connection_reset'),
                       (BrokenPipeError, 'broken_pipe'),
                       (FileNotFoundError, 'socket_missing'),
                       (ConnectionRefusedError, 'connection_refused'),
                       (PermissionError, 'permission_denied')):
        if isinstance(error, kind):
            return name
    return 'http_protocol' if isinstance(error, http.client.HTTPException) else 'os_error'


class LeaseConflict(ValueError):
    pass


@contextmanager
def _defer_sigterm(enabled):
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM}) if enabled else None
    try:
        yield
    finally:
        if previous is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=REQUEST_TIMEOUT_SECONDS)
        self.path = str(path)
        self.phase = 'request_send'

    def connect(self):
        self.phase = 'unix_connect'
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)
        self.phase = 'request_send'


class TrialLeaseClient:
    """응답 캐시는 없다. GET과 resourceVersion을 보존한 PUT만 고정 경로로 보낸다."""

    def __init__(self, kubeconfig, uid, *, handle_sigterm=False):
        self.kubeconfig, self.uid = str(kubeconfig), uid
        if not isinstance(uid, str) or not uid or len(uid) > 128:
            raise ValueError('lease_proxy_uid_required')
        self.process = self.directory = self.socket_path = None
        self.closed = False
        self.handle_sigterm = handle_sigterm
        self.previous_sigterm = self.sigterm_handler = None

    def start(self):
        if self.closed or self.process is not None:
            raise ValueError('lease_proxy_already_started_or_closed')
        info = Path(self.kubeconfig).lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
            raise ValueError('lease_proxy_private_kubeconfig_required')
        # macOS의 긴 TMPDIR에서도 AF_UNIX 경로 제한을 넘지 않는다.
        self.directory = tempfile.TemporaryDirectory(prefix='c04-lease-',
            dir='/private/tmp' if Path('/private/tmp').is_dir() else '/tmp')
        self.socket_path = Path(self.directory.name)/'api.sock'
        try:
            if self.handle_sigterm:
                self.previous_sigterm = signal.getsignal(signal.SIGTERM)
                def terminate(signum, frame):
                    # 실행기의 기존 예외/원복/finally 경로를 거쳐 proxy도 회수한다.
                    raise ValueError('lease_proxy_termination_requested')
                self.sigterm_handler = terminate
                signal.signal(signal.SIGTERM, terminate)
            # PID를 소유 자료에 저장한 뒤 취소를 전달해 생성 직후 자식이 유실되지 않게 한다.
            with _defer_sigterm(self.handle_sigterm):
                self.process = subprocess.Popen(['kubectl', '--kubeconfig', self.kubeconfig,
                    '--context', CONTEXT, '--request-timeout=3s', 'proxy',
                    '--unix-socket='+str(self.socket_path), '--accept-hosts=^localhost$',
                    '--accept-paths='+ACCEPT_PATH,
                    '--reject-methods=^(POST|PATCH|DELETE|CONNECT|HEAD|OPTIONS|TRACE)$'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            end = time.monotonic()+10
            while not self.socket_path.exists():
                if self.process.poll() is not None or time.monotonic() >= end:
                    raise ValueError('lease_proxy_start_failed')
                time.sleep(.02)
            self.socket_path.chmod(0o600)
            self.get()  # 인증·TLS·UID 확인을 실제 승인/시작 예산 전에 준비한다.
            return self
        except BaseException:
            self.close()
            raise

    def _validate(self, lease):
        if not isinstance(lease, dict) or not isinstance(lease.get('metadata'), dict):
            raise ValueError('lease_proxy_resource_changed')
        meta = lease['metadata']
        if (lease.get('apiVersion') != 'coordination.k8s.io/v1' or lease.get('kind') != 'Lease'
                or meta.get('name') != 'b6-scenario-lock' or meta.get('namespace') != 'ai-ops-lab'
                or meta.get('uid') != self.uid or not isinstance(meta.get('resourceVersion'), str)
                or not meta['resourceVersion'] or not isinstance(lease.get('spec'), dict)):
            raise ValueError('lease_proxy_resource_changed')
        return lease

    def _request(self, method, lease=None):
        started = time.monotonic()
        if self.closed or self.process is None or self.process.poll() is not None:
            raise self._failure('lease_proxy_not_running', method, 'proxy_liveness',
                                started, None, 'proxy_not_running')
        payload = None if lease is None else json.dumps(self._validate(lease)).encode()
        if payload is not None and len(payload) > LIMIT:
            raise ValueError('lease_proxy_payload_too_large')
        connection = _UnixConnection(self.socket_path)
        status = None
        try:
            connection.request(method, LEASE_PATH, body=payload,
                               headers={'Content-Type': 'application/json', 'Host': 'localhost'})
            connection.phase = 'response_headers'
            response = connection.getresponse()
            status = response.status
            connection.phase = 'response_body'
            body = response.read(LIMIT+1)
            if len(body) > LIMIT:
                raise ValueError('lease_proxy_response_too_large')
            # read(amt)는 EOF 때 Content-Length 부족을 예외 없이 반환한다.
            # JSON 자체가 완전해도 잘린 HTTP 응답을 성공으로 사용하지 않는다.
            if response.length not in (None, 0):
                raise ValueError('lease_proxy_incomplete_response')
            connection.phase = 'response_validation'
            try:
                value = json.loads(body)
            except (ValueError, UnicodeError):
                raise ValueError('lease_proxy_invalid_response') from None
            if (response.status == 409 and method == 'PUT' and isinstance(value, dict)
                    and value.get('kind') == 'Status' and value.get('reason') == 'Conflict'):
                raise LeaseConflict('lease_proxy_cas_conflict')
            if response.status != 200:
                raise ValueError('lease_proxy_request_failed')
            return self._validate(value)
        except (OSError, http.client.HTTPException) as error:
            raise self._failure('lease_proxy_connection_failed', method, connection.phase,
                                started, status, _cause(error)) from None
        except LeaseConflict:
            raise
        except ValueError as error:
            causes = {'lease_proxy_response_too_large': 'response_too_large',
                      'lease_proxy_incomplete_response': 'incomplete_response',
                      'lease_proxy_invalid_response': 'invalid_json',
                      'lease_proxy_request_failed': 'http_status',
                      'lease_proxy_resource_changed': 'resource_identity'}
            # SIGTERM의 기존 취소 예외 등은 변환하거나 재시도하지 않는다.
            if str(error) not in causes:
                raise
            raise self._failure(str(error), method, connection.phase,
                                started, status, causes[str(error)]) from None
        finally:
            connection.close()

    def _failure(self, code, method, phase, started, status, cause):
        exit_code = self.process.poll() if self.process is not None else None
        diagnostic = dict(event='lease_request_failed', schema_version=1, code=code,
            method=method, phase=phase, cause=cause, http_status=status,
            elapsed_seconds=round(time.monotonic()-started, 6),
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            proxy_exit_code=exit_code if type(exit_code) is int else None)
        try:
            print(json.dumps(diagnostic, separators=(',', ':')), file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass  # 진단 출력 실패가 기존 중지·원복 경로를 바꾸지 않는다.
        return LeaseRequestError(code, diagnostic)

    def get(self):
        return self._request('GET')

    def replace(self, lease):
        return self._request('PUT', lease)

    def close(self):
        self.closed = True
        with _defer_sigterm(self.handle_sigterm and self.sigterm_handler is not None):
            try:
                if self.process is not None and self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
            finally:
                try:
                    if self.directory is not None:
                        self.directory.cleanup()
                finally:
                    if self.sigterm_handler is not None and signal.getsignal(signal.SIGTERM) is self.sigterm_handler:
                        signal.signal(signal.SIGTERM, self.previous_sigterm)
