"""실제 TLS 소켓의 인증·접수 결과·형식·장애 응답과 토큰 교체를 검증한다."""

import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import ssl
import subprocess
import unittest
from unittest.mock import patch

from ai_ops.alert_receiver import receiver_server, read_token
from ai_ops.job_queue import QueueFull
import test_alerts as alert_tests


class AlertReceiverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.certificates=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.certificates.cleanup)
        directory=Path(cls.certificates.name)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
            '-addext','subjectAltName=DNS:localhost','-keyout',str(directory/'tls.key'),'-out',str(directory/'tls.crt')],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def setUp(self):
        self.fixture=alert_tests.AlertInboxTest();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.identity=self.fixture.fixture.root/'identity';self.identity.mkdir(mode=0o700)
        for name in ('tls.key','tls.crt'):
            (self.identity/name).write_bytes((Path(self.certificates.name)/name).read_bytes())
            (self.identity/name).chmod(0o600)
        self.token='synthetic-webhook-token-'+'x'*40
        (self.identity/'token').write_text(self.token);(self.identity/'token').chmod(0o600)
        self.context=ssl.create_default_context(cafile=str(self.identity/'tls.crt'))
        self.stderr=contextlib.redirect_stderr(io.StringIO());self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__,None,None,None)
        self.server_context=receiver_server(self.fixture.inbox,self.identity,address=('127.0.0.1',0))
        self.server=self.server_context.__enter__();self.addCleanup(self.server_context.__exit__,None,None,None)

    def request(self, *, token=None, raw=None, method='POST', path='/v1/alerts', headers=None):
        connection=http.client.HTTPSConnection('localhost',self.server.server_port,context=self.context,timeout=4)
        try:
            connection.request(method,path,body=self.fixture.body() if raw is None else raw,
                headers=headers or {'Authorization':'Bearer '+(self.token if token is None else token),'Content-Type':'application/json'})
            response=connection.getresponse();body=response.read()
            return response.status,json.loads(body)
        finally:connection.close()

    def test_authenticated_tls_ack_persists_before_response_and_duplicates(self):
        status,value=self.request()
        self.assertEqual(status,202);self.assertEqual(value['accepted'],1)
        self.assertEqual(len(self.fixture.rows('SELECT * FROM jobs')),1)
        self.assertEqual(self.request()[1]['duplicate'],1)
        result=self.fixture.fixture.worker().run_once()
        self.assertEqual(result['state'],'partial')
        report=self.fixture.queue.engine.show(result['incident_id'],result['run_id']).report
        self.assertEqual(report.execution.mode,'rules')

    def test_authentication_and_token_rotation_fail_closed(self):
        self.assertEqual(self.request(token='wrong')[0],401)
        self.assertEqual(self.fixture.rows('SELECT * FROM jobs'),[])
        (self.identity/'token').write_text('replacement-token-'+'z'*50)
        self.assertEqual(self.request()[0],401)
        self.assertEqual(self.request(token='replacement-token-'+'z'*50)[0],202)
        (self.identity/'token').write_text('x'*256+'\n'+'hidden-trailing-value')
        self.assertEqual(self.request(token='x'*256)[0],503)
        (self.identity/'token').chmod(0o644)
        self.assertEqual(self.request()[0],503)
        with self.assertRaises(OSError):read_token(self.identity)

    def test_json_size_method_path_and_transfer_encoding_rejected(self):
        self.assertEqual(self.request(raw=b'{}')[0],400)
        self.assertEqual(self.request(raw=b'x'*65537)[0],413)
        self.assertEqual(self.request(method='GET')[0],405)
        self.assertEqual(self.request(path='/metrics')[0],404)
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Transfer-Encoding':'chunked'}
        self.assertEqual(self.request(headers=headers)[0],400)
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'text/plain'}
        self.assertEqual(self.request(headers=headers)[0],400)
        self.assertEqual(self.fixture.rows('SELECT * FROM jobs'),[])

    def test_storage_failure_and_capacity_are_retryable_not_acknowledged(self):
        for error in (sqlite3.OperationalError('private-storage-secret'),QueueFull('private-capacity')):
            with patch.object(self.fixture.inbox,'accept',side_effect=error):
                code,value=self.request()
                self.assertEqual(code,503)
                self.assertNotIn('private',json.dumps(value))
        self.assertEqual(self.fixture.rows('SELECT * FROM jobs'),[])

    def test_credential_rate_limit_and_duplicate_headers(self):
        self.server.calls=60
        self.assertEqual(self.request()[0],429)
        self.server.calls=0
        connection=http.client.HTTPSConnection('localhost',self.server.server_port,context=self.context,timeout=4)
        try:
            connection.putrequest('POST','/v1/alerts')
            for name,value in [('Authorization','Bearer '+self.token),('Authorization','Bearer '+self.token),('Content-Length','2'),('Content-Type','application/json')]:
                connection.putheader(name,value)
            connection.endheaders(b'{}')
            response=connection.getresponse();response.read();self.assertEqual(response.status,401)
        finally:connection.close()

    def test_untrusted_tls_ca_does_not_connect(self):
        connection=http.client.HTTPSConnection('localhost',self.server.server_port,context=ssl.create_default_context(),timeout=4)
        try:
            with self.assertRaises(ssl.SSLError):connection.request('POST','/v1/alerts')
        finally:connection.close()
        self.assertEqual(self.fixture.rows('SELECT * FROM jobs'),[])


if __name__=='__main__':unittest.main()
