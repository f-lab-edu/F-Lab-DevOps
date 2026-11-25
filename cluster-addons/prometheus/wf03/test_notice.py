"""Offline template/routing contracts. Set WF03_AMTOOL to pinned v0.32.0."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
from prepare import build_candidate, notice_template

ROOT=Path(__file__).resolve().parent


LIVE_EVENT_SERVICE='ai-ops-events.ai-ops.svc'  # WF10 namespace/name ai-ops (WF11 decision 2026-11-25)
LIVE_SOURCE_ALIAS='eks-seoul'


class CandidateFixture:
    def setUp(self):
        # The transition input: values.yaml as it was before WF11 (byte copy).
        self.values=yaml.safe_load((ROOT/'legacy-values.yaml').read_text())

    def candidate(self,values=None):
        return build_candidate(values or self.values,source_alias='lab-seoul',investigation_channel='#ai-ops')


class CandidateTest(CandidateFixture, unittest.TestCase):
    def test_preserves_slack_destination_heartbeat_inhibition_and_intervals(self):
        before=copy.deepcopy(self.values); new=self.candidate()
        self.assertEqual(self.values,before)
        a=before['alertmanager']; b=new['alertmanager']
        self.assertEqual(a['alertmanagerSpec'],b['alertmanagerSpec'])
        self.assertEqual(a['config']['inhibit_rules'],b['config']['inhibit_rules'])
        self.assertEqual(a['config']['global'],b['config']['global'])
        for key in ('group_by','group_wait','repeat_interval','receiver'):
            self.assertEqual(a['config']['route'][key],b['config']['route'][key])
        self.assertEqual(a['config']['route']['routes'][0],b['config']['route']['routes'][0])
        for name in ('heartbeat-receiver','null'):
            self.assertEqual(next(r for r in a['config']['receivers'] if r['name']==name),next(r for r in b['config']['receivers'] if r['name']==name))
        old=next(r for r in a['config']['receivers'] if r['name']=='slack-notifications')['slack_configs'][0]
        new=next(r for r in b['config']['receivers'] if r['name']=='slack-notifications')['slack_configs'][0]
        self.assertEqual(old['api_url_file'],new['api_url_file']); self.assertTrue(new['send_resolved'])
        self.assertEqual(new['mrkdwn_in'],[]); self.assertFalse(new['link_names'])
        self.assertNotIn('channel',new)

    def test_explicit_transition_keeps_two_independent_receivers(self):
        config=self.candidate()['alertmanager']['config']
        self.assertEqual(config['route']['routes'][1:], [{'receiver':'slack-notifications','continue':True},{'receiver':'ai-events'}])
        event=next(r for r in config['receivers'] if r['name']=='ai-events')['webhook_configs'][0]
        self.assertEqual(event['url'],'https://ai-ops-webhook.ai-ops.svc:9443/v1/events')
        self.assertFalse(event['http_config']['follow_redirects']);self.assertEqual(event['timeout'],'5s')
        self.assertNotIn('ai-investigation',[r['name'] for r in config['receivers']])

    def test_unreviewed_routes_and_tls_rejected_without_input_mutation(self):
        for mutation in (
            lambda v:v['alertmanager']['config']['route']['routes'][0].update({'continue':True}),
            lambda v:v['alertmanager']['config']['route']['routes'].append({'receiver':'other'}),
            lambda v:v['alertmanager']['config']['receivers'][-1]['webhook_configs'][0]['http_config']['tls_config'].update({'insecure_skip_verify':True}),
            lambda v:v['alertmanager']['config']['receivers'][2]['slack_configs'][0].update({'api_url':'https://secret.invalid/'}),
        ):
            value=copy.deepcopy(self.values);mutation(value); before=copy.deepcopy(value)
            with self.assertRaises(ValueError): self.candidate(value)
            self.assertEqual(value,before)
        with self.assertRaises(ValueError):self.candidate(self.candidate())

    def test_configuration_cannot_inject_template_or_mentions(self):
        for source,channel in [('{{bad}}','#ai-ops'),('a/b','#ai-ops'),('ok','<!channel>'),('ok','#bad name')]:
            with self.assertRaises(ValueError):notice_template(source,channel)

    def test_alternate_config_sources_and_extra_slack_templates_are_rejected(self):
        for field,value in [('tplConfig',True),('useExistingSecret',True),('stringConfig','external')]:
            config=copy.deepcopy(self.values);config['alertmanager'][field]=value
            with self.assertRaises(ValueError):self.candidate(config)
        config=copy.deepcopy(self.values)
        config['alertmanager']['config']['receivers'][2]['slack_configs'][0]['pretext']='{{ .CommonLabels }}'
        with self.assertRaises(ValueError):self.candidate(config)


class LiveValuesTest(CandidateFixture, unittest.TestCase):
    """values.yaml (what Argo deploys) must be exactly the reviewed transition of the legacy file."""
    def live(self):
        return yaml.safe_load((ROOT.parent/'values.yaml').read_text())

    def test_live_values_equal_the_generated_transition(self):
        expected=build_candidate(self.values,source_alias=LIVE_SOURCE_ALIAS,investigation_channel='#ai-ops',
                                 event_service=LIVE_EVENT_SERVICE)
        self.assertEqual(self.live(),expected)

    def test_live_values_route_every_non_watchdog_alert_to_the_wf10_receiver(self):
        config=self.live()['alertmanager']['config']
        self.assertEqual(config['route']['routes'][1:],[{'receiver':'slack-notifications','continue':True},{'receiver':'ai-events'}])
        event=next(r for r in config['receivers'] if r['name']=='ai-events')['webhook_configs'][0]
        self.assertEqual(event['url'],'https://ai-ops-events.ai-ops.svc:9443/v1/events')
        self.assertEqual(event['http_config']['tls_config']['server_name'],'ai-ops-events.ai-ops.svc')
        self.assertEqual(event['http_config']['authorization']['credentials_file'],
                         '/etc/alertmanager/secrets/ai-ops-alertmanager-auth/token')
        self.assertNotIn('ai_ops="enabled"',json.dumps(config['route']))
        self.assertIn('al1/eks-seoul/',json.dumps(config))

    def test_event_service_name_is_validated(self):
        for bad in ('ai-ops-events.ai-ops','https://x.y.svc','Ai.ai.svc','x.y.svc.cluster.local','a..svc'):
            with self.subTest(bad=bad),self.assertRaisesRegex(ValueError,'event receiver service'):
                build_candidate(self.values,source_alias='lab-seoul',investigation_channel='#ai-ops',event_service=bad)


@unittest.skipUnless(os.environ.get('WF03_AMTOOL'),'explicit local amtool binary required')
class LiveAlertmanagerTest(unittest.TestCase):
    """Pinned amtool against the deployed values.yaml config (not a candidate)."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.tool=os.environ['WF03_AMTOOL']
        self.assertIn('version 0.32.0',subprocess.check_output([self.tool,'--version'],stderr=subprocess.STDOUT,text=True))
        self.config=Path(self.temp.name)/'config.json'
        live=yaml.safe_load((ROOT.parent/'values.yaml').read_text())
        self.config.write_text(json.dumps(live['alertmanager']['config']))

    def test_live_config_checks_and_routes_the_lab_alert_to_ai_events(self):
        subprocess.run([self.tool,'check-config',str(self.config)],capture_output=True,check=True,timeout=10)
        for labels,expected in [(['alertname=Watchdog'],'heartbeat-receiver'),
                                (['alertname=LabDeploymentUnavailable','ai_ops=enabled','namespace=ai-ops-lab'],
                                 'slack-notifications,ai-events'),
                                (['alertname=HighErrorRate','ai_ops=enabled'],'slack-notifications,ai-events')]:
            subprocess.run([self.tool,'config','routes','test','--config.file='+str(self.config),
                            '--verify.receivers='+expected,*labels],capture_output=True,check=True,timeout=10)


@unittest.skipUnless(os.environ.get('WF03_AMTOOL'),'explicit local amtool binary required')
class ActualAlertmanagerTest(CandidateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.tool=os.environ['WF03_AMTOOL']
        version=subprocess.check_output([self.tool,'--version'],stderr=subprocess.STDOUT,text=True)
        self.assertIn('version 0.32.0',version)
        (self.directory/'notice.tmpl').write_text(notice_template('lab-seoul','#ai-ops'))
        self.config=self.directory/'config.json'
        self.config.write_text(json.dumps(self.candidate()['alertmanager']['config']))

    def render(self,alerts):
        data=self.directory/'data.json';data.write_text(json.dumps({'receiver':'slack-notifications','status':'firing','alerts':alerts}))
        return subprocess.check_output([self.tool,'template','render','--template.glob='+str(self.directory/'notice.tmpl'),
            '--template.text={{ template "wf03.notice" . }}','--template.data='+str(data)],text=True,timeout=10)

    def alert(self,**changes):
        value={'status':'firing','labels':{'alertname':'HighErrorRate','severity':'warning'},'annotations':{},
               'startsAt':'2026-11-18T21:00:00.123456789+09:00','endsAt':'2026-11-18T21:01:00+09:00','fingerprint':'0123456789abcdef'}
        value.update(changes);return value

    def test_real_config_and_routes_all_alerts_watchdog(self):
        subprocess.run([self.tool,'check-config',str(self.config)],capture_output=True,check=True,timeout=10)
        for labels,expected in [(['alertname=Watchdog','ai_ops=enabled'],'heartbeat-receiver'),
                                (['alertname=HighErrorRate','ai_ops=enabled'],'slack-notifications,ai-events'),
                                (['alertname=NodeDown'],'slack-notifications,ai-events')]:
            subprocess.run([self.tool,'config','routes','test','--config.file='+str(self.config),'--verify.receivers='+expected,*labels],capture_output=True,check=True,timeout=10)

    def test_real_nanosecond_offset_roundtrip_and_resolved(self):
        text=self.render([self.alert()])
        self.assertIn('al1/lab-seoul/0123456789abcdef/2026-11-18T12:00:00.123456789Z',text)
        same=self.render([self.alert(startsAt='2026-11-18T12:00:00.123456789Z')])
        self.assertEqual(text,same)
        resolved=self.render([self.alert(status='resolved')])
        self.assertIn('경보 해제',resolved);self.assertIn('해제 시각: 2026-11-18T12:01:00.000000000Z',resolved)

    def test_untrusted_annotation_and_label_not_forwarded(self):
        text=self.render([self.alert(annotations={'summary':'token=private-value','description':'<!channel> http://evil'},
            labels={'alertname':'<!channel>','severity':'<@USER>','namespace':'http://evil','service':'x'*1000})])
        for value in ('private-value','<!channel>','<@USER>','http://evil','x'*1000): self.assertNotIn(value,text)
        self.assertIn('미확인',text)

    def test_multi_alert_limit_and_invalid_fingerprint(self):
        alert=self.alert(labels={k:'x'*64 for k in ('alertname','cluster_id','namespace','workload','service','environment')})
        text=self.render([alert]*32)
        self.assertEqual(text.count('사건 참조: al1/'),3)
        self.assertIn('표시 상한',text);self.assertLess(len(text),3000)
        self.assertNotIn('al1/',self.render([self.alert(fingerprint='invalid')]))

    def test_slack_default_fallback_leak_and_candidate_inline_fields(self):
        data=self.directory/'unsafe.json'
        data.write_text(json.dumps({'status':'firing','receiver':'slack-notifications',
            'commonLabels':{'private_label':'synthetic-private-value'},'externalURL':'https://private.invalid/',
            'alerts':[self.alert()]}))
        def render(text):
            return subprocess.check_output([self.tool,'template','render','--template.glob='+str(self.directory/'notice.tmpl'),
                '--template.text='+text,'--template.data='+str(data)],text=True,timeout=10)
        self.assertIn('synthetic-private-value',render('{{ template "slack.default.fallback" . }}'))
        slack=next(r for r in self.candidate()['alertmanager']['config']['receivers'] if r['name']=='slack-notifications')['slack_configs'][0]
        output='\n'.join(render(slack[k]) for k in ('text','title','fallback'))
        self.assertEqual(slack['title_link'],'')
        self.assertNotIn('synthetic-private-value',output);self.assertNotIn('private.invalid',output)
        self.assertIn('al1/lab-seoul/0123456789abcdef/',output)


if __name__=='__main__':unittest.main()
