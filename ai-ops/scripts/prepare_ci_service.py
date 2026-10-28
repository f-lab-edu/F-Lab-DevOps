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
    parser.add_argument('--normal-binding', type=Path, help='현재 Pod에 고정된 정상 관측 CI binding')
    parser.add_argument('--projection-read', action='store_true', help='별도 Secret을 가진 읽기 전용 실습 사건 조회 sidecar를 준비')
    args = cli_paths(parser, parser.parse_args(), {'manifest': 'input', 'output': 'output',
                                                   'normal_binding': 'input'})
    bundle = json.loads(args.manifest.read_text()); validate_lab(bundle)
    config = next(x['data'] for x in bundle['items'] if x['kind']=='ConfigMap')
    config = dict(config)
    bindings = json.loads(config['bindings.json'])
    bindings['bindings'][0]['argo']['namespace'] = 'argocd'
    config['bindings.json'] = json.dumps(bindings)
    if args.normal_binding is not None:
        from ai_ops.normal_ci_contract import NormalCIBinding
        config['normal-ci-binding.json'] = NormalCIBinding.model_validate_json(
            args.normal_binding.read_bytes()).model_dump_json()
    policy = next(x for x in bundle['items'] if x['kind']=='NetworkPolicy' and x['metadata']['name']=='lab-collector')
    cidrs = [x['ipBlock']['cidr'] for x in policy['spec']['egress'][0]['to']]
    write_private_json(args.output, build_service(args.image, config, cidrs,
                                                 projection_secret_name='ci-projection-identity' if args.projection_read else None))
    print('비활성 배포 정의 생성 완료. 토큰·Secret 생성·서버 적용은 실행하지 않았습니다.')


if __name__ == '__main__':
    main()
