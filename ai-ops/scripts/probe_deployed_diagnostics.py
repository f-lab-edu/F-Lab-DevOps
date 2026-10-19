"""배포된 진단 Pod의 권한·통신·합성 저장 자료·토큰 교체만 검사한다."""

from __future__ import annotations

import argparse
import datetime
import errno
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import time


IDENTITY = Path('/var/run/ai-ops/kubernetes')
API_HOST = 'kubernetes.default.svc'
DEPLOYMENT = '/apis/apps/v1/namespaces/url-shortener/deployments/url-shortener-api'


def api(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """현재 토큰을 매번 읽고 고정 API의 제한된 JSON 응답만 메모리에서 처리한다."""
    token = (IDENTITY / 'token').read_text().strip()
    if not token or len(token) > 8192 or any(c in token for c in '\r\n'):
        raise ValueError('토큰 형식 오류')
    context = ssl.create_default_context(cafile=str(IDENTITY / 'ca.crt'))
    connection = http.client.HTTPSConnection(API_HOST, timeout=5, context=context)
    try:
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None,
                           headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError('응답 크기 한도 초과')
        return response.status, json.loads(raw)
    finally:
        connection.close()


def access_review(group: str, resource: str, verb: str, namespace: str,
                  name: str = '', subresource: str = '') -> bool:
    """대상 리소스를 변경하지 않고 자신의 권한만 질의한다."""
    attributes = {'group': group, 'resource': resource, 'verb': verb, 'namespace': namespace}
    if name:
        attributes['name'] = name
    if subresource:
        attributes['subresource'] = subresource
    status, result = api('POST', '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews', {
        'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
        'spec': {'resourceAttributes': attributes}})
    if status != 201 or result.get('status', {}).get('evaluationError'):
        raise ValueError('권한 질의 실패')
    return result['status']['allowed'] is True


def runtime_checks() -> dict:
    """실제 읽기·거부 응답과 권한 질의 결과를 본문 없이 기록한다."""
    checks = {}
    for key, path, expected in [
        ('target_deployment', DEPLOYMENT, 200),
        ('target_service', '/api/v1/namespaces/url-shortener/services/url-shortener-svc', 200),
        ('argo_application', '/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/url-shortener', 200),
        ('secret_denied', '/api/v1/namespaces/url-shortener/secrets?limit=1', 403),
        ('other_namespace_denied', '/api/v1/namespaces/kube-system/pods?limit=1', 403),
    ]:
        status, _ = api('GET', path)
        checks[key] = {'status': status, 'passed': status == expected}
    reviews = [
        ('pods_list', '', 'pods', 'list', 'url-shortener', '', '', True),
        ('pod_logs', '', 'pods', 'get', 'url-shortener', 'check', 'log', True),
        ('deployment_patch_denied', 'apps', 'deployments', 'patch', 'url-shortener', 'url-shortener-api', '', False),
        ('deployment_delete_denied', 'apps', 'deployments', 'delete', 'url-shortener', 'url-shortener-api', '', False),
        ('pod_create_denied', '', 'pods', 'create', 'url-shortener', '', '', False),
        ('exec_denied', '', 'pods', 'create', 'url-shortener', 'check', 'exec', False),
        ('own_namespace_secret_denied', '', 'secrets', 'list', 'ai-ops', '', '', False),
        ('other_deployment_denied', 'apps', 'deployments', 'get', 'url-shortener', 'unregistered', '', False),
        ('cluster_nodes_denied', '', 'nodes', 'list', '', '', '', False),
        ('role_update_denied', 'rbac.authorization.k8s.io', 'roles', 'update', 'url-shortener', 'ai-ops-read-target', '', False),
        ('token_create_denied', '', 'serviceaccounts', 'create', 'ai-ops', 'ai-ops-investigator', 'token', False),
    ]
    for key, group, resource, verb, namespace, name, subresource, expected in reviews:
        allowed = access_review(group, resource, verb, namespace, name, subresource)
        checks[key] = {'allowed': allowed, 'passed': allowed is expected}
    checks['uid'] = {'value': os.geteuid(), 'passed': os.geteuid() == 10001}
    try:
        with Path('/opt/ai-ops/.b3-root-write-probe').open('x'):
            pass
    except OSError as error:
        checks['root_read_only'] = {'passed': error.errno == errno.EROFS}
    else:
        Path('/opt/ai-ops/.b3-root-write-probe').unlink()
        checks['root_read_only'] = {'passed': False}
    return checks


def storage_check(probe_id: str, write: bool) -> dict:
    """운영 보고서와 분리한 합성 표식만 저장하고 재연결 뒤 읽는다."""
    if not re.fullmatch(r'[a-f0-9]{16}', probe_id):
        raise ValueError('합성 표식 형식 오류')
    os.umask(0o077)
    path = Path('/data/incidents') / ('.b3-volume-' + probe_id + '.sqlite3')
    if write:
        if path.exists():
            raise ValueError('기존 검사 파일 덮어쓰기 거부')
        with sqlite3.connect(path) as database:
            assert database.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
            database.execute('PRAGMA synchronous=FULL')
            database.execute('CREATE TABLE probe (marker TEXT NOT NULL)')
            database.execute('INSERT INTO probe VALUES (?)', (probe_id,))
            database.commit()
    with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as database:
        passed = database.execute('SELECT marker FROM probe').fetchall() == [(probe_id,)]
        passed = passed and database.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        mode = database.execute('PRAGMA journal_mode').fetchone()[0]
    private = path.stat().st_mode & 0o777 == 0o600
    return {'sqlite_marker': {'passed': passed and mode == 'wal' and private,
                             'journal_mode': mode, 'private_permissions': private}}


def token_rotation() -> dict:
    """원문·해시를 출력하지 않고 같은 Pod의 파일 교체와 새 토큰 인증을 확인한다."""
    previous = (IDENTITY / 'token').read_bytes()
    started = time.monotonic()
    for _ in range(25):
        time.sleep(30)
        if (IDENTITY / 'token').read_bytes() != previous:
            status, _ = api('GET', DEPLOYMENT)
            return {'rotation': {'passed': status == 200, 'status': status,
                                 'elapsed_seconds': round(time.monotonic() - started)}}
    return {'rotation': {'passed': False, 'reason': 'bounded_wait_expired'}}


def main() -> int:
    parser = argparse.ArgumentParser(description='배포된 진단 Pod의 제한된 검증')
    parser.add_argument('--mode', choices=('runtime', 'storage-write', 'storage-read', 'rotation'), required=True)
    parser.add_argument('--probe-id', default='')
    args = parser.parse_args()
    try:
        if args.mode == 'runtime':
            checks = runtime_checks()
        elif args.mode == 'rotation':
            checks = token_rotation()
        else:
            checks = storage_check(args.probe_id, args.mode == 'storage-write')
        passed = bool(checks) and all(value['passed'] for value in checks.values())
    except Exception as error:
        # 인증값이나 응답 본문이 섞일 수 있는 예외 메시지는 남기지 않는다.
        checks = {'incomplete': {'passed': False, 'error_type': type(error).__name__}}
        passed = False
    print(json.dumps({'record_type': 'ai_ops_deployed_diagnostics_probe', 'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), 'mode': args.mode, 'checks': checks, 'passed': passed}, ensure_ascii=False), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
