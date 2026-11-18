"""Bounded read-only 401 recovery; no credentials, cluster or network egress."""
from contextlib import contextmanager, redirect_stderr
from io import StringIO
import http.server
import json
from pathlib import Path
import socketserver
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from ai_ops.c_trial_lease_client import TrialLeaseClient, LeaseRequestError, LeaseConflict, LEASE_PATH, lease_failure_summary
from test_c_trial_lease_client import lease

UNAUTHORIZED = {'kind':'Status','reason':'Unauthorized','message':'SECRET-CANARY'}

@contextmanager
def responses(replies):
    with tempfile.TemporaryDirectory(dir='/tmp') as folder:
        path=Path(folder)/'api.sock';requests=[];stop=threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def handle_request(self):
                index=len(requests);requests.append((self.command,self.path))
                if self.command=='PUT':self.rfile.read(int(self.headers['Content-Length']))
                reply=replies[min(index,len(replies)-1)]
                code,value,delay=reply[:3]
                stop.wait(delay)
                payload=json.dumps(value).encode()
                try:
                    self.send_response(code);self.send_header('Content-Length',str(len(payload)))
                    self.end_headers()
                    if len(reply)>3:
                        for byte in payload:
                            self.wfile.write(bytes([byte]));self.wfile.flush();stop.wait(reply[3])
                    else:self.wfile.write(payload)
                except (OSError,ValueError):pass
            do_GET=do_PUT=handle_request
            def log_message(self,*_):pass
        class Server(socketserver.ThreadingUnixStreamServer):daemon_threads=True
        server=Server(str(path),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        c=TrialLeaseClient(Path(folder)/'unused','fixture-uid');c.socket_path=path;c.process=Mock();c.process.poll.return_value=None
        try:yield c,requests
        finally:stop.set();server.shutdown();server.server_close();thread.join(2)

class AuthenticationRefreshTest(unittest.TestCase):
    def test_unauthorized_get_reads_new_live_value_once(self):
        fresh=lease();fresh['metadata']['resourceVersion']='99';fresh['spec']['holderIdentity']='other-owner'
        with responses([(401,UNAUTHORIZED,0),(200,fresh,0)]) as (c,req), redirect_stderr(StringIO()) as output:
            self.assertEqual(c.get(),fresh)
            self.assertEqual(req,[('GET',LEASE_PATH)]*2)
            self.assertEqual(c.last_auth_retry['outcome'],'recovered')
            self.assertNotIn('SECRET-CANARY',output.getvalue())

    def test_persistent_401_stops_after_one_retry(self):
        with responses([(401,UNAUTHORIZED,0)]) as (c,req), redirect_stderr(StringIO()) as output:
            with self.assertRaises(LeaseRequestError) as caught:c.get()
            self.assertEqual(len(req),2);self.assertEqual(caught.exception.diagnostic['http_status'],401)
            self.assertEqual(lease_failure_summary(caught.exception.diagnostic)['authentication_retry'],
                             {'attempts':2,'first_http_status':401})
            self.assertEqual(c.last_auth_retry['outcome'],'failed');self.assertNotIn('SECRET-CANARY',output.getvalue())

    def test_put_401_is_never_replayed(self):
        with responses([(401,UNAUTHORIZED,0)]) as (c,req), redirect_stderr(StringIO()):
            with self.assertRaises(LeaseRequestError):c.replace(lease())
            self.assertEqual(req,[('PUT',LEASE_PATH)]);self.assertIsNone(c.last_auth_retry)

    def test_other_status_or_malformed_unauthorized_is_not_retried(self):
        for status,body in [(403,UNAUTHORIZED),(500,UNAUTHORIZED),(401,{}),(401,[]),(401,{'kind':'Status','reason':'Forbidden'})]:
            with self.subTest(status=status,body=body),responses([(status,body,0)]) as (c,req),redirect_stderr(StringIO()):
                with self.assertRaises(LeaseRequestError):c.get()
                self.assertEqual(len(req),1)

    def test_second_response_still_checks_identity_and_cas(self):
        bad=lease();bad['metadata']['uid']='replaced'
        with responses([(401,UNAUTHORIZED,0),(200,bad,0)]) as (c,req),redirect_stderr(StringIO()):
            with self.assertRaisesRegex(LeaseRequestError,'resource_changed'):c.get()
            self.assertEqual(len(req),2)
        with responses([(409,{'kind':'Status','reason':'Conflict'},0)]) as (c,req):
            with self.assertRaises(LeaseConflict):c.replace(lease())
            self.assertEqual(len(req),1)

    def test_retry_shares_original_three_second_budget(self):
        with responses([(401,UNAUTHORIZED,1.7),(200,lease(),1.7)]) as (c,req),redirect_stderr(StringIO()):
            start=time.monotonic()
            with self.assertRaises(LeaseRequestError) as caught:c.get()
            elapsed=time.monotonic()-start
            self.assertEqual(len(req),2);self.assertEqual(caught.exception.diagnostic['cause'],'timeout')
            self.assertGreater(elapsed,2.8);self.assertLess(elapsed,3.3)

    def test_slow_body_cannot_reset_deadline_per_read(self):
        with responses([(200,lease(),0,.03)]) as (c,req),redirect_stderr(StringIO()):
            start=time.monotonic()
            with self.assertRaises(LeaseRequestError) as caught:c.get()
            self.assertLess(time.monotonic()-start,3.3)
            self.assertEqual(caught.exception.diagnostic['phase'],'response_body');self.assertEqual(len(req),1)

    def test_cancellation_before_retry_preserves_stop_without_second_request(self):
        with responses([(401,UNAUTHORIZED,0)]) as (c,req),redirect_stderr(StringIO()):
            c.process.poll.side_effect=[None,ValueError('lease_proxy_termination_requested')]
            with self.assertRaisesRegex(ValueError,'termination_requested'):c.get()
            self.assertEqual(len(req),1)

    def test_retry_diagnostics_whitelist_discards_untrusted_fields(self):
        value={'authentication_retry':{'attempts':2,'first_http_status':401,'token':'SECRET-CANARY'}}
        summary=lease_failure_summary(value)
        self.assertEqual(summary,lease_failure_summary(summary))
        self.assertNotIn('SECRET-CANARY',json.dumps(summary))
        value['authentication_retry']['attempts']=True
        self.assertNotIn('authentication_retry',lease_failure_summary(value))

    def test_second_401_is_not_a_normal_lease_or_approval(self):
        with responses([(401,UNAUTHORIZED,0),(401,lease(),0)]) as (c,req),redirect_stderr(StringIO()):
            with self.assertRaises(LeaseRequestError):c.get()
            self.assertEqual(len(req),2)

if __name__=='__main__':unittest.main()
