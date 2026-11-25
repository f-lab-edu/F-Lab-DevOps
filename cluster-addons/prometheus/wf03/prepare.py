"""Offline candidate generator; never applies Helm, contacts Slack or reads Secrets."""
import argparse
import copy
import json
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parent


def notice_template(source_alias, investigation_channel):
    if not isinstance(source_alias,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}',source_alias):
        raise ValueError('invalid configured source alias')
    if not isinstance(investigation_channel,str) or not re.fullmatch(r'#[a-z0-9_-]{1,80}',investigation_channel):
        raise ValueError('invalid investigation channel display name')
    return (ROOT/'notice.tmpl').read_text().replace('__SOURCE_ALIAS__',source_alias).replace('__INVESTIGATION_CHANNEL__',investigation_channel)


LEGACY_EVENT_SERVICE = 'ai-ops-webhook.ai-ops.svc'
SERVICE = re.compile(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.svc')


def build_candidate(values, *, source_alias, investigation_channel, event_service=LEGACY_EVENT_SERVICE):
    """``event_service`` is the in-cluster DNS name of the event receiver Service
    (``<name>.<namespace>.svc``); it is both the URL host and the TLS server_name."""
    if not isinstance(event_service,str) or not SERVICE.fullmatch(event_service):
        raise ValueError('invalid event receiver service name')
    template = notice_template(source_alias,investigation_channel)
    result = copy.deepcopy(values)
    am = result['alertmanager']
    if am.get('tplConfig') or am.get('useExistingSecret') or am.get('stringConfig'):
        raise ValueError('alternate config source requires separate review')
    config = am['config']; route = config['route']
    routes=route.get('routes',[])
    if (len(routes)!=3 or route.get('receiver')!='slack-notifications' or route.get('group_by')!=['...']
            or routes[0].get('matchers')!=['alertname="Watchdog"'] or routes[0].get('receiver')!='heartbeat-receiver'
            or routes[0].get('continue',False) is not False
            or routes[1]!={'matchers':['ai_ops="enabled"'],'receiver':'ai-investigation','continue':True}
            or routes[2]!={'receiver':'slack-notifications'} or routes[0].get('routes')):
        raise ValueError('unreviewed routing; refusing to replace')
    receivers=config['receivers']; names=[r['name'] for r in receivers]
    if any(names.count(n)!=1 for n in ('slack-notifications','heartbeat-receiver','ai-investigation')) or 'ai-events' in names:
        raise ValueError('unexpected receivers')
    slack=next(r for r in receivers if r['name']=='slack-notifications')
    legacy=next(r for r in receivers if r['name']=='ai-investigation')
    directory='/etc/alertmanager/secrets/ai-ops-alertmanager-auth'
    expected={'name':'ai-investigation','webhook_configs':[{
        'url':'https://ai-ops-webhook.ai-ops.svc:9443/v1/alerts','send_resolved':True,'timeout':'5s','max_alerts':32,
        'http_config':{'authorization':{'type':'Bearer','credentials_file':directory+'/token'},'follow_redirects':False,
                       'tls_config':{'ca_file':directory+'/ca.crt','server_name':'ai-ops-webhook.ai-ops.svc'}}}]}
    if legacy!=expected or am['alertmanagerSpec']['secrets'].count('ai-ops-alertmanager-auth')!=1:
        raise ValueError('unreviewed event TLS/auth boundary')
    if (len(slack.get('slack_configs',[]))!=1 or slack['slack_configs'][0].get('api_url_file')!='/etc/alertmanager/secrets/alertmanager-slack-secret/webhook-url'
            or set(slack['slack_configs'][0]) - {'api_url_file','title','text','send_resolved','channel'}
            or slack['slack_configs'][0].get('send_resolved') is not True):
        raise ValueError('unreviewed Slack destination')
    # Candidate only: explicit transition from the legacy automatic route to metadata intake.
    route['routes']=[routes[0],{'receiver':'slack-notifications','continue':True},{'receiver':'ai-events'}]
    legacy['name']='ai-events'; legacy['webhook_configs'][0]['url']=f'https://{event_service}:9443/v1/events'
    legacy['webhook_configs'][0]['http_config']['tls_config']['server_name']=event_service
    text=template+'{{ template "wf03.notice" . }}'
    # The upstream fallback renders all common labels even when title is overridden.
    slack['slack_configs'][0].update(title='경보 발생·해제 사실',text=text,
        fallback='경보 발생·해제 사실: 채널의 사건 참조와 상세 내용을 확인하세요.',
        title_link='',mrkdwn_in=[],link_names=False)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--values',type=Path,required=True)
    parser.add_argument('--source-alias',required=True)
    parser.add_argument('--investigation-channel',required=True)
    parser.add_argument('--event-service',default=LEGACY_EVENT_SERVICE,help='<service>.<namespace>.svc of the event receiver')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    value=build_candidate(yaml.safe_load(args.values.read_text()),source_alias=args.source_alias,
                          investigation_channel=args.investigation_channel,event_service=args.event_service)
    # Exclusive creation prevents silently overwriting a previously reviewed candidate.
    with args.output.open('x') as stream: json.dump(value,stream,ensure_ascii=False,indent=2)


if __name__=='__main__': main()
