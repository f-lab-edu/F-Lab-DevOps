"""Actual kubectl exec refresh against loopback TLS only; synthetic credentials."""
import argparse
import base64
from contextlib import redirect_stderr
from io import StringIO
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

from ai_ops.c_trial_lease_client import CONTEXT, LEASE_PATH, TrialLeaseClient, LeaseRequestError

CANARY='LOCAL-AUTH-CANARY'


def check_case(binary, pin, scenario, legacy=False):
    with tempfile.TemporaryDirectory(prefix='c04-auth-', dir='/tmp') as folder:
        root=Path(folder)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost',
            '-keyout',str(root/'key'),'-out',str(root/'cert')],check=True,capture_output=True,timeout=15)
        (root/'scenario').write_text(scenario)
        credential=root/'credential.py'
        credential.write_text('''from pathlib import Path
from datetime import datetime,timedelta,timezone
import json,os,sys,time
root=Path(__file__).parent;p=root/'count';n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))
scenario=(root/'scenario').read_text()
if n>1 and scenario=='refresh_failure':
 print('LOCAL-AUTH-CANARY',file=sys.stderr);sys.exit(1)
if n>1 and scenario=='refresh_stall':
 parent=os.getppid();end=time.monotonic()+3.4
 while os.getppid()==parent and time.monotonic()<end:time.sleep(.01)
print(json.dumps({'apiVersion':'client.authentication.k8s.io/v1beta1','kind':'ExecCredential',
 'status':{'token':'LOCAL-AUTH-CANARY-'+str(n),
 'expirationTimestamp':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat().replace('+00:00','Z')}}))
''')
        state={'apiVersion':'coordination.k8s.io/v1','kind':'Lease','metadata':{
            'name':'b6-scenario-lock','namespace':'ai-ops-lab','uid':'local-auth-fixture','resourceVersion':'1'},'spec':{}}
        phase={'reject':False};requests=[];stop=threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def handle_request(self):
                assert self.path==LEASE_PATH
                if self.command=='PUT':self.rfile.read(int(self.headers['Content-Length']))
                # Only boolean authentication outcomes and method are retained.
                fresh=self.headers.get('Authorization')=='Bearer '+CANARY+'-2'
                requests.append((self.command,fresh))
                denied=phase['reject'] and (not fresh or scenario=='persistent')
                code=401 if denied else 200
                value={'kind':'Status','reason':'Unauthorized','message':CANARY} if denied else state
                if phase['reject'] and not denied:
                    if scenario=='retry_stall':stop.wait(3.4)
                    if scenario=='uid_changed':value={**state,'metadata':{**state['metadata'],'uid':'changed'}}
                payload=json.dumps(value).encode()
                try:
                    self.send_response(code);self.send_header('Content-Length',str(len(payload)))
                    self.end_headers();self.wfile.write(payload)
                except OSError:pass
            do_GET=do_PUT=handle_request
            def log_message(self,*_):pass
        class Server(http.server.ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                # Closing the tested proxy may reset a local keepalive socket.
                if not isinstance(sys.exception(), OSError):super().handle_error(request, client_address)
        server=Server(('127.0.0.1',0),Handler)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(root/'cert',root/'key')
        server.socket=tls.wrap_socket(server.socket,server_side=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        config={'apiVersion':'v1','kind':'Config','current-context':CONTEXT,
            'contexts':[{'name':CONTEXT,'context':{'cluster':'local','user':'local'}}],
            'clusters':[{'name':'local','cluster':{'server':'https://localhost:'+str(server.server_port),
                'certificate-authority-data':base64.b64encode((root/'cert').read_bytes()).decode()}}],
            'users':[{'name':'local','user':{'exec':{'apiVersion':'client.authentication.k8s.io/v1beta1',
                'command':sys.executable,'args':[str(credential)],'interactiveMode':'Never'}}}]}
        kubeconfig=root/'config';kubeconfig.write_text(json.dumps(config));kubeconfig.chmod(0o600)
        c=TrialLeaseClient(kubeconfig,'local-auth-fixture',trace_binary=binary,trace_sha256=pin)
        captured=StringIO();failure=None;result=None
        try:
            with redirect_stderr(captured):
                c.start();assert int((root/'count').read_text())==1
                phase['reject']=True;started=time.monotonic()
                try:result=c.replace(state) if scenario=='put' else c.get()
                except LeaseRequestError as error:failure=error.diagnostic
                elapsed=time.monotonic()-started
                if legacy:
                    assert failure and failure['http_status']==401
                    assert int((root/'count').read_text())==2, 'refresh_not_completed'
                    assert len(requests)==2, 'legacy_unexpected_retry'
                    assert c.get()['metadata']['uid']=='local-auth-fixture', 'refreshed_credentials_unusable'
                elif scenario=='recovered':
                    assert failure is None and result==state
                    assert c.last_auth_retry['outcome']=='recovered'
                    assert len(requests)==3 and int((root/'count').read_text())==2
                else:
                    assert failure is not None, 'unsafe_failure_accepted'
                    assert len(requests)<=3
                    if scenario=='put':assert requests[-1][0]=='PUT' and len(requests)==2
                    if scenario in ('refresh_stall','retry_stall'):
                        assert failure['cause']=='timeout' and 2.8<=elapsed<3.3
                    if scenario=='uid_changed':assert failure['cause']=='resource_identity'
                    if scenario=='persistent':assert failure['http_status']==401 and len(requests)==3
                assert elapsed<3.3
                assert CANARY not in captured.getvalue()
                c.close();assert c.process.poll() is not None and not c.socket_path.parent.exists()
                if c.stage_collector is not None:
                    c.stage_collector.thread.join(timeout=1);assert not c.stage_collector.thread.is_alive()
            return {'scenario':scenario,'legacy':legacy,'status':'passed','elapsed_seconds':round(elapsed,6),
                'exec_calls':int((root/'count').read_text()),'http_requests_including_warmup':len(requests),
                'failure_http_status':failure.get('http_status') if failure else None,
                'failure_cause':failure.get('cause') if failure else None,
                'secret_canary_absent':True,'proxy_socket_reader_closed':True}
        finally:c.close();stop.set();server.shutdown();server.server_close();thread.join(2)


def main():
    p=argparse.ArgumentParser();p.add_argument('--binary');p.add_argument('--sha256')
    p.add_argument('--legacy',action='store_true');args=p.parse_args();os.umask(0o077)
    scenarios=['recovered'] if args.legacy else ['recovered','persistent','refresh_failure','refresh_stall','retry_stall','uid_changed','put']
    print(json.dumps({'scope':'loopback_only','cloud_calls':0,'status':'passed',
        'cases':[check_case(args.binary,args.sha256,s,args.legacy) for s in scenarios]},indent=2))
if __name__=='__main__':main()
