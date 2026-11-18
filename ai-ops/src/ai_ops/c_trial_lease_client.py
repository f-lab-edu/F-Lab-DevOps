"""한 실습 Lease만 노출하는 개인 Unix proxy. 인증·TLS·갱신은 kubectl이 담당한다."""

import http.client
import hashlib
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import socket
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import select
import time
import uuid


CONTEXT = 'arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener'
LEASE_PATH = '/apis/coordination.k8s.io/v1/namespaces/ai-ops-lab/leases/b6-scenario-lock'
ACCEPT_PATH = '^'+re.escape(LEASE_PATH)+'$'
LIMIT = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 3


class _StageCollector:
    """Drain untrusted stderr; retain only bounded, whitelisted events for this request."""

    PREFIX = b'C04_LEASE_STAGE_V1 '
    PHASES = {'authentication', 'authentication_refresh', 'dns', 'tcp', 'tls',
              'connection', 'request_write', 'response_wait', 'response_headers'}

    def __init__(self, stream=None):
        self.lock = threading.Lock()
        self.active = None
        self.events = []
        self.lost = False
        self.stop = threading.Event()
        self.thread = None
        if stream is not None:
            self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
            self.thread.start()

    def begin(self, request_id):
        with self.lock:
            self.active, self.events, self.lost = request_id, [], False

    def accept(self, line):
        # No raw log/error/header/token is retained or echoed, including malformed input.
        if len(line) > 1024 or not line.startswith(self.PREFIX):
            return
        try:
            value = json.loads(line[len(self.PREFIX):])
        except (ValueError, UnicodeError, RecursionError):
            return
        required = {'version', 'request_id', 'sequence', 'phase', 'state', 'elapsed_us', 'dropped'}
        if (not isinstance(value, dict) or not required <= set(value)
                or set(value) - required - {'reused'}
                or type(value['version']) is not int or value['version'] != 1
                or not isinstance(value['request_id'], str)
                or re.fullmatch('[a-f0-9]{32}', value['request_id']) is None
                or not isinstance(value['phase'], str) or value['phase'] not in self.PHASES
                or not isinstance(value['state'], str) or value['state'] not in {'start', 'done', 'failed'}
                or any(type(value[k]) is not int or not 0 <= value[k] <= 3600000000
                       for k in ('sequence', 'elapsed_us', 'dropped'))
                or value['sequence'] < 1
                or ('reused' in value and (type(value['reused']) is not bool
                    or value['phase'] != 'connection' or value['state'] != 'done'))):
            return
        with self.lock:
            if value['request_id'] != self.active:
                return
            if value['dropped'] or len(self.events) >= 32:
                self.lost = True
            self.events.append(value)
            self.events = sorted(self.events, key=lambda e: e['sequence'])[-32:]

    def snapshot(self):
        with self.lock:
            events = [dict(e) for e in self.events]
            seq = [e['sequence'] for e in events]
            return {'coverage': 'best_effort', 'delivery_complete': False,
                    'loss_observed': self.lost or seq != list(range(1, len(seq)+1)),
                    'events': events,
                    'last_observed': ({'phase': events[-1]['phase'], 'state': events[-1]['state']}
                                      if events else None)}

    def end(self):
        with self.lock:
            self.active = None

    def _read(self, stream):
        pending = bytearray()
        discard = False
        try:
            fd = stream.fileno()
            os.set_blocking(fd, False)
            while not self.stop.is_set():
                ready, _, _ = select.select([fd], [], [], .05)
                if not ready:
                    continue
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                for byte in chunk:
                    if byte == 10:
                        if not discard:
                            self.accept(bytes(pending))
                        pending.clear(); discard = False
                    elif not discard:
                        if len(pending) == 1024:
                            pending.clear(); discard = True
                        else:
                            pending.append(byte)
        except (OSError, ValueError):
            pass  # Losing telemetry cannot change request outcome or retry it.
        finally:
            stream.close()

    def close(self):
        self.end()
        self.stop.set()  # Never extend request/rollback budgets waiting for telemetry.


class LeaseRequestError(ValueError):
    """기존 오류 코드를 유지하며 원문 자격/응답 없는 진단만 전달한다."""

    def __init__(self, code, diagnostic):
        super().__init__(code)
        self.diagnostic = diagnostic


def lease_failure_summary(value):
    """Revalidate persisted child diagnostics; never forward raw exception/log fields."""
    value = value if isinstance(value, dict) else {}
    choices = {
        'code': {'lease_proxy_connection_failed', 'lease_proxy_not_running',
                 'lease_proxy_response_too_large', 'lease_proxy_incomplete_response',
                 'lease_proxy_invalid_response', 'lease_proxy_request_failed',
                 'lease_proxy_resource_changed'},
        'method': {'GET', 'PUT'},
        'phase': {'proxy_liveness', 'unix_connect', 'request_send', 'response_headers',
                  'response_body', 'response_validation'},
        'cause': {'timeout', 'peer_closed', 'incomplete_response', 'connection_reset',
                  'broken_pipe', 'socket_missing', 'connection_refused', 'permission_denied',
                  'http_protocol', 'os_error', 'proxy_not_running', 'response_too_large',
                  'invalid_json', 'http_status', 'resource_identity'},
    }
    result = {key: value.get(key) if isinstance(value.get(key), str)
              and value[key] in allowed else 'unknown' for key, allowed in choices.items()}
    for key, limit in (('elapsed_seconds', 3600), ('timeout_seconds', 3)):
        number = value.get(key)
        result[key] = (number if type(number) in (int, float) and 0 <= number <= limit
                       and math.isfinite(number) else None)
    status = value.get('http_status')
    result['http_status'] = status if type(status) is int and 100 <= status <= 599 else None
    upstream = value.get('upstream')
    last = (upstream.get('last_observed') if isinstance(upstream, dict)
            else value.get('last_observed_upstream'))
    result['last_observed_upstream'] = (
        {'phase': last['phase'], 'state': last['state']}
        if isinstance(last, dict) and isinstance(last.get('phase'), str)
        and last['phase'] in _StageCollector.PHASES and isinstance(last.get('state'), str)
        and last['state'] in {'start', 'done', 'failed'} else None)
    # Missing best-effort telemetry is not proof of a network/authentication cause.
    result['upstream_cause_proven'] = False
    return result


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

    def __init__(self, kubeconfig, uid, *, handle_sigterm=False,
                 trace_binary=None, trace_sha256=None):
        self.kubeconfig, self.uid = str(kubeconfig), uid
        if not isinstance(uid, str) or not uid or len(uid) > 128:
            raise ValueError('lease_proxy_uid_required')
        self.process = self.directory = self.socket_path = None
        self.closed = False
        self.handle_sigterm = handle_sigterm
        self.previous_sigterm = self.sigterm_handler = None
        self.trace_binary, self.trace_sha256 = trace_binary, trace_sha256
        self.stage_collector = None
        self.trace_request_id = None
        self.last_upstream_trace = None

    def _proxy_command(self):
        if self.trace_binary is None and self.trace_sha256 is None:
            return 'kubectl'
        if (not isinstance(self.trace_binary, (str, Path))
                or not isinstance(self.trace_sha256, str)
                or re.fullmatch('[a-f0-9]{64}', self.trace_sha256) is None):
            raise ValueError('lease_trace_binary_pin_required')
        path = Path(self.trace_binary)
        try:
            info = path.lstat()
            if (not path.is_absolute() or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid() or info.st_mode & 0o022
                    or info.st_nlink != 1 or not os.access(path, os.X_OK)):
                raise ValueError('lease_trace_binary_rejected')
            # A pinned file is still replaceable through a shared writable parent.
            # Only root-owned sticky system directories (e.g. /tmp) are exempt.
            for parent in path.parents:
                meta = parent.lstat()
                if stat.S_ISLNK(meta.st_mode) and meta.st_uid != 0:
                    raise ValueError('lease_trace_binary_rejected')
            for parent in path.resolve(strict=True).parents:
                meta = parent.stat()
                sticky_root = meta.st_uid == 0 and meta.st_mode & stat.S_ISVTX
                if (meta.st_uid not in (0, os.geteuid())
                        or (meta.st_mode & 0o022 and not sticky_root)):
                    raise ValueError('lease_trace_binary_rejected')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError('lease_trace_binary_rejected')
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                        info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                    raise ValueError('lease_trace_binary_rejected')
            if digest != self.trace_sha256:
                raise ValueError('lease_trace_binary_rejected')
        except OSError:
            # Do not put a private executable path in an error message.
            raise ValueError('lease_trace_binary_rejected') from None
        return str(path)

    def start(self):
        if self.closed or self.process is not None:
            raise ValueError('lease_proxy_already_started_or_closed')
        command = self._proxy_command()
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
                self.process = subprocess.Popen([command, '--kubeconfig', self.kubeconfig,
                    '--context', CONTEXT, '--request-timeout=3s', 'proxy',
                    '--unix-socket='+str(self.socket_path), '--accept-hosts=^localhost$',
                    '--accept-paths='+ACCEPT_PATH,
                    '--reject-methods=^(POST|PATCH|DELETE|CONNECT|HEAD|OPTIONS|TRACE)$'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE if self.trace_binary is not None else subprocess.DEVNULL)
                if self.trace_binary is not None:
                    self.stage_collector = _StageCollector(self.process.stderr)
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
        headers = {'Content-Type': 'application/json', 'Host': 'localhost'}
        if self.stage_collector is not None:
            self.trace_request_id = uuid.uuid4().hex
            self.stage_collector.begin(self.trace_request_id)
            headers.update({'Audit-ID': self.trace_request_id, 'X-C04-Lease-Trace': '1'})
        try:
            connection.request(method, LEASE_PATH, body=payload,
                               headers=headers)
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
            if self.stage_collector is not None:
                self.last_upstream_trace = self.stage_collector.snapshot()
                self.stage_collector.end()
                self.trace_request_id = None

    def _failure(self, code, method, phase, started, status, cause):
        exit_code = self.process.poll() if self.process is not None else None
        diagnostic = dict(event='lease_request_failed', schema_version=1, code=code,
            method=method, phase=phase, cause=cause, http_status=status,
            elapsed_seconds=round(time.monotonic()-started, 6),
            timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            proxy_exit_code=exit_code if type(exit_code) is int else None)
        if self.stage_collector is not None:
            diagnostic['request_id'] = self.trace_request_id
            diagnostic['upstream'] = (self.stage_collector.snapshot() if self.trace_request_id else
                {'coverage': 'best_effort', 'delivery_complete': False, 'loss_observed': False,
                 'events': [], 'last_observed': None})
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
                    if self.stage_collector is not None:
                        self.stage_collector.close()
                    if self.directory is not None:
                        self.directory.cleanup()
                finally:
                    if self.sigterm_handler is not None and signal.getsignal(signal.SIGTERM) is self.sigterm_handler:
                        signal.signal(signal.SIGTERM, self.previous_sigterm)
