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
import tempfile
import time


CONTEXT = 'arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener'
LEASE_PATH = '/apis/coordination.k8s.io/v1/namespaces/ai-ops-lab/leases/b6-scenario-lock'
ACCEPT_PATH = '^'+re.escape(LEASE_PATH)+'$'
LIMIT = 2 * 1024 * 1024


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
        super().__init__('localhost', timeout=3)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


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
        if self.closed or self.process is None or self.process.poll() is not None:
            raise ValueError('lease_proxy_not_running')
        payload = None if lease is None else json.dumps(self._validate(lease)).encode()
        if payload is not None and len(payload) > LIMIT:
            raise ValueError('lease_proxy_payload_too_large')
        connection = _UnixConnection(self.socket_path)
        try:
            connection.request(method, LEASE_PATH, body=payload,
                               headers={'Content-Type': 'application/json', 'Host': 'localhost'})
            response = connection.getresponse()
            body = response.read(LIMIT+1)
            if len(body) > LIMIT:
                raise ValueError('lease_proxy_response_too_large')
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
        except (OSError, http.client.HTTPException):
            raise ValueError('lease_proxy_connection_failed') from None
        finally:
            connection.close()

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
