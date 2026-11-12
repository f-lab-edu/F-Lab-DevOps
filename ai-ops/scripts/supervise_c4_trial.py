"""새 실행 승인 뒤 기존 자격 범위에서 감시/조회/예약 기동과 자기 자격 회수를 연결한다."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import base64, json, os, socket, subprocess, sys, threading, time, uuid

from ai_ops.c_operator import OperatorServer
from ai_ops.c_projection_client import ProjectionClient
from ai_ops.c_trial_execution import TrialPlan, TrialAuthorization
from ai_ops.c_trial_bundle import verify_runtime_bundle
from ai_ops.c_trial_guards import private_json
from ai_ops.c_trial_schedule import validate_operator_socket, verify_guard_sources, prepare_operator_kubeconfig
from ai_ops.contracts import _utc_datetime
from ai_ops.path_policy import cli_paths


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description='C-04 단일 실행의 감시와 자동 기동 감독')
    for name in ('plan','authorization','preflight','projection-probe'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('runtime-root','guard-source-root','run-dir'):
        parser.add_argument('--'+name,type=Path,required=True)
    for name in ('operator-state-root','kubeconfig'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--normal-run-id',type=int,required=True)
    parser.add_argument('--review-notifier-socket',required=True)
    parser.add_argument('--execute-approved-trial',action='store_true',required=True)
    return cli_paths(parser,parser.parse_args(argv),{
        **{k:'input' for k in ('plan','authorization','preflight','projection_probe')},
        'runtime_root':'directory','guard_source_root':'directory','run_dir':'store'})


def main():
    if not __debug__:
        raise ValueError('검증을 제거하는 Python 최적화 모드는 사용할 수 없습니다')
    args=parse_args()
    plan=TrialPlan.model_validate(private_json(args.plan))
    authorization=TrialAuthorization.model_validate(private_json(args.authorization))
    if (authorization.trial_id!=plan.trial_id or authorization.plan_hash!=plan.digest()
            or authorization.approved_at>plan.approval_cutoff
            or authorization.approved_at>datetime.now(timezone.utc)):
        raise ValueError('현재 계획의 새 실행 승인이 필요합니다')
    R=args.runtime_root.resolve(strict=True);G=args.guard_source_root.resolve(strict=True);P=args.run_dir
    verify_runtime_bundle(R)
    from ai_ops.c_trial_notice import require_bridge
    require_bridge(plan,args.review_notifier_socket)
    PY=sys.executable
    preflight=private_json(args.preflight)
    if preflight['plan_hash']!=plan.digest():raise ValueError('다른 계획의 사전 검증입니다')
    expected_hashes=preflight['checks']['rollback_workflow_hashes']
    validate_guard=verify_guard_sources(G,expected_hashes)
    # 긴 소켓/오래된 감시를 자격 발급보다 먼저 거부한다.
    args.operator_state_root=Path(args.operator_state_root)
    if args.operator_state_root!=Path('/private/tmp')/('c04-'+str(plan.trial_id)):
        raise ValueError('계획 ID에 고정된 전용 소켓 폴더가 필요합니다')
    args.operator_state_root.mkdir(mode=0o700,parents=False,exist_ok=True)
    validate_operator_socket(args.operator_state_root)
    if (args.operator_state_root/'operator.sock').exists():raise ValueError('이미 활성인 운영자 소켓입니다')
    def read_operator_config():
        result = subprocess.run(['kubectl','--kubeconfig',args.kubeconfig,'config','view',
                                 '--minify','--raw','-o','json'],capture_output=True,timeout=20)
        if result.returncode or len(result.stdout)>1024*1024:
            raise ValueError('operator_kubeconfig_read_failed')
        return prepare_operator_kubeconfig(json.loads(result.stdout),
            context='arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener')
    # 예정 시각까지 기다리기 전에 실제 실행 경로의 초기 조건을 검증한다.
    initial_config = read_operator_config()
    P.mkdir(mode=0o700,parents=False,exist_ok=False)
    if R/'ai-ops/scripts/run_c4_trial.py' != Path(__file__).resolve().with_name('run_c4_trial.py'):
        raise ValueError('검증된 같은 runtime에서 감독기를 실행해야 합니다')
    os.environ['AWS_PROFILE']='urlshortener-operator'
    os.environ['AWS_REGION']='ap-northeast-2'
    os.environ['KUBECONFIG']=str(args.kubeconfig)
    os.environ['PYTHONPATH']=str(R/'ai-ops/src')
    os.environ['AI_OPS_FILES_ROOT']=str(args.operator_state_root)
    BASE=['kubectl','--kubeconfig',os.environ['KUBECONFIG'],'--context','arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener','--request-timeout=15s']
    state={'trial_id':plan.trial_id,'plan_hash':plan.digest(),'stage':'preparing','pid':os.getpid(),'tokens':{},'runner_started':False,'remote_run_id':None}
    tokens={}; children=[]; server=None
    def save(name,value):
        path=P/name
        temporary=P/('.'+name+'.tmp')
        with temporary.open('w') as f:
            os.fchmod(f.fileno(),0o600);json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
        os.replace(temporary,path)
    def cmd(args,env=None,payload=None,timeout=30):
        r=subprocess.run(args,input=payload,capture_output=True,env=env,timeout=timeout)
        if r.returncode or len(r.stdout)>4*1024*1024:raise ValueError('command_failed_'+args[0]+'_'+str(r.returncode))
        return r.stdout
    def kube(ns,kind,name):return json.loads(cmd(BASE+['-n',ns,'get',kind,name,'-o','json']))
    def gh(path):return json.loads(cmd(['gh','api',path]))
    def core(*args):
        return cmd(['argocd','--config',str(P/'core-argo-config'),'--core',*args],
                   env={**os.environ,'KUBECONFIG':str(P/'core-kubeconfig.json')})
    def cleanup():
        state['cleanup_started']=True
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        if state.get('remote_run_id'):
            try:
                run=gh('repos/f-lab-edu/F-Lab-DevOps/actions/runs/'+str(state['remote_run_id']))
                if run['status']!='completed':cmd(['gh','run','cancel',str(state['remote_run_id']),'--repo','f-lab-edu/F-Lab-DevOps'])
                for _ in range(10):
                    run=gh('repos/f-lab-edu/F-Lab-DevOps/actions/runs/'+str(state['remote_run_id']))
                    if run['status']=='completed':break
                    time.sleep(.5)
                state['remote_stopped']=run['status']=='completed'
            except Exception:state['remote_cancel_needs_check']=True
        if state.get('secret_updated_at'):
            try:
                entry=gh('repos/f-lab-edu/F-Lab-DevOps/environments/ai-ops-lab-ci/secrets/C03_ROLLBACK_ARGOCD_TOKEN')
                if entry['updated_at']!=state['secret_updated_at']:raise ValueError('secret_changed')
                cmd(['gh','secret','delete','C03_ROLLBACK_ARGOCD_TOKEN','--repo','f-lab-edu/F-Lab-DevOps','--env','ai-ops-lab-ci'])
                state['secret_removed']=True
            except Exception:state['secret_removed']=False
        for name,claim in state['tokens'].items():
            try:
                obj=kube('argocd','appproject','b6-lab')
                role=next(r for r in obj['spec']['roles'] if r['name']==name)
                found=[t for t in role.get('jwtTokens',[]) if t.get('id')==claim['id']]
                if found:
                    assert len(found)==1 and found[0]['iat']==claim['iat'] and sum(t['iat']==claim['iat'] for t in role['jwtTokens'])==1
                    core('proj','role','delete-token','b6-lab',name,str(claim['iat']))
                current=kube('argocd','appproject','b6-lab')
                assert not any(t.get('id')==claim['id'] for r in current['spec']['roles'] for t in r.get('jwtTokens',[]))
                assert not any(t.get('id')==claim['id'] for v in current.get('status',{}).get('jwtTokensByRole',{}).values() for t in v.get('items',[]))
                claim['revoked']=True
            except Exception:claim['revoked']=False
        if server:
            server.shutdown();server.server_close()
        for name in ('core-kubeconfig.json','core-argo-config'):
            (P/name).unlink(missing_ok=True)
        (args.operator_state_root/'operator.sock').unlink(missing_ok=True)
        state['cleanup_complete']=(all(claim.get('revoked') is True for claim in state['tokens'].values())
            and (not state.get('secret_updated_at') or state.get('secret_removed') is True)
            and (not state.get('remote_run_id') or state.get('remote_stopped') is True))
        save('state.json',state)
    try:
        # 이번 승인과 같은 신규 폴더만 사용한다. 과거 결과/승인 재사용을 막는다.
        assert not (P/'execution-closed.json').exists() and not (P/'result.json').exists()
        assert authorization.trial_id==plan.trial_id and authorization.plan_hash==plan.digest()
        assert authorization.approved_at<=plan.approval_cutoff and authorization.approved_at<=datetime.now(timezone.utc)
        if datetime.now(timezone.utc)>plan.planned_at-timedelta(minutes=12):
            raise ValueError('preparation_start_deadline_missed')
        state['stage']='waiting_preparation_window'
        state['local_kubeconfig_verified']=True
        save('state.json',state)
        while datetime.now(timezone.utc)<plan.planned_at-timedelta(minutes=12):
            if (P/'abort').exists():raise ValueError('operator_abort')
            time.sleep(.25)
        config=read_operator_config()
        if config!=initial_config:raise ValueError('operator_kubeconfig_changed_since_start')
        save('core-kubeconfig.json',config)
        obj=kube('argocd','appproject','b6-lab');save('project-before.json',obj)
        secrets=gh('repos/f-lab-edu/F-Lab-DevOps/environments/ai-ops-lab-ci/secrets')
        assert all(s['name']!='C03_ROLLBACK_ARGOCD_TOKEN' for s in secrets['secrets'])
        for name in ('c03-sync','c03-rollback'):
            role=next(r for r in obj['spec']['roles'] if r['name']==name)
            assert not role.get('jwtTokens') and not role.get('groups')
            assert role['policies']==[f'p, proj:b6-lab:{name}, applications, {v}, b6-lab/lab-target, allow' for v in ('get','update','sync')]
            token_id='c04-'+str(plan.trial_id)+'-'+name+'-'+uuid.uuid4().hex
            token=core('proj','role','create-token','b6-lab',name,'--expires-in','45m','--id',token_id,'--token-only').decode().strip()
            claim=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'='*(-len(token.split('.')[1])%4)))
            assert claim['jti']==token_id and claim['sub']=='proj:b6-lab:'+name and 0<claim['exp']-claim['iat']<=2700
            state['tokens'][name]={'id':token_id,'iat':claim['iat'],'exp':claim['exp']};save('state.json',state)
            checks={}
            for verb,resource,target,want in [('get','applications','b6-lab/lab-target','yes'),('update','applications','b6-lab/lab-target','yes'),('sync','applications','b6-lab/lab-target','yes'),('delete','applications','b6-lab/lab-target','no'),('override','applications','b6-lab/lab-target','no'),('get','applications','default/url-shortener','no'),('update','applications','default/url-shortener','no'),('sync','applications','default/url-shortener','no'),('update','projects','b6-lab','no')]:
                got=cmd(['argocd','--config',str(P/'server-argo-config'),'--server','argocd.bidservice.store','--grpc-web','account','can-i',verb,resource,target],env={**os.environ,'ARGOCD_AUTH_TOKEN':token}).decode().strip()
                assert got==want;checks[verb+' '+resource+' '+target]=got
            tokens[name]=token;state['tokens'][name]['permission_checks']=checks
        cmd(['gh','secret','set','C03_ROLLBACK_ARGOCD_TOKEN','--repo','f-lab-edu/F-Lab-DevOps','--env','ai-ops-lab-ci'],payload=(tokens['c03-rollback']+'\n').encode())
        state['secret_updated_at']=gh('repos/f-lab-edu/F-Lab-DevOps/environments/ai-ops-lab-ci/secrets/C03_ROLLBACK_ARGOCD_TOKEN')['updated_at']
        binding=kube('ai-ci','configmap','ci-normal-binding')['data']['normal-ci-binding.json'];save('normal-binding.json',json.loads(binding))
        normal_run=gh('repos/f-lab-edu/F-Lab-DevOps/actions/runs/'+str(args.normal_run_id))
        assert normal_run['conclusion']=='success' and normal_run['head_sha']==plan.workflow_main
        state['normal_run_id']=normal_run['id'];state['normal_head']=normal_run['head_sha']
        from ai_ops.normal_ci_contract import NormalCIBinding
        from ai_ops.normal_ci_history import verify_normal_ci_history
        verified=verify_normal_ci_history(normal_run['id'],normal_run['head_sha'],NormalCIBinding.model_validate_json(binding))
        state['normal_receipt_verified']={'run_id':verified.run_id,'artifact_id':verified.artifact_id,'archive_digest':verified.archive_digest}
        current_config=kube('ai-ci','configmap','ci-config')['data']
        runtime=json.loads(current_config['runtime.json']);assert runtime['llm_enabled'] is False
        assert json.loads(current_config['bindings.json'])['bindings'][0]['argo']['repo_url']==plan.repository
        worker_pods=json.loads(cmd(BASE+['-n','ai-ci','get','pods','-l','app=ci-worker','-o','json']))['items'];assert len(worker_pods)==1
        worker=worker_pods[0];state['worker_pod']={'name':worker['metadata']['name'],'uid':worker['metadata']['uid']}
        assert all(c['ready'] for c in worker['status']['containerStatuses'])
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        f=(P/'projection-forward.log').open('wb');os.fchmod(f.fileno(),0o600)
        forward=subprocess.Popen(BASE+['--as=system:serviceaccount:ai-ci:c2-projection-operator','--as-group=system:serviceaccounts','--as-group=system:serviceaccounts:ai-ci','--as-group=system:authenticated','-n','ai-ci','port-forward','--address=127.0.0.1','pod/'+worker['metadata']['name'],str(port)+':9444'],stdout=f,stderr=subprocess.STDOUT)
        children.append(forward)
        end=time.monotonic()+20
        while time.monotonic()<end:
            if forward.poll() is not None:raise ValueError('projection_forward_stopped')
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.5):break
            except OSError:time.sleep(.2)
        identity=Path.home()/'.local/share/url-shortener-ai-ops/c2-projection-identity-20261028'
        projection=ProjectionClient(identity/'token',identity/'tls.crt',port=port)
        probe=private_json(args.projection_probe)
        assert projection.fetch(probe['incident_id'],probe['run_id']).bundle_hash==probe['bundle_hash']
        state_root=args.operator_state_root
        server=OperatorServer(state_root,None,149856960,projection_client=projection)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        state['operator_state_root']=str(state_root)
        rollback={'schema_version':2,'target':'urlshortener/ai-ops-lab/lab-api/practice','source_repository':plan.repository,
          'application_uid':plan.application_uid,'deployment_uid':plan.deployment_uid,'baseline_revision':plan.normal_revision,
          'failure_revision':plan.failure_revision,'normal_image':plan.normal_image,'failure_image':plan.failure_image,
          'deadline_utc':plan.rollback_deadline.isoformat()}
        validate_guard(rollback, now=plan.planned_at)
        save('rollback-plan.json',rollback)
        state['stage']='credentials_and_projection_ready_waiting_guard_window';save('state.json',state)
        while datetime.now(timezone.utc).timestamp()<plan.rollback_deadline.timestamp()-900:
            if (P/'abort').exists():raise ValueError('operator_abort')
            time.sleep(1)
        local_log=(P/'local-guard.log').open('wb');os.fchmod(local_log.fileno(),0o600)
        local=subprocess.Popen([PY,str(G/'ai-ops/scripts/run_c2_rollback_guard.py'),'--plan',str(P/'rollback-plan.json'),'--kubeconfig',str(P/'core-kubeconfig.json'),'--core'],env={**os.environ,'PYTHONPATH':str(G/'ai-ops/src')},stdout=local_log,stderr=subprocess.STDOUT)
        children.append(local);state['local_guard_pid']=local.pid
        old_pages=json.loads(cmd(['gh','api','repos/f-lab-edu/F-Lab-DevOps/actions/workflows/401385940/runs?per_page=100','--paginate','--slurp']))
        old=[run for page in old_pages for run in page['workflow_runs']]
        assert all(r['status']=='completed' for r in old)
        known={r['id'] for r in old}
        request={'ref':'main','inputs':{'source':'c03-isolated','prepare_only':'false',**{k:rollback[k] for k in ('baseline_revision','failure_revision','application_uid','deployment_uid','normal_image','failure_image','deadline_utc')}}}
        cmd(['gh','api','repos/f-lab-edu/F-Lab-DevOps/actions/workflows/401385940/dispatches','--method','POST','--input','-'],payload=json.dumps(request).encode())
        for _ in range(25):
            runs=gh('repos/f-lab-edu/F-Lab-DevOps/actions/workflows/401385940/runs?per_page=50')['workflow_runs']
            new=[r for r in runs if r['id'] not in known and r['event']=='workflow_dispatch' and r['head_sha']==plan.workflow_main]
            if len(new)==1:state['remote_run_id']=new[0]['id'];break
            time.sleep(2)
        assert state['remote_run_id'];state['stage']='guards_started_waiting_environment_approval';save('state.json',state)
        runner=None; last_poll=0; remote_active=False; guard_observed=False
        while datetime.now(timezone.utc).timestamp()<plan.rollback_deadline.timestamp()+600:
            if (P/'abort').exists():raise ValueError('operator_abort')
            observed=P/'remote-ready-observed.json'
            if observed.exists():
                marker=json.loads(observed.read_text())
                guard_observed=(marker.get('remote_run_id')==state['remote_run_id'] and marker.get('plan_hash')==plan.digest() and marker.get('actual_ready_marker_verified') is True and _utc_datetime(marker['observed_at'])<plan.approval_cutoff and _utc_datetime(marker['observed_at'])<=datetime.now(timezone.utc))
            if time.monotonic()-last_poll>5:
                jobs=gh('repos/f-lab-edu/F-Lab-DevOps/actions/runs/'+str(state['remote_run_id'])+'/jobs?per_page=100')
                remote_active=any(j.get('status')=='in_progress' and any(s.get('name')=='별도 기한 원복과 정상 상태 검증' and s.get('status')=='in_progress' for s in j.get('steps',[])) for j in jobs['jobs'])
                last_poll=time.monotonic()
            local_ready=local.poll() is None and 'C-02 별도 원복 감시 준비 완료' in (P/'local-guard.log').read_text()
            if runner is None and datetime.now(timezone.utc)>=plan.approval_cutoff:
                raise ValueError('guard_readiness_deadline_missed')
            if runner is None and guard_observed and remote_active and local_ready:
                ready_at=datetime.now(timezone.utc)
                assert ready_at<plan.approval_cutoff
                save('guard-evidence-live.json',{'plan_hash':plan.digest(),'actual_ready_markers_verified':True,
                 'rollback_deadline':plan.rollback_deadline.isoformat(),'ready_at':ready_at.isoformat(),
                 'workflow_main':plan.workflow_main,'local_pid':local.pid,'local_plan':str(P/'rollback-plan.json'),
                 'remote_run_id':state['remote_run_id'],'guard_source_root':str(G),
                 'guard_code_sha256':expected_hashes,'local_ready_log':str(P/'local-guard.log')})
                state['stage']='guards_ready_starting_automatic_scheduler'
                out=(P/'runner.log').open('wb');os.fchmod(out.fileno(),0o600)
                env={**os.environ,'C04_SYNC_ARGOCD_TOKEN':tokens['c03-sync'],'C04_ROLLBACK_ARGOCD_TOKEN':tokens['c03-rollback']}
                runner=subprocess.Popen([PY,str(R/'ai-ops/scripts/run_c4_trial.py'),'--execute-approved-trial',
                 '--plan',str(args.plan),'--authorization',str(args.authorization),
                 '--guard-evidence',str(P/'guard-evidence-live.json'),'--recovery-context',str(P/'recovery-context.json'),
                 '--review-notifier-socket',args.review_notifier_socket,
                 '--output',str(P/'result.json'),'--kubeconfig',os.environ['KUBECONFIG']],env=env,stdout=out,stderr=subprocess.STDOUT)
                children.append(runner);state['runner_pid']=runner.pid;state['runner_started']=True
                post_log=(P/'post.log').open('wb');os.fchmod(post_log.fileno(),0o600)
                post=subprocess.Popen([PY,str(R/'ai-ops/scripts/prepare_c4_recovery_pr.py'),
                    '--plan',str(args.plan),'--authorization',str(args.authorization),'--run-dir',str(P),
                    '--kubeconfig',os.environ['KUBECONFIG'],'--execute-approved-trial',
                    '--review-notifier-socket',args.review_notifier_socket],
                    env=os.environ.copy(),stdout=post_log,stderr=subprocess.STDOUT)
                children.append(post);state['post_pid']=post.pid
            if runner is not None and runner.poll() is not None:
                state['runner_exit']=runner.returncode;state['stage']='runner_finished';save('state.json',state);break
            save('state.json',state);time.sleep(1)
    except BaseException as exc:
        state['stage']='stopped';state['error_type']=type(exc).__name__
        state['error_code']=str(exc) if str(exc).startswith(('command_failed_','preparation_start_deadline_','guard_readiness_deadline_')) else 'preparation_or_execution_condition_failed'
        save('state.json',state)
    finally:
        cleanup()
    print(json.dumps({'stage':state['stage'],'runner_exit':state.get('runner_exit'),'cleanup_complete':state.get('cleanup_complete')},ensure_ascii=False))
    return 0 if state.get('runner_exit')==0 and state.get('cleanup_complete') else 1


if __name__=='__main__':
    raise SystemExit(main())
