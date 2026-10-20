"""기존 Slack·Watchdog를 보존하는 명시적 경보 분기 설정을 준비한다."""

from __future__ import annotations

import copy


def build_routing(values: dict) -> dict:
    """외부 호출 없이 검토할 values 복사본만 반환한다. 원본은 수정하지 않는다."""
    result = copy.deepcopy(values)
    alertmanager = result['alertmanager']
    config = alertmanager['config']
    route = config['route']
    receivers = config['receivers']
    names = [item['name'] for item in receivers]
    if names.count('slack-notifications') != 1 or names.count('heartbeat-receiver') != 1 or 'ai-investigation' in names:
        raise ValueError('기존 사람 알림 수신 설정을 확인해야 합니다')
    existing = route.get('routes', [])
    if (len(existing) != 1 or existing[0].get('matchers') != ['alertname="Watchdog"']
            or existing[0].get('receiver') != 'heartbeat-receiver'
            or existing[0].get('continue', False) is not False
            or route.get('receiver') != 'slack-notifications'
            or route.get('group_by') != ['...']):
        raise ValueError('검토하지 않은 경보 라우팅을 자동으로 덮어쓸 수 없습니다')
    secret = 'ai-ops-alertmanager-auth'
    secrets = alertmanager['alertmanagerSpec']['secrets']
    if secret not in secrets:
        secrets.append(secret)
    route['routes'] = [*existing,
        {'matchers':['ai_ops="enabled"'], 'receiver':'ai-investigation', 'continue':True},
        {'receiver':'slack-notifications'}]
    directory = '/etc/alertmanager/secrets/' + secret
    receivers.append({'name':'ai-investigation','webhook_configs':[{
        'url':'https://ai-ops-webhook.ai-ops.svc:9443/v1/alerts',
        'send_resolved':True, 'timeout':'5s', 'max_alerts':32,
        'http_config':{'authorization':{'type':'Bearer','credentials_file':directory+'/token'},
                       'follow_redirects':False,
                       'tls_config':{'ca_file':directory+'/ca.crt','server_name':'ai-ops-webhook.ai-ops.svc'}}}]})
    return result
