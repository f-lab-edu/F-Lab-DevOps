"""Actual Unix header stall, guard CLI, failed readiness, and diagnostic boundaries."""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
import importlib.util
import json
import os
import socketserver
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

from ai_ops.c_trial_lease_client import TrialLeaseClient, LeaseRequestError, lease_failure_summary
from ai_ops.c_trial_schedule import RefreshingProbe
import test_c_trial_ready as fixtures
import test_c_trial_ready_transport as transport_fixtures
from test_c_trial_lease_client import lease

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('guard_timeout_cli', SCRIPTS/'run_c4_ready_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def diagnostic():
    return dict(code='lease_proxy_connection_failed', method='GET', phase='response_headers',
                cause='timeout', elapsed_seconds=3.001, timeout_seconds=3, http_status=None)


class GuardTimeoutLocalTest(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.ReadyStartTest(); self.fx.setUp(); self.addCleanup(self.fx.doCleanups)
        self.root = Path(self.fx.root.name).resolve(); self.root.chmod(0o700)
        self.intent = self.fx.intent
        self.marker = self.root/'marker.json'
        self.intent_file = self.root/'intent.json'
        self.intent_file.write_text(self.intent.model_dump_json()); self.intent_file.chmod(0o600)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'AI_OPS_FILES_ROOT': str(self.root)}).start()
        patch.object(guard, 'verify_local_kubeconfig').start()
        patch.object(guard, 'verify_normal').start()
        patch.object(guard, 'get_application', return_value={}).start()
        patch.object(guard, 'call', return_value=b'{}').start()
        self.rollback = patch.object(guard, 'rollback_at_deadline').start()
        self.args = ['--intent', str(self.intent_file), '--marker', str(self.marker),
                     '--kubeconfig', str(self.root/'unused'), '--role', 'local', '--core']

    def test_actual_three_second_header_stall_invalidates_ready_and_never_writes(self):
        # The only socket is Unix/local. No kubeconfig, authentication, or cloud is used.
        requests = []
        stop = threading.Event()
        value = lease(); value['metadata']['uid'] = self.intent.targets['lease_uid']
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                raw = b''
                while b'\r\n\r\n' not in raw:
                    chunk = self.request.recv(8192)
                    if not chunk: return
                    raw += chunk
                requests.append(raw.split(b'\r\n')[0])
                if len(requests) == 2:
                    stop.wait(4)
                    return
                body = json.dumps(value).encode()
                self.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: '+
                                     str(len(body)).encode()+b'\r\n\r\n'+body)
        # Keep AF_UNIX path below macOS limits.
        import tempfile
        with tempfile.TemporaryDirectory(dir='/tmp', prefix='c04-guard-') as folder:
            path = Path(folder)/'api.sock'
            server = socketserver.UnixStreamServer(str(path), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            client = TrialLeaseClient(self.root/'unused', value['metadata']['uid'])
            client.socket_path = path; client.process = Mock(); client.process.poll.return_value = None
            client.start = Mock(return_value=client)
            output = StringIO()
            try:
                with patch.object(guard, 'TrialLeaseClient', return_value=client), \
                        redirect_stdout(output), redirect_stderr(output), self.assertRaises(LeaseRequestError) as caught:
                    guard.main(self.args)
            finally:
                stop.set(); server.shutdown(); server.server_close(); thread.join(timeout=2)
            event = caught.exception.diagnostic
            self.assertEqual((event['phase'], event['cause']), ('response_headers', 'timeout'))
            self.assertEqual(event['timeout_seconds'], 3)
            self.assertTrue(2.8 <= event['elapsed_seconds'] < 4)
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(r.startswith(b'GET ') for r in requests))
            self.assertFalse(thread.is_alive()); self.assertTrue(client.closed)
            self.rollback.assert_not_called()
        self.assertEqual(json.loads(self.marker.read_text())['state'], 'standby')
        failed = json.loads(self.marker.with_name('marker.json.failure.json').read_text())
        self.assertEqual(failed['intent_hash'], self.intent.digest())
        self.assertEqual(failed['state'], 'failed')
        self.assertEqual(failed['lease_failure']['cause'], 'timeout')
        self.assertFalse((self.root/'consumed.json').exists())

    def test_trace_pin_is_forwarded_and_failure_does_not_retry(self):
        client = Mock(); error = LeaseRequestError('lease_proxy_connection_failed', diagnostic())
        client.start.side_effect = error
        with patch.object(guard, 'TrialLeaseClient', return_value=client) as factory, \
                self.assertRaises(LeaseRequestError) as caught:
            guard.main(self.args+['--lease-trace-binary', str(self.root/'pinned'),
                                 '--lease-trace-sha256', 'a'*64])
        self.assertIs(caught.exception, error)
        self.assertEqual(factory.call_args.kwargs['trace_sha256'], 'a'*64)
        self.assertEqual(factory.call_args.kwargs['trace_binary'], str(self.root/'pinned'))
        client.start.assert_called_once(); client.close.assert_called_once()
        client.replace.assert_not_called(); self.rollback.assert_not_called()

    def test_missing_pin_rejects_before_start(self):
        with patch.object(guard, 'TrialLeaseClient') as factory, redirect_stderr(StringIO()), \
                self.assertRaises(SystemExit):
            guard.main(self.args+['--lease-trace-binary', str(self.root/'untrusted')])
        factory.assert_not_called()

    def test_failed_diagnostic_write_does_not_replace_stop_or_skip_cleanup(self):
        client = Mock(); error = LeaseRequestError('lease_proxy_connection_failed', diagnostic())
        client.start.side_effect = error
        with patch.object(guard, 'TrialLeaseClient', return_value=client), \
                patch.object(guard, 'write_private_json', side_effect=OSError('SECRET')), \
                self.assertRaises(LeaseRequestError) as caught:
            guard.main(self.args)
        self.assertIs(caught.exception, error)
        client.close.assert_called_once(); self.rollback.assert_not_called()

    def test_sigterm_is_not_converted_to_lease_failure(self):
        client = Mock(); client.start.side_effect = ValueError('lease_proxy_termination_requested')
        with patch.object(guard, 'TrialLeaseClient', return_value=client), self.assertRaises(ValueError):
            guard.main(self.args)
        self.assertFalse(self.marker.with_name('marker.json.failure.json').exists())
        client.close.assert_called_once()


class FailedGuardEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.fx = transport_fixtures.StandbyEvidenceTest(); self.fx.setUp(); self.addCleanup(self.fx.doCleanups)

    def failure(self, **overrides):
        value = dict(state='failed', role='local', intent_hash=self.fx.intent.digest(),
                     guard_id='local:123:n', lease_failure=diagnostic())
        value.update(overrides); self.fx.write('marker.json.failure.json', value)

    def test_failure_invalidates_old_ready_even_before_process_death(self):
        reader = self.fx.reader
        probe = RefreshingProbe(self.fx.intent, reader)
        probe.refresh(); self.assertTrue(probe.require(self.fx.intent))
        self.failure()
        probe.refresh()
        self.assertIsNone(probe.value)
        with self.assertRaises(ValueError): probe.require(self.fx.intent)
        self.assertEqual(reader.last_failure['phase'], 'response_headers')
        self.assertEqual(reader.last_failure['cause'], 'timeout')

    def test_failure_survives_dead_pid_without_another_remote_read(self):
        self.failure(); self.fx.reader.github = Mock()
        with patch('ai_ops.c_trial_ready_transport.os.kill', side_effect=ProcessLookupError), \
                self.assertRaisesRegex(ValueError, 'local_standby_guard_failed'):
            self.fx.reader(self.fx.intent)
        self.fx.reader.github.assert_not_called()
        self.assertIsNotNone(self.fx.reader.last_failure)

    def test_foreign_failure_never_becomes_evidence(self):
        for changes in ({'intent_hash':'b'*64}, {'guard_id':'other'}, {'role':'remote'}):
            self.failure(**changes)
            with self.assertRaises(ValueError): self.fx.reader(self.fx.intent)
            self.assertIsNone(self.fx.reader.last_failure)

    def test_summary_is_bounded_redacted_and_idempotent(self):
        data = {**diagnostic(), 'token':'SECRET', 'path':'SECRET',
                'upstream': {'last_observed':{'phase':'authentication','state':'start','secret':'SECRET'}}}
        summary = lease_failure_summary(data)
        self.assertEqual(summary, lease_failure_summary(summary))
        self.assertNotIn('SECRET', json.dumps(summary))
        self.assertEqual(summary['last_observed_upstream'], {'phase':'authentication','state':'start'})
        self.assertFalse(summary['upstream_cause_proven'])
        hostile = {k: 'SECRET' for k in data}
        hostile.update(elapsed_seconds=float('nan'), timeout_seconds=30, http_status=True)
        self.assertNotIn('SECRET', json.dumps(lease_failure_summary(hostile), allow_nan=False))
        for value in ([], None, 'SECRET'):
            self.assertNotIn('SECRET', json.dumps(lease_failure_summary(value)))

    def test_oversized_integer_cannot_replace_original_failure_with_overflow(self):
        value = {**diagnostic(), 'elapsed_seconds': 10**400, 'timeout_seconds': 10**400}
        summary = lease_failure_summary(value)
        self.assertIsNone(summary['elapsed_seconds'])
        self.assertIsNone(summary['timeout_seconds'])
        self.assertEqual(summary['cause'], 'timeout')
