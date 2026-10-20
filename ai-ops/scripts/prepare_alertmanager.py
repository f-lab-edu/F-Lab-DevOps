"""기존 알림을 보존하는 경보 수신 분기 초안을 생성한다. 배포하지 않는다."""

import argparse
import json
from pathlib import Path
import subprocess

from ai_ops.alert_routing import build_routing
from ai_ops.path_policy import cli_paths, write_private_json

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description='AI·Slack·Watchdog의 검토용 경보 분기 초안 생성')
    parser.add_argument('--output',type=Path,required=True)
    args = cli_paths(parser,parser.parse_args(),{'output':'output'})
    try:
        raw = (ROOT/'cluster-addons/prometheus/values.yaml').read_text()
        parsed = subprocess.run(['ruby','-ryaml','-rjson','-e','puts JSON.generate(YAML.load(STDIN.read))'],input=raw,text=True,capture_output=True,check=True)
        result = build_routing(json.loads(parsed.stdout))
        write_private_json(args.output,result)
        print('검토용 Alertmanager 분기 초안 생성 완료. TLS·인증 Secret·수신기 검증 전에는 적용하지 마세요.')
    except Exception:
        raise SystemExit('분기 초안 생성 실패: 기존 수신자·Watchdog·Slack·group_by 설정을 확인하세요.') from None


if __name__=='__main__':main()
