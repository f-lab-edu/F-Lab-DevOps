"""승인된 작성 App으로 단일 격리 저장소의 정상 기준선 PR만 생성한다."""

import base64
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from urllib.parse import quote, urlencode

REPO = 'joungGo/c03-execution-lab'
REPO_ID = 1452572723
APP_ID = 5696038
INSTALLATION_ID = 180499782
BOT = 'c03-execution-lab-writer[bot]'
BOT_ID = 354870297
FILE = 'ai-ops/lab/values.yaml'
PREFIX = '/repos/'+REPO
PERMISSIONS = {'contents':'write','metadata':'read','pull_requests':'write'}
KEY = Path.home()/'.local/share/url-shortener-ai-ops/c03-app-keys/writer.pem'
LIMIT = 2 * 1024 * 1024


class ApiError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__('GitHub 응답 거부')


def api(method, path, credential, payload=None, missing=False):
    """고정 GitHub HTTPS에만 요청하며 자격·오류 본문을 저장하거나 출력하지 않는다."""
    connection = http.client.HTTPSConnection('api.github.com', timeout=12)
    try:
        connection.request(method, path, body=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+credential,'Accept':'application/vnd.github+json',
                     'X-GitHub-Api-Version':'2026-03-10','User-Agent':'c04-normal-baseline-preparation',
                     'Content-Type':'application/json'})
        response = connection.getresponse()
        raw = response.read(LIMIT+1)
        if len(raw)>LIMIT:raise ValueError('GitHub 응답 크기 초과')
        if missing and response.status==404:return None
        if not 200<=response.status<300:raise ApiError(response.status)
        return json.loads(raw) if raw else {}
    finally:
        connection.close()


def require(value):
    if not value:raise ValueError('원본·범위·동일성 검증 거부')


def encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=')


def jwt():
    """개인 키는 openssl만 읽으며 서명·JWT는 프로세스 메모리에만 둔다."""
    info=KEY.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid()
            and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1)
    now=int(time.time())
    message=b'.'.join((encoded(b'{"alg":"RS256","typ":"JWT"}'),
        encoded(json.dumps({'iat':now-60,'exp':now+540,'iss':str(APP_ID)},separators=(',',':')).encode())))
    signature=subprocess.run(['openssl','dgst','-sha256','-sign',str(KEY)],input=message,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,check=True).stdout
    return (message+b'.'+encoded(signature)).decode()
