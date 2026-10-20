"""실제 chart 경보 label·기존 알림 분기 보존을 렌더링으로 검사한다."""

import json
from pathlib import Path
import subprocess
import unittest
from ai_ops.alert_routing import build_routing

ROOT=Path(__file__).resolve().parents[2]


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


def check():
    case=AlertChartTest()
    case.test_default_labels_unchanged_and_enabled_labels_cover_five_only()
    case.test_enable_without_target_or_invalid_environment_rejected()
    base=yaml_value((ROOT/'cluster-addons/prometheus/values.yaml').read_text())[0]
    result=build_routing(base)
    assert result['alertmanager']['config']['receivers'][:-1]==base['alertmanager']['config']['receivers']
    assert result['alertmanager']['config']['route']['routes'][0]==base['alertmanager']['config']['route']['routes'][0]
    print('B-05 실제 chart 검사 통과: 서비스 경보 5개 명시적 대상·Watchdog 불변·기존 알림 보존·잘못된 활성 설정 거부. 실제 전송은 미검증.')


if __name__=='__main__':check()
