"""운영 worker와 분리한 실습 CI 전달기·worker의 비활성 기본 배포 정의를 만든다."""

from copy import deepcopy
import ipaddress
import json
import re

NAMESPACE = 'ai-ci'


def build_service(image, config, api_cidrs):
    """검증 이미지를 사용하며 토큰 값 없이 Secret 이름만 참조한다."""
    if not re.fullmatch(r'716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener-practice-ai-ops@sha256:[a-f0-9]{64}', image):
        raise ValueError('전용 digest 이미지 필요')
    for cidr in api_cidrs:
        n = ipaddress.ip_network(cidr, strict=True)
        if n.version != 4 or n.prefixlen != 32 or not n.is_private:
            raise ValueError('내부 API /32 필요')
    if not api_cidrs:
        raise ValueError('현재 API 주소 필요')
    from ai_ops.b_contracts import BindingRegistry, BRuntime, MetricCatalog
    required_config = ('bindings.json', 'runtime.json', 'metric-queries.json')
    if set(config) not in (set(required_config), set(required_config) | {'normal-ci-binding.json'}):
        raise ValueError('실습 CI 설정 항목이 유효하지 않음')
    BindingRegistry.model_validate_json(config['bindings.json'])
    BRuntime.model_validate_json(config['runtime.json'])
    MetricCatalog.model_validate_json(config['metric-queries.json'])
    normal_binding = config.get('normal-ci-binding.json')
    if normal_binding is not None:
        from ai_ops.normal_ci_contract import NormalCIBinding
        NormalCIBinding.model_validate_json(normal_binding)
    bindings = json.loads(config['bindings.json'])
    if len(bindings['bindings']) != 1 or bindings['bindings'][0]['target']['namespace'] != 'ai-ops-lab':
        raise ValueError('실습 대상만 허용')
    items = []
    def obj(kind, name, spec=None, ns=NAMESPACE, api='v1', **extra):
        d = {'apiVersion': api, 'kind': kind, 'metadata': {'name': name, 'labels': {'app.kubernetes.io/part-of': 'ai-ci'}}}
        if ns: d['metadata']['namespace'] = ns
        if spec is not None: d['spec'] = spec
        d.update(extra); items.append(d); return d
    obj('Namespace', NAMESPACE, ns=None)['metadata']['labels'].update({'pod-security.kubernetes.io/enforce': 'restricted'})
    for name in ['ci-worker', 'ci-relay']:
        obj('ServiceAccount', name, automountServiceAccountToken=False)
    obj('ConfigMap', 'ci-config', data={key: deepcopy(config[key]) for key in required_config})
    if normal_binding is not None:
        obj('ConfigMap', 'ci-normal-binding', data={'normal-ci-binding.json': normal_binding})
    for ns, role in [('ai-ops-lab', 'lab-read-only'), ('argocd', 'b6-lab-argo-read')]:
        obj('RoleBinding', 'ci-worker-read', ns=ns, api='rbac.authorization.k8s.io/v1',
            roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': role},
            subjects=[{'kind': 'ServiceAccount', 'name': 'ci-worker', 'namespace': NAMESPACE}])
    obj('PersistentVolumeClaim', 'ci-queue', {'accessModes': ['ReadWriteOnce'], 'storageClassName': 'ai-ops-gp3-encrypted', 'resources': {'requests': {'storage': '1Gi'}}})
    security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
    for name, module in [('ci-worker', 'worker_runtime'), ('ci-relay', 'ci_relay')]:
        worker = name == 'ci-worker'
        mounts = [{'name': 'config', 'mountPath': '/etc/ai-ops', 'readOnly': True}, {'name': 'data', 'mountPath': '/data'}, {'name': 'tmp', 'mountPath': '/tmp'}]
        config_volume = ({'name': 'config', 'projected': {'sources': [
            {'configMap': {'name': 'ci-config'}}, {'configMap': {'name': 'ci-normal-binding'}}]}}
            if normal_binding is not None else
            {'name': 'config', 'configMap': {'name': 'ci-config'}})
        volumes = [config_volume, {'name': 'data', 'persistentVolumeClaim': {'claimName': 'ci-queue'}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '32Mi'}}]
        container = {'name': name, 'image': image, 'command': ['python', '-m', 'ai_ops.' + module],
                     'args': ['--retention', '--metrics'] if worker else ['--config', '/etc/ai-ops', '--store', '/data/incidents', '--watch'],
                     'securityContext': security, 'resources': {'requests': {'cpu': '50m', 'memory': '96Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
                     'env': [{'name': 'HOME', 'value': '/tmp'}], 'volumeMounts': mounts}
        if worker:
            mounts.append({'name': 'identity', 'mountPath': '/var/run/ai-ops/kubernetes', 'readOnly': True})
            volumes.append({'name': 'identity', 'projected': {'sources': [{'serviceAccountToken': {'path': 'token', 'expirationSeconds': 600}}, {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}})
            for key in ['readinessProbe', 'livenessProbe']:
                container[key] = {'exec': {'command': ['python', '-m', 'ai_ops.worker_runtime', '--probe']}, 'periodSeconds': 30, 'timeoutSeconds': 5, 'failureThreshold': 3}
            container['startupProbe'] = {'exec': {'command': ['python', '-m', 'ai_ops.worker_runtime', '--probe']}, 'periodSeconds': 10, 'failureThreshold': 18, 'timeoutSeconds': 5}
        else:
            mounts.append({'name': 'github', 'mountPath': '/etc/ai-ci-github', 'readOnly': True})
            volumes.append({'name': 'github', 'secret': {'secretName': 'ci-relay-github', 'defaultMode': 288}})
            container['env'].append({'name': 'AI_CI_GITHUB_TOKEN_FILE', 'value': '/etc/ai-ci-github/token'})
            if normal_binding is not None:
                container['args'] += ['--normal-binding', '/etc/ai-ops/normal-ci-binding.json']
        pod = {'serviceAccountName': name, 'automountServiceAccountToken': False, 'enableServiceLinks': False,
               'terminationGracePeriodSeconds': 90, 'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001, 'fsGroup': 10001, 'fsGroupChangePolicy': 'OnRootMismatch', 'seccompProfile': {'type': 'RuntimeDefault'}},
               'containers': [container], 'volumes': volumes}
        if not worker:
            pod['affinity'] = {'podAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': [{'labelSelector': {'matchLabels': {'app': 'ci-worker'}}, 'topologyKey': 'kubernetes.io/hostname'}]}}
        obj('Deployment', name, {'replicas': 0, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': {'app': name}}, 'template': {'metadata': {'labels': {'app': name}}, 'spec': pod}}, api='apps/v1')
    dns = {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}, 'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}], 'ports': [{'port': 53, 'protocol': p} for p in ['UDP', 'TCP']]}
    obj('NetworkPolicy', 'ci-deny', {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}, api='networking.k8s.io/v1')
    for name in ['ci-worker', 'ci-relay']:
        egress = [dns]
        if name == 'ci-worker':
            egress += [{'to': [{'ipBlock': {'cidr': c}} for c in api_cidrs], 'ports': [{'port': 443}]}, {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'monitoring'}}, 'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'prometheus'}}}], 'ports': [{'port': 9090}]}]
        else:
            egress += [{'to': [{'ipBlock': {'cidr': '0.0.0.0/0', 'except': ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16', '127.0.0.0/8']}}], 'ports': [{'port': 443}]}]
        obj('NetworkPolicy', name, {'podSelector': {'matchLabels': {'app': name}}, 'policyTypes': ['Egress'], 'egress': egress}, api='networking.k8s.io/v1')
    return {'apiVersion': 'v1', 'kind': 'List', 'items': items}
