"""승인된 현재 실습의 C-02 조사에서 새 복구 PR·정확한 리뷰를 실행기로 연결한다."""

from pathlib import Path
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from urllib.parse import quote
import argparse,base64,json,os,re,signal,subprocess,time

from ai_ops.c_operator import _client
from ai_ops.c_failure_handoff import FailureHandoff
from ai_ops.c_trial_execution import TrialPlan
from ai_ops.c_trial_source import TrialSource
from ai_ops.c_trial_transport import C4TrialTransport
from ai_ops.c_recovery import (IncidentProjection,RecoveryRequest,RecoveryDraftStore,draft_verified_proposal,_encoded_values)
from ai_ops.c_recovery_proof import assess_proposal_recheck
from ai_ops.c_approval import prepare_pr_link,verify_review,ApprovalRejected,ApprovalGitHubReader
from ai_ops.path_policy import write_private_json


def main():
    if not __debug__:raise ValueError("검증을 제거하는 최적화 모드는 사용할 수 없습니다")
    from ai_ops.c_trial_execution import TrialAuthorization
    from ai_ops.c_trial_guards import private_json
    from ai_ops.c_trial_bundle import verify_runtime_bundle
    from ai_ops.c_trial_review_window import require_review_notice, review_in_time
    parser=argparse.ArgumentParser(description='현재 실습의 실제 조사와 복구 PR 연결')
    for name in ('plan','authorization','run-dir','kubeconfig'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--review-notifier-socket',required=True)
    parser.add_argument('--execute-approved-trial',action='store_true',required=True)
    args=parser.parse_args()
    P=args.run_dir;R=Path(__file__).resolve().parents[2]
    verify_runtime_bundle(R)
    plan=TrialPlan.model_validate(private_json(args.plan))
    authorization=TrialAuthorization.model_validate(private_json(args.authorization))
    if (authorization.trial_id!=plan.trial_id or authorization.plan_hash!=plan.digest()
            or authorization.approved_at>plan.approval_cutoff or authorization.approved_at>datetime.now(timezone.utc)):
        raise ValueError('현재 계획의 실제 실행 승인이 필요합니다')
    os.environ['AWS_PROFILE']='urlshortener-operator'
    os.environ['KUBECONFIG']=str(args.kubeconfig)
    state={'trial_id':plan.trial_id,'stage':'waiting_failure_handoff'}
    def save(name,value):
        if name == 'post-state.json':
            steps = value.setdefault('stage_history', [])
            if not steps or steps[-1]['stage'] != value['stage']:
                steps.append({'stage': value['stage'], 'at': datetime.now(timezone.utc).isoformat()})
        path=P/name; tmp=P/('.'+name+'.tmp')
        with tmp.open('w') as f:os.fchmod(f.fileno(),0o600);json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
        os.replace(tmp,path)
    def run(args,payload=None,timeout=20):
        r=subprocess.run(args,input=payload,capture_output=True,timeout=timeout)
        if r.returncode or len(r.stdout)>2*1024*1024:raise ValueError('command_failed_'+args[0]+'_'+str(r.returncode))
        return r.stdout
    BASE=['kubectl','--kubeconfig',os.environ['KUBECONFIG'],'--context','arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener','--request-timeout=15s']
    def wait():
        if datetime.now(timezone.utc)>=plan.manual_recovery_at:raise ValueError('manual_recovery_cutoff_reached')
        time.sleep(.5)
    def stop_pending(_signum, _frame):
        raise KeyboardInterrupt('stop_pending_recovery_steps')
    # Raising in Python preserves the installation-token revocation finally block.
    signal.signal(signal.SIGTERM, stop_pending)
    try:
        from ai_ops.c_trial_read_cache import warm_trial_reads
        from ai_ops.normal_ci_contract import NormalCIBinding
        prep=private_json(P/'state.json')
        caches=warm_trial_reads(plan,NormalCIBinding.model_validate(private_json(P/'normal-binding.json')),
            prep['normal_run_id'],prep['normal_head'])
        state['stage']='static_content_warmed_waiting_failure';save('post-state.json',state)
        while not (P/'result-handoff.json').exists():wait()
        raw=json.loads((P/'result-handoff.json').read_text());handoff=FailureHandoff.model_validate(raw['failure_handoff'])
        assert handoff.trial_id==plan.trial_id
        prep=json.loads((P/'state.json').read_text());worker=prep['worker_pod']
        state['stage']='waiting_capture_job';save('post-state.json',state)
        job_id=None
        while job_id is None:
            got=subprocess.run(BASE+['-n','ai-ci','logs','job/c2-source-capture-'+str(plan.trial_id),'--pod-running-timeout=1s'],capture_output=True,timeout=8)
            if got.returncode==0:
                try:
                    v=json.loads(got.stdout);job_id=v.get('job_id')
                    if job_id is not None:assert re.fullmatch(r'job-[a-f0-9]{32}',job_id)
                except ValueError:pass
            if job_id is None:wait()
        state['job_id']=job_id;state['stage']='waiting_authenticated_incident';save('post-state.json',state)
        query="import sqlite3,json,sys;d=sqlite3.connect('file:/data/incidents/queue/jobs.sqlite3?mode=ro',uri=True);d.row_factory=sqlite3.Row;r=d.execute('SELECT state,incident_id,run_id,bundle_hash FROM jobs WHERE job_id=?',(sys.argv[1],)).fetchone();print(json.dumps(dict(r) if r else None));d.close()"
        while True:
            current=json.loads(run(BASE+['-n','ai-ci','get','pod',worker['name'],'-o','json']))
            assert current['metadata']['uid']==worker['uid']
            metadata=json.loads(run(BASE+['-n','ai-ci','exec',worker['name'],'-c','ci-worker','--','python','-c',query,job_id]))
            if metadata and metadata.get('incident_id') and metadata.get('run_id'):break
            if metadata and metadata.get('state') in ('failed','cancelled','needs_review'):raise ValueError('investigation_not_ready')
            wait()
        save('queue-metadata.json',metadata)
        op=Path(prep['operator_state_root']);os.environ['AI_OPS_FILES_ROOT']=str(op)
        shown=_client(op/'operator.sock','show',metadata['incident_id'],metadata['run_id']);assert shown['status']=='ok'
        projection=IncidentProjection.model_validate(shown['projection']);assert projection.image_failure is not None
        requested=_client(op/'operator.sock','request',metadata['incident_id'],metadata['run_id']);assert requested['status']=='ok'
        request=RecoveryRequest.model_validate(requested['request'])
        context=SimpleNamespace(kubeconfig=os.environ['KUBECONFIG'],recovery_repository='joungGo/c03-execution-lab',
          normal_binding=P/'normal-binding.json',normal_run_id=prep['normal_run_id'],normal_head=prep['normal_head'],
          operator_state_root=op,incident_id=metadata['incident_id'],incident_run_id=metadata['run_id'],request_id=request.request_id)
        transport=C4TrialTransport(context.kubeconfig,image_check=lambda p:False,guard_check=lambda p:False,merge_session=None)
        source=TrialSource(context,handoff,transport,clock=lambda:datetime.now(timezone.utc),**caches)
        state['stage']='fresh_c02_proposal_recheck';save('post-state.json',state)
        first,_=source.collect();second,snapshot=source.collect()
        readiness=assess_proposal_recheck(first,second)
        if not readiness.get('proposal_ready'):save('proposal-blocked.json',readiness);raise ValueError('fresh_proposal_not_ready')
        proposal=draft_verified_proposal(request,projection,readiness,snapshot['values_bytes'])
        drafts=RecoveryDraftStore(op);drafts.save(request,proposal)
        write_private_json(P/'proposal.json',proposal.model_dump(mode='json'));write_private_json(P/'proposal-audit.json',readiness)
        third,_=source.collect();refreshed=assess_proposal_recheck(second,third)
        decision,link=prepare_pr_link(proposal,readiness,refreshed)
        assert decision['status']=='pr_candidate' and link is not None and link.pr_base_sha==plan.seed_main
        write_private_json(P/'pr-link.json',link.model_dump(mode='json'))
        state['stage']='creating_writer_pr';state['proposal_id']=proposal.proposal_id;save('post-state.json',state)
        deadline=plan.review_cutoff()
        def review_window():
            drafts.read(proposal.proposal_id, actor_github_id=149856960)
            if datetime.now(timezone.utc)>=deadline-timedelta(seconds=plan.minimum_review_seconds or 30):
                raise ValueError('manual_recovery_review_window_too_short')
        review_window()
        from ai_ops import c_trial_writer_api as api
        assertion=api.jwt();reg=api.api('GET','/app',assertion);install=api.api('GET','/app/installations/180499782',assertion)
        perms={'contents':'write','pull_requests':'write','metadata':'read'}
        assert reg['id']==5696038 and reg['slug']=='c03-execution-lab-writer' and reg['permissions']==perms
        assert install['app_id']==5696038 and install['repository_selection']=='selected' and install.get('suspended_at') is None and install['permissions']==perms
        token=None
        try:
            access=api.api('POST','/app/installations/180499782/access_tokens',assertion,{'repository_ids':[1452572723],'permissions':perms})
            token=access['token'];assert access['permissions']==perms
            expiry=datetime.fromisoformat(access['expires_at'].replace('Z','+00:00'));assert 0<(expiry-datetime.now(timezone.utc)).total_seconds()<=3600
            repos=api.api('GET','/installation/repositories?per_page=100',token);assert repos['total_count']==1 and repos['repositories'][0]['id']==1452572723
            prefix='/repos/joungGo/c03-execution-lab'
            assert api.api('GET',prefix+'/git/ref/heads/main',token)['object']['sha']==link.pr_base_sha
            branch='codex/c04-recovery-'+str(plan.trial_id)+'-'+proposal.proposal_id[-8:]
            review_window()
            api.api('POST',prefix+'/git/refs',token,{'ref':'refs/heads/'+branch,'sha':link.pr_base_sha})
            before=api.api('GET',prefix+'/contents/ai-ops/lab/values.yaml?ref='+link.pr_base_sha,token)
            assert base64.b64decode(before['content'])==_encoded_values(proposal.current_digest).encode()
            review_window()
            created=api.api('PUT',prefix+'/contents/ai-ops/lab/values.yaml',token,{
              'message':'C-04 격리 실습 이미지 복구\n\nRecovery-Proposal-SHA256: '+link.link_hash,
              'content':base64.b64encode(_encoded_values(proposal.desired_digest).encode()).decode(),
              'sha':before['sha'],'branch':branch})
            head=created['commit']['sha'];commit=api.api('GET',prefix+'/commits/'+head,token)
            assert commit['author']['id']==354870297 and [p['sha'] for p in commit['parents']]==[link.pr_base_sha]
            comp=api.api('GET',prefix+'/compare/'+link.pr_base_sha+'...'+head,token)
            assert comp['total_commits']==1 and len(comp['files'])==1 and comp['files'][0]['filename']=='ai-ops/lab/values.yaml'
            assert comp['files'][0]['additions']==comp['files'][0]['deletions']==1
            review_window()
            pr=api.api('POST',prefix+'/pulls',token,{'title':'C-04 격리 실습 이미지 복구 '+str(plan.trial_id),'head':branch,'base':'main',
              'body':'격리 실습 lab-api의 이미지 digest 한 필드를 정상 이미지로 복구합니다.\n\nRecovery-Proposal-SHA256: '+link.link_hash+'\n\nReview deadline '+deadline.isoformat()+'.'})
            pull=api.api('GET',prefix+'/pulls/'+str(pr['number']),token)
            assert pull['user']['id']==354870297 and pull['head']['sha']==head and pull['base']['sha']==link.pr_base_sha and pull['state']=='open'
            state.update(stage='pr_created_waiting_notice',pull_number=pr['number'],url=pr['html_url'],head_sha=head,review_deadline_utc=deadline.isoformat(),minimum_review_seconds=plan.minimum_review_seconds)
            save('pr-ready.json',state);save('post-state.json',state)
        finally:
            if token is not None:api.api('DELETE','/installation/token',token)
        from ai_ops.c_trial_notice import deliver_review_notice
        deliver_review_notice(plan,state,args.review_notifier_socket,P/'review-notified.json')
        state['stage']='waiting_exact_head_review_and_ci';save('post-state.json',state)
        number=state['pull_number'];prefix='repos/joungGo/c03-execution-lab'
        while datetime.now(timezone.utc)<deadline:
            if drafts.status(proposal.proposal_id, 149856960)['state'] != 'draft':
                state['stage']='proposal_cancelled_or_expired';save('post-state.json',state);break
            notice=None
            if plan.schema_version==2:
                notice_path=P/'review-notified.json'
                if notice_path.exists():
                    notice=private_json(notice_path)
                    require_review_notice(plan,notice,pull_number=number,head=head,now=datetime.now(timezone.utc))
                elif datetime.now(timezone.utc)>=deadline-timedelta(seconds=plan.minimum_review_seconds):
                    state['stage']='minimum_notification_window_missed';save('post-state.json',state);break
            reviews=json.loads(run(['gh','api',prefix+'/pulls/'+str(number)+'/reviews?per_page=100']))
            assert len(reviews)<100
            choices=[r for r in reviews if r['user']['id']==149856960 and r['state'] in ('APPROVED','CHANGES_REQUESTED','DISMISSED')]
            latest=sorted(choices,key=lambda r:(r['submitted_at'],r['id']))[-1]if choices else None
            approved=bool(latest)and latest['state']=='APPROVED'and latest['commit_id']==head and review_in_time(latest,plan)
            checks=json.loads(run(['gh','api',prefix+'/commits/'+head+'/check-runs?per_page=100']))
            assert checks['total_count']<100
            gates=[c for c in checks['check_runs'] if c['name']=='C03 Main PR Gate' and c['head_sha']==head and c['app']['id']==15368]
            if approved and (plan.schema_version==1 or notice is not None) and len(gates)==1 and gates[0]['status']=='completed' and gates[0]['conclusion']=='success':
                verify_review(drafts=drafts,proposal_id=proposal.proposal_id,pull_number=number,allowed_github_id=149856960,pr_author_id=354870297,pr_link=link)
                payload={'trial_id':plan.trial_id,'normal_binding':str(P/'normal-binding.json'),'normal_run_id':prep['normal_run_id'],
                 'normal_head':prep['normal_head'],'operator_state_root':str(op),'incident_id':metadata['incident_id'],
                 'incident_run_id':metadata['run_id'],'request_id':request.request_id,'proposal_id':proposal.proposal_id,
                 'proposal_audit':str(P/'proposal-audit.json'),'pr_link':str(P/'pr-link.json'),'pull_number':number}
                write_private_json(P/'recovery-context.json',payload);state['stage']='review_and_ci_verified_context_published';save('post-state.json',state);break
            time.sleep(1)
        else:state['stage']='review_deadline_passed_no_execution_context';save('post-state.json',state)
    except BaseException as exc:
        state.update(stage='stopped',error_type=type(exc).__name__,error_code=str(exc) if str(exc).startswith(('command_failed_','manual_recovery_','fresh_','investigation_','review_notice_','insufficient_actual_review_notice_window')) else 'source_or_pr_condition_failed')
        save('post-state.json',state)
    print(json.dumps(state,ensure_ascii=False))


if __name__=='__main__':main()
