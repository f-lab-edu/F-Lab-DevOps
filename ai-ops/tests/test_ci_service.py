"""실습 worker와 전달기의 권한·자격·저장소 경계를 검사한다."""
import json
from pathlib import Path
import unittest
from ai_ops.b6_lab import LabInputs, build_lab
from ai_ops.ci_service import build_service


class CIServiceTests(unittest.TestCase):
    def setUp(self):
        self.image = '716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops@sha256:' + 'a'*64
        inputs = LabInputs(app_image=self.image.replace('-practice-ai-ops', ''), collector_image=self.image,
                           postgres_image='docker.io/library/postgres@sha256:'+'b'*64,
                           redis_image='docker.io/library/redis@sha256:'+'c'*64, api_server_cidrs=['10.0.0.1/32'])
        self.config = next(x['data'] for x in build_lab(inputs)['items'] if x['kind']=='ConfigMap')

    def test_separate_identities_and_no_default_activation(self):
        docs = build_service(self.image, self.config, ['10.0.0.1/32'])['items']
        pods = {x['metadata']['name']:x for x in docs if x['kind']=='Deployment'}
        self.assertEqual(set(pods), {'ci-worker','ci-relay'})
        for x in pods.values():
            self.assertEqual(x['spec']['replicas'], 0)
            self.assertFalse(x['spec']['template']['spec']['automountServiceAccountToken'])
            self.assertEqual(x['spec']['template']['spec']['securityContext']['fsGroupChangePolicy'], 'OnRootMismatch')
        worker = pods['ci-worker']['spec']['template']['spec']
        relay = pods['ci-relay']['spec']['template']['spec']
        self.assertFalse(any('secret' in v for v in worker['volumes']))
        self.assertFalse(any('projected' in v for v in relay['volumes']))
        self.assertTrue(any('secret' in v for v in relay['volumes']))
        self.assertEqual(relay['containers'][0]['args'], ['--config', '/etc/ai-ops', '--store', '/data/incidents', '--watch'])
        self.assertEqual([x['metadata']['namespace'] for x in docs if x['kind']=='RoleBinding'], ['ai-ops-lab','argocd'])
        self.assertFalse(any(x['kind'] in ['ClusterRole','ClusterRoleBinding'] for x in docs))

    def test_worker_has_no_public_egress(self):
        docs = build_service(self.image, self.config, ['10.0.0.1/32'])['items']
        policy = next(x for x in docs if x['kind']=='NetworkPolicy' and x['metadata']['name']=='ci-worker')
        self.assertNotIn('0.0.0.0/0', json.dumps(policy))
        self.assertNotIn('169.254.169.254', json.dumps(policy))

    def test_wrong_image_target_and_api_rejected(self):
        with self.assertRaises(ValueError): build_service('python:latest',self.config,['10.0.0.1/32'])
        with self.assertRaises(ValueError): build_service(self.image,self.config,['8.8.8.8/32'])
        config=dict(self.config);config['bindings.json']=config['bindings.json'].replace('ai-ops-lab','url-shortener')
        with self.assertRaises(ValueError): build_service(self.image,config,['10.0.0.1/32'])
