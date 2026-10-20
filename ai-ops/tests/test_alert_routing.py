"""경보 분기·기존 알림 보존과 앱 chart 대상 label을 정적으로 검증한다."""

import copy
import json
import unittest

from ai_ops.alert_routing import build_routing, verify_routing

class AlertRoutingTest(unittest.TestCase):
    def setUp(self):
        self.base={'alertmanager':{'alertmanagerSpec':{'secrets':['slack-secret','heartbeat-secret']},'config':{'route':{'receiver':'slack-notifications','group_by':['...'],'routes':[{'matchers':['alertname=\"Watchdog\"'],'receiver':'heartbeat-receiver'}]},'receivers':[{'name':'slack-notifications','slack_configs':[{'send_resolved':True}]},{'name':'heartbeat-receiver','webhook_configs':[{'send_resolved':False}]}],'inhibit_rules':[{'equal':['namespace']}]}}}

    def selected(self, values, labels):
        # 이 시험은 현재 사용하는 동등 일치 matcher와 sibling continue만 평가한다.
        result=[]
        for route in values['alertmanager']['config']['route']['routes']:
            matches=all(labels.get(m.split('=',1)[0])==m.split('=',1)[1].strip('"') for m in route.get('matchers',[]))
            if matches:
                result.append(route['receiver'])
                if not route.get('continue',False):break
        return result or [values['alertmanager']['config']['route']['receiver']]

    def test_explicit_ai_then_slack_watchdog_only_heartbeat(self):
        original=copy.deepcopy(self.base)
        result=build_routing(self.base)
        self.assertEqual(self.base,original)
        self.assertEqual(self.selected(result,{'alertname':'HighErrorRate','ai_ops':'enabled'}),['ai-investigation','slack-notifications'])
        self.assertEqual(self.selected(result,{'alertname':'HighErrorRate'}),['slack-notifications'])
        self.assertEqual(self.selected(result,{'alertname':'Watchdog','ai_ops':'enabled'}),['heartbeat-receiver'])
        before=original['alertmanager'];after=result['alertmanager']
        self.assertEqual(after['config']['inhibit_rules'],before['config']['inhibit_rules'])
        self.assertEqual(after['config']['receivers'][:-1],before['config']['receivers'])
        self.assertEqual(after['config']['route']['routes'][0],before['config']['route']['routes'][0])
        self.assertEqual(after['alertmanagerSpec']['secrets'][:-1],before['alertmanagerSpec']['secrets'])
        webhook=after['config']['receivers'][-1]['webhook_configs'][0]
        self.assertTrue(webhook['send_resolved']);self.assertEqual(webhook['timeout'],'5s')
        self.assertTrue(webhook['url'].startswith('https://'))
        self.assertFalse(webhook['http_config']['follow_redirects'])
        self.assertNotIn('insecure_skip_verify',json.dumps(webhook))
        self.assertIn('credentials_file',webhook['http_config']['authorization'])

    def test_unknown_routes_or_missing_existing_receivers_fail_closed(self):
        for mutate in (lambda v:v['alertmanager']['config']['route']['routes'].append({'receiver':'other'}),
                       lambda v:v['alertmanager']['config']['receivers'].pop(),
                       lambda v:v['alertmanager']['config']['route']['routes'][0].update(continue_=True,receiver='slack-notifications')):
            value=copy.deepcopy(self.base);mutate(value)
            with self.assertRaises(ValueError):build_routing(value)
        with self.assertRaises(ValueError):build_routing(build_routing(self.base))

    def test_activated_routing_rejects_auth_or_sibling_drift(self):
        value=build_routing(self.base)
        original=copy.deepcopy(value)
        verify_routing(value)
        self.assertEqual(value,original)
        for mutate in (lambda v:v['alertmanager']['config']['route']['routes'][1].update({'continue':False}),
                       lambda v:v['alertmanager']['config']['receivers'][-1]['webhook_configs'][0]['http_config']['tls_config'].update({'insecure_skip_verify':True}),
                       lambda v:v['alertmanager']['config']['route']['routes'].pop(),
                       lambda v:v['alertmanager']['alertmanagerSpec']['secrets'].remove('ai-ops-alertmanager-auth')):
            changed=copy.deepcopy(value);mutate(changed)
            with self.assertRaises(ValueError):verify_routing(changed)



if __name__=='__main__':unittest.main()
