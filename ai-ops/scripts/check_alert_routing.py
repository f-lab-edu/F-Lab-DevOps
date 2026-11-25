"""실제 chart 경보 label·기존 알림 분기 보존을 렌더링으로 검사한다."""

import json
from pathlib import Path
import subprocess
import unittest
from ai_ops.alert_routing import build_routing, verify_routing

ROOT=Path(__file__).resolve().parents[2]
# WF11(2026-11-25): values.yaml은 WF03 전환(ai-events, /v1/events) 이후 상태다. 전환 입력은 wf03/legacy-values.yaml.
LEGACY_VALUES=ROOT/'cluster-addons/prometheus/wf03/legacy-values.yaml'
EVENT_SERVICE='ai-ops-events.ai-ops.svc'


def yaml_value(raw):
    output=subprocess.run(['ruby','-ryaml','-rjson','-e','puts JSON.generate(YAML.load_stream(STDIN.read))'],
        input=raw,text=True,capture_output=True,check=True)
    return [value for value in json.loads(output.stdout) if value]


class AlertChartTest(unittest.TestCase):
    def render(self, *args):
        return subprocess.run(['helm','template','url-shortener',str(ROOT/'url-shortener/url-shortener-chart'),
            '--namespace','url-shortener','--set','serviceMonitor.enabled=true',*args],capture_output=True,text=True)

    def test_default_labels_unchanged_and_enabled_labels_cover_five_only(self):
        plain=self.render();self.assertEqual(plain.returncode,0,plain.stderr)
        enabled=self.render('--set','monitoring.aiOps.enabled=true','--set','monitoring.aiOps.clusterId=urlshortener','--set','monitoring.aiOps.environment=production')
        self.assertEqual(enabled.returncode,0,enabled.stderr)
        def rules(raw):return next(v for v in yaml_value(raw) if v['kind']=='PrometheusRule')['spec']['groups'][0]['rules']
        before=rules(plain.stdout);after=rules(enabled.stdout)
        self.assertEqual(len(before),len(after))
        for old,new in zip(before,after):
            self.assertEqual(old['expr'],new['expr'])
            self.assertEqual(old['annotations'],new['annotations'])
            if old['alert']=='Watchdog':self.assertEqual(old,new);continue
            self.assertNotIn('ai_ops',old['labels'])
            self.assertEqual(new['labels'],{**old['labels'],'ai_ops':'enabled','cluster_id':'urlshortener',
                'environment':'production','namespace':'url-shortener','service':'url-shortener-svc','workload':'url-shortener-api'})

    def test_enable_without_target_or_invalid_environment_rejected(self):
        for args in (['--set','monitoring.aiOps.enabled=true'],
                     ['--set','monitoring.aiOps.enabled=true','--set','monitoring.aiOps.clusterId=x','--set','monitoring.aiOps.environment=staging'],
                     ['--set-string','monitoring.aiOps.enabled=false']):
            self.assertNotEqual(self.render(*args).returncode,0)


def verify_event_transition(legacy, live):
    """전환 후 values가 legacy와 정해진 차이(사건 접수 주소·server_name·공지 템플릿)만 갖는지 확인한다.
    생성 결과와의 정확한 일치는 cluster-addons/prometheus/wf03 시험이 따로 강제한다."""
    assert {k:v for k,v in live.items() if k!='alertmanager'}=={k:v for k,v in legacy.items() if k!='alertmanager'}
    old,new=legacy['alertmanager'],live['alertmanager']
    assert new['alertmanagerSpec']==old['alertmanagerSpec']
    oc,nc=old['config'],new['config']
    assert nc['global']==oc['global'] and nc['inhibit_rules']==oc['inhibit_rules']
    assert {k:v for k,v in nc['route'].items() if k!='routes'}=={k:v for k,v in oc['route'].items() if k!='routes'}
    assert nc['route']['routes']==[oc['route']['routes'][0],{'receiver':'slack-notifications','continue':True},{'receiver':'ai-events'}]
    old_r={r['name']:r for r in oc['receivers']};new_r={r['name']:r for r in nc['receivers']}
    assert list(new_r)==['null','heartbeat-receiver','slack-notifications','ai-events']
    assert new_r['null']==old_r['null'] and new_r['heartbeat-receiver']==old_r['heartbeat-receiver']
    slack_old,slack_new=old_r['slack-notifications']['slack_configs'][0],new_r['slack-notifications']['slack_configs'][0]
    assert slack_new['api_url_file']==slack_old['api_url_file'] and slack_new['send_resolved'] is True
    assert 'channel' not in slack_new
    expected=json.loads(json.dumps(old_r['ai-investigation']['webhook_configs']))
    expected[0]['url']=f'https://{EVENT_SERVICE}:9443/v1/events'
    expected[0]['http_config']['tls_config']['server_name']=EVENT_SERVICE
    assert new_r['ai-events']['webhook_configs']==expected


def check():
    case=AlertChartTest()
    case.test_default_labels_unchanged_and_enabled_labels_cover_five_only()
    case.test_enable_without_target_or_invalid_environment_rejected()
    base=yaml_value((ROOT/'cluster-addons/prometheus/values.yaml').read_text())[0]
    if any(receiver['name']=='ai-events' for receiver in base['alertmanager']['config']['receivers']):
        legacy=yaml_value(LEGACY_VALUES.read_text())[0]
        verify_routing(legacy)  # 전환 입력은 기존 검토 구성 그대로여야 한다
        verify_event_transition(legacy,base)
    elif any(receiver['name']=='ai-investigation' for receiver in base['alertmanager']['config']['receivers']):
        verify_routing(base)
    else:
        result=build_routing(base)
        verify_routing(result)
        assert result['alertmanager']['config']['receivers'][:-1]==base['alertmanager']['config']['receivers']
        assert result['alertmanager']['config']['route']['routes'][0]==base['alertmanager']['config']['route']['routes'][0]
    print('B-05 실제 chart 검사 통과: 서비스 경보 5개 명시적 대상·Watchdog 불변·기존 알림 보존·잘못된 활성 설정 거부. 실제 전송은 미검증.')


if __name__=='__main__':check()
