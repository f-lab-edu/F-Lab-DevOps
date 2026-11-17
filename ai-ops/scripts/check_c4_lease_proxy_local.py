"""실제 kubectl과 로컬 TLS API 대역만 사용한다. AWS/GitHub/실습 환경은 호출하지 않는다."""

from copy import deepcopy
import base64
import http.server
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time

from ai_ops.c_trial_lease_client import TrialLeaseClient, LeaseConflict, LeaseRequestError, LEASE_PATH, CONTEXT, _UnixConnection


def main():
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='c4-proxy-local-', dir='/tmp') as folder:
        root = Path(folder)
        (root/'tls.cnf').write_text('''[req]
distinguished_name=dn
x509_extensions=v3
prompt=no
[dn]
CN=localhost
[v3]
basicConstraints=critical,CA:TRUE
keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign
subjectAltName=DNS:localhost,IP:127.0.0.1
''')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-config', str(root/'tls.cnf'), '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem')],
            check=True, capture_output=True, timeout=15)
        count = root/'exec-count'
        credential = root/'credential.py'
        credential.write_text('''from pathlib import Path
from datetime import datetime,timedelta,timezone
import json
p=Path(__file__).with_name('exec-count')
p.write_text(str(int(p.read_text())+1 if p.exists() else 1))
print(json.dumps({'apiVersion':'client.authentication.k8s.io/v1beta1','kind':'ExecCredential',
 'status':{'token':'local-fixture-only','expirationTimestamp':(datetime.now(timezone.utc)+timedelta(seconds=6)).isoformat().replace('+00:00','Z')}}))
''')
        state = dict(apiVersion='coordination.k8s.io/v1', kind='Lease', metadata=dict(
            name='b6-scenario-lock', namespace='ai-ops-lab', uid='local-fixture', resourceVersion='1'), spec={})
        requests = []
        fault = {'headers_stall': False}
        lock = threading.Lock()
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def handle_request(self):
                nonlocal state
                if fault['headers_stall'] and self.path == LEASE_PATH:
                    time.sleep(3.25)  # 로컬에서만 upstream 응답 지연을 재현한다.
                with lock:
                    requests.append((self.command, self.path))
                    if self.headers.get('Authorization') != 'Bearer local-fixture-only':
                        code, value = 401, {'kind': 'Status', 'reason': 'Unauthorized'}
                    elif self.path != LEASE_PATH:
                        code, value = 404, {'kind': 'Status', 'reason': 'NotFound'}
                    else:
                        code, value = 200, state
                        if self.command == 'PUT':
                            desired = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                            if desired['metadata']['resourceVersion'] != state['metadata']['resourceVersion']:
                                code, value = 409, {'kind': 'Status', 'reason': 'Conflict'}
                            else:
                                state = deepcopy(desired)
                                state['metadata']['resourceVersion'] = str(int(state['metadata']['resourceVersion'])+1)
                                value = state
                    payload = json.dumps(value).encode()
                self.send_response(code); self.send_header('Content-Type', 'application/json')
                try:
                    self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload)
                except (OSError, ssl.SSLError):
                    pass  # 지연 시험에서 proxy가 먼저 연결을 닫을 수 있다.
            do_GET = do_PUT = handle_request
            def log_message(self, *_): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(root/'cert.pem', root/'key.pem')
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        config = {'apiVersion':'v1','kind':'Config','current-context':CONTEXT,
            'contexts':[{'name':CONTEXT,'context':{'cluster':'fixture','user':'fixture'}}],
            'clusters':[{'name':'fixture','cluster':{'server':'https://127.0.0.1:'+str(server.server_port),
                'certificate-authority-data':base64.b64encode((root/'cert.pem').read_bytes()).decode()}}],
            'users':[{'name':'fixture','user':{'exec':{'apiVersion':'client.authentication.k8s.io/v1beta1',
                'command':sys.executable,'args':[str(credential)],'interactiveMode':'Never'}}}]}
        kubeconfig = root/'kubeconfig'; kubeconfig.write_text(json.dumps(config)); kubeconfig.chmod(0o600)
        client = TrialLeaseClient(kubeconfig, 'local-fixture')
        try:
            client.start(); start = time.perf_counter()
            for _ in range(20): client.get()
            elapsed = time.perf_counter()-start
            assert int(count.read_text()) == 1, 'exec_credentials_not_reused'
            stale = client.get(); current = client.get(); current['metadata']['annotations'] = {'remote':'ack'}
            client.replace(current)
            try: client.replace(stale)
            except LeaseConflict: pass
            else: raise AssertionError('cas_conflict_not_preserved')
            fresh = client.get(); fresh['metadata']['annotations']['local'] = 'ack'; client.replace(fresh)
            before = len(requests)
            for method, path, host in [('GET','/api/v1/secrets','localhost'),
                    ('GET',LEASE_PATH.replace('coordination.k8s.io','coordinationXk8sYio'),'localhost'),
                    ('GET',LEASE_PATH+'/other','localhost'),('DELETE',LEASE_PATH,'localhost'),
                    ('PATCH',LEASE_PATH,'localhost'),('GET',LEASE_PATH,'untrusted.example')]:
                connection = _UnixConnection(client.socket_path)
                try:
                    connection.request(method, path, headers={'Host':host})
                    response = connection.getresponse(); response.read()
                    assert response.status == 403, 'proxy_filter_not_enforced'
                finally: connection.close()
            assert len(requests) == before, 'blocked_request_reached_upstream'
            time.sleep(6.1); client.get()
            assert int(count.read_text()) == 2, 'exec_credentials_not_refreshed'
            assert state['metadata']['annotations'] == {'remote':'ack','local':'ack'}
            fault['headers_stall'] = True
            delay_started = time.monotonic()
            try:
                try: client.get()
                except LeaseRequestError as error:
                    stalled = error.diagnostic
                    assert stalled['phase'] in ('response_headers','response_body','response_validation')
                    assert stalled['timeout_seconds'] == 3
                else: raise AssertionError('stalled_upstream_was_accepted')
            finally:
                fault['headers_stall'] = False
            assert time.monotonic()-delay_started < 4, 'original_read_timeout_was_extended'
            # 별도의 읽기에서만 정상 서버 복귀를 확인한다. 실패한 요청의 자동 재시도는 없다.
            assert client.get()['metadata']['uid'] == 'local-fixture'
            socket_path = client.socket_path; process = client.process
            client.close(); assert process.poll() is not None and not socket_path.parent.exists()
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-config', str(root/'tls.cnf'), '-keyout', str(root/'other-key.pem'),
                '-out', str(root/'other-cert.pem')], check=True, capture_output=True, timeout=15)
            bad_config = deepcopy(config)
            bad_config['clusters'][0]['cluster']['certificate-authority-data'] = base64.b64encode(
                (root/'other-cert.pem').read_bytes()).decode()
            bad_path = root/'untrusted-kubeconfig'; bad_path.write_text(json.dumps(bad_config)); bad_path.chmod(0o600)
            untrusted = TrialLeaseClient(bad_path, 'local-fixture')
            try:
                try: untrusted.start()
                except ValueError: pass
                else: raise AssertionError('invalid_ca_was_accepted')
                assert untrusted.closed and untrusted.process.poll() is not None
                assert not untrusted.socket_path.parent.exists()
            finally: untrusted.close()
            child_script = root/'terminate-client.py'
            child_state = root/'child-state.json'
            child_script.write_text('''from pathlib import Path
import json,sys,time
from ai_ops.c_trial_lease_client import TrialLeaseClient
c=TrialLeaseClient(sys.argv[1],'local-fixture',handle_sigterm=True)
try:
 c.start()
 state=Path(sys.argv[2]); temporary=state.with_suffix('.tmp')
 temporary.write_text(json.dumps({'pid':c.process.pid,'socket':str(c.socket_path)}))
 temporary.replace(state)
 while True: time.sleep(.1)
except ValueError: pass
finally: c.close()
''')
            child = subprocess.Popen([sys.executable, str(child_script), str(kubeconfig), str(child_state)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                end = time.monotonic()+12
                while not child_state.exists():
                    assert child.poll() is None and time.monotonic() < end, 'termination_fixture_failed'
                    time.sleep(.02)
                owned = json.loads(child_state.read_text())
                child.terminate(); assert child.wait(timeout=8) == 0
                assert not Path(owned['socket']).parent.exists(), 'sigterm_left_socket'
                try: os.kill(owned['pid'], 0)
                except ProcessLookupError: pass
                else: raise AssertionError('sigterm_left_proxy')
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=5)
            version = json.loads(subprocess.run(['kubectl', 'version', '--client', '-o', 'json'],
                check=True, capture_output=True, timeout=5).stdout)['clientVersion']['gitVersion']
            print(json.dumps({'status':'passed','upstream':'local_tls_fixture_only','cloud_calls':0,
                'model_calls':0,'warm_gets':20,'warm_gets_seconds':round(elapsed,6),
                'initial_exec_credential_calls':1,'after_expiry_exec_credential_calls':2,
                'tls_verified':True,'invalid_ca_rejected':True,'kubectl_version':version,
                'fixed_path_method_host_filters_verified':True,
                'cas_conflict_and_both_acks_verified':True,'proxy_and_socket_removed':True,
                'stalled_upstream_diagnostic':stalled,
                'original_three_second_read_limit_preserved':True,
                'sigterm_cleanup_verified':True}))
        finally:
            client.close(); server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__': main()
