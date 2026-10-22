"""현재 실습 설정으로 토큰 값 없는 비활성 CI 서비스 배포 정의를 만든다."""

import argparse
import json
from pathlib import Path
from ai_ops.ci_service import build_service
from ai_ops.b6_lab import validate_lab
from ai_ops.path_policy import cli_paths, write_private_json


def main():
    parser = argparse.ArgumentParser(description='실습 CI worker·전달기 배포 입력 생성; 실제 적용 없음')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {'manifest': 'input', 'output': 'output'})
    bundle = json.loads(args.manifest.read_text()); validate_lab(bundle)
    config = next(x['data'] for x in bundle['items'] if x['kind']=='ConfigMap')
    config = dict(config)
    bindings = json.loads(config['bindings.json'])
    bindings['bindings'][0]['argo']['namespace'] = 'argocd'
    config['bindings.json'] = json.dumps(bindings)
    policy = next(x for x in bundle['items'] if x['kind']=='NetworkPolicy' and x['metadata']['name']=='lab-collector')
    cidrs = [x['ipBlock']['cidr'] for x in policy['spec']['egress'][0]['to']]
    write_private_json(args.output, build_service(args.image, config, cidrs))
    print('비활성 배포 정의 생성 완료. 토큰·Secret 생성·서버 적용은 실행하지 않았습니다.')


if __name__ == '__main__':
    main()
