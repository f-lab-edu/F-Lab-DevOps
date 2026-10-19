"""운영자가 임시 점검 Pod에서 실행하는 제한된 통신 검사다. 인증값·응답 본문을 출력하지 않는다."""

from __future__ import annotations

import argparse
import datetime
import http.client
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import sys


IDENTITY = Path('/var/run/ai-ops/kubernetes')
API_HOST = 'kubernetes.default.svc'
PROM_HOST = 'kube-prometheus-stack-prometheus.monitoring.svc'
DEPLOYMENT_PATH = '/apis/apps/v1/namespaces/url-shortener/deployments/url-shortener-api'
TIMEOUT = 3


def tcp_result(address: str, port: int) -> str:
    """시간 초과만 차단 후보로 기록하고 연결 거부·경로 오류는 성공 처리하지 않는다."""
    try:
        with socket.create_connection((address, port), timeout=TIMEOUT):
            return 'connected'
    except (TimeoutError, socket.timeout):
        return 'timeout'
    except OSError:
        return 'connection_error'


def api_status(address: str, path: str) -> int:
    """API의 CA·이름을 검증하고 매번 다시 읽은 단기 토큰으로 고정 GET만 보낸다."""
    context = ssl.create_default_context(cafile=str(IDENTITY / 'ca.crt'))
    token = (IDENTITY / 'token').read_text().strip()
    if not token or any(c in token for c in '\r\n'):
        raise ValueError('인증 파일 형식 오류')
    with socket.create_connection((address, 443), timeout=TIMEOUT) as raw:
        with context.wrap_socket(raw, server_hostname=API_HOST) as connection:
            connection.sendall((f'GET {path} HTTP/1.1\r\nHost: {API_HOST}\r\nAuthorization: Bearer {token}\r\nConnection: close\r\n\r\n').encode('ascii'))
            response = http.client.HTTPResponse(connection)
            response.begin()
            return response.status


def main() -> int:
    parser = argparse.ArgumentParser(description='임시 Pod의 기본 차단과 최소 허용 경로만 검사한다')
    parser.add_argument('--phase', choices=('deny', 'allow'), required=True)
    parser.add_argument('--api-ip', action='append', required=True)
    args = parser.parse_args()
    addresses = [str(ipaddress.IPv4Address(value)) for value in args.api_ip]
    if not 1 <= len(addresses) <= 4:
        parser.error('API 주소는 1~4개만 허용한다')
    checks = {}
    try:
        if args.phase == 'deny':
            for address in ['172.20.0.1', *addresses]:
                value = tcp_result(address, 443)
                checks[f'api_{address}'] = {'result': value, 'passed': value == 'timeout'}
        else:
            resolved = sorted({entry[4][0] for entry in socket.getaddrinfo(API_HOST, 443, family=socket.AF_INET)})
            checks['dns_api'] = {'addresses': resolved, 'passed': resolved == ['172.20.0.1']}
            for address in [API_HOST, *addresses]:
                try:
                    status = api_status(address, DEPLOYMENT_PATH)
                    checks[f'api_{address}'] = {'status': status, 'passed': status == 200}
                except (OSError, ValueError, http.client.HTTPException) as error:
                    checks[f'api_{address}'] = {'error_type': type(error).__name__, 'passed': False}
            status = api_status(API_HOST, '/api/v1/namespaces/url-shortener/secrets?limit=1')
            checks['secrets_denied'] = {'status': status, 'passed': status == 403}
            connection = http.client.HTTPConnection(PROM_HOST, 9090, timeout=TIMEOUT)
            try:
                connection.request('GET', '/-/ready')
                status = connection.getresponse().status
            finally:
                connection.close()
            checks['prometheus_ready'] = {'status': status, 'passed': status == 200}
    except (OSError, ValueError, http.client.HTTPException) as error:
        # 예외 메시지에는 주소·인증값이 포함될 수 있어 형식 이름만 남긴다.
        checks['incomplete'] = {'error_type': type(error).__name__, 'passed': False}
    passed = bool(checks) and all(check['passed'] for check in checks.values())
    print(json.dumps({'record_type': 'ai_ops_temporary_network_probe', 'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), 'phase': args.phase, 'checks': checks, 'passed': passed}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
