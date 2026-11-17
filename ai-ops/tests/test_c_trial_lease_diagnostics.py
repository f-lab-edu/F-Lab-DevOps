"""실제 로컬 Unix 소켓 실패를 분류하고 재시도/자격 노출 없이 중지한다."""

from contextlib import contextmanager, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import socketserver
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from ai_ops.c_trial_lease_client import (TrialLeaseClient, LeaseRequestError,
    LEASE_PATH, REQUEST_TIMEOUT_SECONDS)
from test_c_trial_lease_client import lease


@contextmanager
def peer(reply):
    with tempfile.TemporaryDirectory(prefix='lease-diag-', dir='/tmp') as folder:
        path = Path(folder)/'api.sock'
        requests = []
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                raw = b''
                while b'\r\n\r\n' not in raw:
                    raw += self.request.recv(8192)
                headers, body = raw.split(b'\r\n\r\n', 1)
                length = next((int(line.split(b':', 1)[1]) for line in headers.split(b'\r\n')
                               if line.lower().startswith(b'content-length:')), 0)
                while len(body) < length:
                    chunk = self.request.recv(8192)
                    if not chunk:
                        break
                    raw += chunk; body += chunk
                requests.append(raw)
                reply(self.request)
        server = socketserver.UnixStreamServer(str(path), Handler)
        server.timeout = 5
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        client = TrialLeaseClient(Path(folder)/'unused', 'fixture-uid')
        client.socket_path = path
        client.process = Mock(); client.process.poll.return_value = None
        try:
            yield client, requests
        finally:
            thread.join(timeout=6)
            server.server_close()
            if thread.is_alive():
                raise AssertionError('local_socket_fixture_did_not_finish')


class LeaseDiagnosticsTest(unittest.TestCase):
    def failure(self, client, cause, phase, code='lease_proxy_connection_failed', method='GET'):
        output = StringIO()
        with redirect_stderr(output), self.assertRaises(LeaseRequestError) as caught:
            client.get() if method == 'GET' else client.replace(lease())
        error = caught.exception
        self.assertEqual(str(error), code)
        event = json.loads(output.getvalue())
        self.assertEqual(event, error.diagnostic)
        self.assertEqual(event['cause'], cause)
        self.assertEqual(event['phase'], phase)
        self.assertEqual(event['timeout_seconds'], 3)
        self.assertEqual(event['method'], method)
        self.assertIsInstance(error, ValueError)
        return event

    def test_header_timeout_uses_original_three_seconds_and_one_get(self):
        with peer(lambda sock: time.sleep(3.15)) as (client, requests):
            event = self.failure(client, 'timeout', 'response_headers')
            self.assertGreaterEqual(event['elapsed_seconds'], 2.8)
            self.assertLess(event['elapsed_seconds'], 4)
            self.assertEqual(len(requests), 1)
            self.assertIn(('GET '+LEASE_PATH+' HTTP').encode(), requests[0])

    def test_body_timeout_is_distinct_from_header_timeout(self):
        def reply(sock):
            sock.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 80\r\n\r\n{')
            time.sleep(3.15)
        with peer(reply) as (client, requests):
            event = self.failure(client, 'timeout', 'response_body')
            self.assertEqual(event['http_status'], 200)
            self.assertEqual(len(requests), 1)

    def test_headers_eof_is_peer_closed_with_proxy_still_alive(self):
        with peer(lambda sock: None) as (client, requests):
            event = self.failure(client, 'peer_closed', 'response_headers')
            self.assertIsNone(event['proxy_exit_code'])
            self.assertEqual(len(requests), 1)

    def test_valid_json_with_incomplete_http_length_is_rejected(self):
        def reply(sock):
            body = json.dumps(lease()).encode()
            sock.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)+1).encode()+b'\r\n\r\n'+body)
        with peer(reply) as (client, requests):
            self.failure(client, 'incomplete_response', 'response_body',
                         'lease_proxy_incomplete_response')
            self.assertEqual(len(requests), 1)

    def test_missing_socket_and_dead_proxy_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            client = TrialLeaseClient(Path(folder)/'unused', 'fixture-uid')
            client.socket_path = Path(folder)/'missing'; client.process = Mock()
            client.process.poll.return_value = None
            self.failure(client, 'socket_missing', 'unix_connect')
            client.process.poll.return_value = 9
            event = self.failure(client, 'proxy_not_running', 'proxy_liveness',
                                 'lease_proxy_not_running')
            self.assertEqual(event['proxy_exit_code'], 9)

    def test_auth_failure_has_no_body_headers_or_credentials_in_diagnostic(self):
        secret = 'fixture-credential-NEVER-LOG'
        def reply(sock):
            body = json.dumps({'kind':'Status','message':secret}).encode()
            sock.sendall(b'HTTP/1.1 403 Forbidden\r\nX-Secret: '+secret.encode()+b'\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        with peer(reply) as (client, requests):
            event = self.failure(client, 'http_status', 'response_validation',
                                 'lease_proxy_request_failed')
            self.assertEqual(event['http_status'], 403)
            self.assertNotIn(secret, json.dumps(event))
            self.assertEqual(len(requests), 1)

    def test_ambiguous_put_disconnect_never_replays_write(self):
        with peer(lambda sock: None) as (client, requests):
            self.failure(client, 'peer_closed', 'response_headers', method='PUT')
            self.assertEqual(len(requests), 1)
            self.assertTrue(requests[0].startswith(('PUT '+LEASE_PATH+' HTTP').encode()))

    def test_failed_diagnostic_output_preserves_original_stop_error(self):
        with peer(lambda sock: None) as (client, requests):
            with patch('ai_ops.c_trial_lease_client.sys.stderr.write', side_effect=OSError('fixture-private-text')):
                with self.assertRaises(LeaseRequestError) as caught:
                    client.get()
            self.assertEqual(str(caught.exception), 'lease_proxy_connection_failed')
            self.assertEqual(caught.exception.diagnostic['cause'], 'peer_closed')
            self.assertEqual(len(requests), 1)

    def test_request_timeout_constant_is_unchanged(self):
        self.assertEqual(REQUEST_TIMEOUT_SECONDS, 3)


if __name__ == '__main__':
    unittest.main()
