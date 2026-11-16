"""로컬 Unix HTTP 대역으로 최신 조회·CAS·범위 제한·기동 실패/회수를 검증한다."""

from copy import deepcopy
from datetime import timedelta
import http.server
import json
import os
from pathlib import Path
import socketserver
import signal
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from ai_ops.c_trial_lease_client import TrialLeaseClient, LeaseConflict, LEASE_PATH, ACCEPT_PATH, LIMIT, CONTEXT
from ai_ops.c_trial_ready import ACTIVATION_KEY, ACK_KEYS
from ai_ops.c_trial_ready_guard import standby_guard
import test_c_trial_ready as fixtures
from test_c2_active_capture import NOW


def lease():
    return dict(apiVersion='coordination.k8s.io/v1', kind='Lease', metadata=dict(
        namespace='ai-ops-lab', name='b6-scenario-lock', uid='fixture-uid', resourceVersion='1'), spec={})


class LeaseHTTPTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='lease-test-', dir='/tmp')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name); self.path = self.root/'api.sock'
        self.value = lease(); self.requests = []; self.status = None; self.body = None
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def handle_request(self):
                owner.requests.append((self.command, self.path))
                code, body = 200, owner.value
                if self.command == 'PUT':
                    desired = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    if desired['metadata']['resourceVersion'] != owner.value['metadata']['resourceVersion']:
                        code, body = 409, {'kind': 'Status', 'reason': 'Conflict'}
                    else:
                        owner.value = desired
                        owner.value['metadata']['resourceVersion'] = str(int(desired['metadata']['resourceVersion'])+1)
                        body = owner.value
                code = owner.status or code
                payload = owner.body if owner.body is not None else json.dumps(body).encode()
                self.send_response(code); self.send_header('Content-Length', str(len(payload)))
                self.end_headers(); self.wfile.write(payload)
            do_GET = do_PUT = handle_request
            def log_message(self, *args):
                pass
        self.server = socketserver.UnixStreamServer(str(self.path), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop_server)
        self.client = TrialLeaseClient(self.root/'unused-kubeconfig', 'fixture-uid')
        self.client.socket_path = self.path; self.client.process = Mock()
        self.client.process.poll.return_value = None

    def stop_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def test_every_get_is_fresh_and_only_fixed_lease_is_requested(self):
        first = self.client.get()
        self.value['spec']['holderIdentity'] = 'changed-owner'
        second = self.client.get()
        self.assertNotEqual(first, second)
        self.assertEqual(second['spec']['holderIdentity'], 'changed-owner')
        self.assertEqual(self.requests, [('GET', LEASE_PATH)]*2)

    def test_conflicting_replace_preserves_other_guard_ack_until_fresh_retry(self):
        stale = self.client.get()
        other = self.client.get(); other['metadata']['annotations'] = {'remote': 'exact-ack'}
        self.client.replace(other)
        with self.assertRaises(LeaseConflict): self.client.replace(stale)
        fresh = self.client.get(); fresh['metadata']['annotations']['local'] = 'exact-ack'
        result = self.client.replace(fresh)
        self.assertEqual(result['metadata']['annotations'], {'remote': 'exact-ack', 'local': 'exact-ack'})
        self.assertTrue(all(path == LEASE_PATH for _, path in self.requests))

    def test_unknown_uid_or_wrong_resource_cannot_be_read_or_written(self):
        for key, value in [('uid', 'replacement'), ('name', 'other'), ('namespace', 'production'),
                           ('resourceVersion', '')]:
            self.value = lease(); self.value['metadata'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.client.get()
            before = len(self.requests)
            with self.assertRaises(ValueError): self.client.replace(self.value)
            self.assertEqual(len(self.requests), before)

    def test_missing_or_wrong_response_type_fails_closed(self):
        for value in [[], None, {}, {'metadata': []}, {**lease(), 'spec': []}]:
            self.body = json.dumps(value).encode()
            with self.subTest(value=value), self.assertRaises(ValueError): self.client.get()

    def test_auth_failure_and_other_http_status_are_not_retried_as_conflicts(self):
        for code in [401, 403, 404, 409, 500]:
            self.status = code; self.body = b'{"kind":"Status","reason":"Forbidden"}'
            with self.subTest(code=code), self.assertRaises(ValueError) as error: self.client.get()
            self.assertNotIsInstance(error.exception, LeaseConflict)

    def test_malformed_and_oversized_bodies_fail_without_echoing_payload(self):
        for body in [b'credential-body', b'x'*(LIMIT+1)]:
            self.body = body
            with self.assertRaises(ValueError) as error: self.client.get()
            self.assertNotIn('credential-body', str(error.exception))

    def test_proxy_death_and_socket_failure_have_no_shell_fallback(self):
        self.client.process.poll.return_value = 1
        with self.assertRaisesRegex(ValueError, 'not_running'): self.client.get()
        self.assertEqual(self.requests, [])
        self.client.process.poll.return_value = None; self.client.socket_path = self.root/'missing.sock'
        with self.assertRaisesRegex(ValueError, 'connection_failed'): self.client.get()

    def test_two_guard_cas_race_preserves_both_exact_acks_and_frozen_deadline(self):
        f = fixtures.ReadyStartTest(); f.setUp(); self.addCleanup(f.doCleanups); f.start()
        activation = f.activation
        self.value = deepcopy(f.fx.server.value); self.value['spec'] = {}
        self.value['metadata']['annotations'] = {}; self.value['metadata']['resourceVersion'] = '1'
        ready = threading.Barrier(2); replace = threading.Barrier(2)
        rollbacks, errors, conflicts = [], [], []
        def run(role):
            client = TrialLeaseClient(self.root/'unused', activation.execution.lease_uid)
            client.process = self.client.process; client.socket_path = self.path
            now = NOW; tick = 0.; wrote = False; first = True
            def publish(_):
                ready.wait(timeout=3)
                # 첫 번째 감시가 읽기 전에 두 초기 Lease 조회가 끝났음을 보장한다.
                self.value['spec']['holderIdentity'] = activation.owner_holder
                self.value['metadata']['annotations'][ACTIVATION_KEY] = activation.model_dump_json()
            def write(value):
                nonlocal wrote, first
                if first:
                    first = False; replace.wait(timeout=3)
                try: client.replace(value)
                except LeaseConflict:
                    conflicts.append(role); return False
                wrote = True; return True
            def sleep(seconds):
                nonlocal now, tick
                delta = 300. if wrote else seconds
                now += timedelta(seconds=delta); tick += delta
            try:
                standby_guard(activation.intent, role=role,
                    guard_id=activation.readiness.guards[role], get_lease=client.get,
                    replace_lease=write, rollback=lambda plan, _: rollbacks.append((role, plan)),
                    publish_ready=publish, clock=lambda: now, monotonic=lambda: tick, sleep=sleep)
            except BaseException as exc: errors.append(exc)
        threads = [threading.Thread(target=run, args=(role,)) for role in ACK_KEYS]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads)); self.assertEqual(errors, [])
        self.assertEqual(len(conflicts), 1); self.assertEqual(len(rollbacks), 2)
        for role, key in ACK_KEYS.items():
            self.assertEqual(json.loads(self.value['metadata']['annotations'][key]),
                {'activation_hash': activation.digest(), 'guard_id': activation.readiness.guards[role]})
        for _, plan in rollbacks:
            self.assertEqual(plan['deadline_utc'], (NOW+timedelta(seconds=300)).isoformat())


class LeaseLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.kubeconfig = Path(self.folder.name)/'config'; self.kubeconfig.write_text('fixture')
        self.kubeconfig.chmod(0o600)
        self.client = TrialLeaseClient(self.kubeconfig, 'fixture-uid')

    def test_one_proxy_private_socket_exact_filter_and_existing_auth_no_tcp_listener(self):
        calls = []
        process = Mock(); process.poll.return_value = None
        def launch(argv, **kwargs):
            calls.append(argv)
            Path(next(a.split('=', 1)[1] for a in argv if a.startswith('--unix-socket='))).touch()
            return process
        with patch('ai_ops.c_trial_lease_client.subprocess.Popen', side_effect=launch), \
             patch.object(self.client, 'get', return_value=lease()) as read:
            self.client.start()
            self.assertEqual(self.client.socket_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.client.socket_path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(len(calls), 1)
            self.assertIn('--context', calls[0]); self.assertIn(CONTEXT, calls[0])
            self.assertIn('--accept-paths='+ACCEPT_PATH, calls[0])
            self.assertIn('--accept-hosts=^localhost$', calls[0])
            self.assertFalse(any('disable-filter' in x or 'insecure' in x or x.startswith('--port=') for x in calls[0]))
            socket_path = self.client.socket_path; self.client.close()
            process.terminate.assert_called_once(); self.assertFalse(socket_path.parent.exists())

    def test_auth_or_warmup_failure_terminates_proxy_and_removes_private_directory(self):
        process = Mock(); process.poll.return_value = None
        def launch(argv, **kw): self.client.socket_path.touch(); return process
        with patch('ai_ops.c_trial_lease_client.subprocess.Popen', side_effect=launch), \
             patch.object(self.client, 'get', side_effect=ValueError('auth_failed')):
            with self.assertRaisesRegex(ValueError, 'auth_failed'): self.client.start()
        process.terminate.assert_called_once(); self.assertFalse(self.client.socket_path.parent.exists())
        with self.assertRaises(ValueError): self.client.start()

    def test_sigterm_uses_existing_error_path_and_restores_previous_handler(self):
        process = Mock(); process.poll.return_value = None
        original = signal.getsignal(signal.SIGTERM)
        self.client.handle_sigterm = True
        def launch(argv, **kw): self.client.socket_path.touch(); return process
        with patch('ai_ops.c_trial_lease_client.subprocess.Popen', side_effect=launch), \
             patch.object(self.client, 'get', return_value=lease()):
            try:
                self.client.start()
                with self.assertRaisesRegex(ValueError, 'termination_requested'):
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            finally: self.client.close()
        self.assertIs(signal.getsignal(signal.SIGTERM), original)
        process.terminate.assert_called_once(); self.assertFalse(self.client.socket_path.parent.exists())

    def test_sigterm_during_spawn_is_deferred_until_the_child_pid_is_owned(self):
        process = Mock(); process.poll.return_value = None
        original = signal.getsignal(signal.SIGTERM); self.client.handle_sigterm = True
        def launch(argv, **kw):
            os.kill(os.getpid(), signal.SIGTERM)
            return process
        with patch('ai_ops.c_trial_lease_client.subprocess.Popen', side_effect=launch):
            with self.assertRaisesRegex(ValueError, 'termination_requested'): self.client.start()
        process.terminate.assert_called_once()
        self.assertFalse(self.client.socket_path.parent.exists())
        self.assertIs(signal.getsignal(signal.SIGTERM), original)

    def test_fixed_path_filter_treats_api_group_dots_as_literals(self):
        import re
        self.assertIsNotNone(re.fullmatch(ACCEPT_PATH, LEASE_PATH))
        self.assertIsNone(re.fullmatch(ACCEPT_PATH, LEASE_PATH.replace('coordination.k8s.io', 'coordinationXk8sYio')))

    def test_proxy_spawn_failure_cleans_directory(self):
        with patch('ai_ops.c_trial_lease_client.subprocess.Popen', side_effect=OSError('missing')):
            with self.assertRaises(OSError): self.client.start()
        self.assertFalse(self.client.socket_path.parent.exists())

    def test_public_or_symlink_kubeconfig_is_rejected_before_proxy_starts(self):
        with patch('ai_ops.c_trial_lease_client.subprocess.Popen') as spawn:
            self.kubeconfig.chmod(0o644)
            with self.assertRaises(ValueError): self.client.start()
            self.kubeconfig.chmod(0o600)
            link = self.kubeconfig.parent/'link'; link.symlink_to(self.kubeconfig)
            with self.assertRaises(ValueError): TrialLeaseClient(link, 'fixture-uid').start()
            spawn.assert_not_called()

    def test_unresponsive_proxy_is_killed_and_directory_still_removed(self):
        self.client.directory = tempfile.TemporaryDirectory(prefix='lease-kill-', dir='/tmp')
        path = Path(self.client.directory.name); self.client.process = Mock()
        self.client.process.poll.return_value = None
        self.client.process.wait.side_effect = [subprocess.TimeoutExpired('proxy', 5), None]
        self.client.close(); self.client.process.kill.assert_called_once()
        self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
