"""기존 병합 App의 한 저장소 단기 자격으로 정확한 PR head만 병합한다."""

import base64
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time


REPOSITORY = "joungGo/c03-execution-lab"
APP_ID = 5696044
INSTALLATION_ID = 180499786
BOT_ID = 354870499
PERMISSIONS = {"contents": "write", "pull_requests": "read", "metadata": "read"}


class GitHubMergeSession:
    def __init__(self, key=None):
        self.key = key or Path.home()/".local/share/url-shortener-ai-ops/c03-app-keys/merge.pem"
        self.token = None
        self.identity = None

    def _api(self, method, path, credential, payload=None):
        connection = http.client.HTTPSConnection("api.github.com", timeout=12)
        try:
            connection.request(method, path, body=None if payload is None else json.dumps(payload).encode(),
                headers={"Authorization": "Bearer "+credential, "Accept": "application/vnd.github+json",
                    "User-Agent": "c04-isolated-executor", "X-GitHub-Api-Version": "2026-03-10",
                    "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(2*1024*1024+1)
            if not 200 <= response.status < 300 or len(raw) > 2*1024*1024:
                raise ValueError("고정 GitHub App 요청을 확인하지 못했습니다")
            return json.loads(raw) if raw else {}
        finally:
            connection.close()

    def _jwt(self):
        info = self.key.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError("기존 App 개인 키의 사용자 전용 보관 상태가 다릅니다")
        encoded = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=")
        now = int(time.time())
        message = b".".join((encoded(b'{"alg":"RS256","typ":"JWT"}'),
            encoded(json.dumps({"iat": now-60, "exp": now+540, "iss": str(APP_ID)},
                               separators=(",", ":")).encode())))
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.key)],
            input=message, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=True).stdout
        return (message+b"."+encoded(signature)).decode()

    def verify_registration(self):
        assertion = self._jwt()
        app = self._api("GET", "/app", assertion)
        installation = self._api("GET", f"/app/installations/{INSTALLATION_ID}", assertion)
        if (app.get("id") != APP_ID or app.get("slug") != "c03-execution-lab-merge"
                or app.get("owner", {}).get("login") != "joungGo" or app.get("permissions") != PERMISSIONS
                or installation.get("id") != INSTALLATION_ID or installation.get("app_id") != APP_ID
                or installation.get("account", {}).get("login") != "joungGo"
                or installation.get("repository_selection") != "selected"
                or installation.get("suspended_at") is not None
                or installation.get("permissions") != PERMISSIONS):
            raise ValueError("기존 병합 App·설치·최소 권한이 다릅니다")
        return {"app_id": APP_ID, "installation_id": INSTALLATION_ID, "permissions": PERMISSIONS}

    def __enter__(self):
        self.verify_registration()
        issued = self._api("POST", f"/app/installations/{INSTALLATION_ID}/access_tokens", self._jwt(),
                          {"repository_ids": [1452572723], "permissions": PERMISSIONS})
        self.token = issued["token"]
        try:
            expiry = datetime.fromisoformat(issued["expires_at"].replace("Z", "+00:00"))
            if (issued.get("permissions") != PERMISSIONS
                    or not 0 < (expiry-datetime.now(timezone.utc)).total_seconds() <= 3600):
                raise ValueError("병합 자격의 권한·짧은 유효기간이 다릅니다")
            repositories = self._api("GET", "/installation/repositories?per_page=100", self.token)
            repos = repositories["repositories"]
            if (repositories.get("total_count") != 1 or len(repos) != 1
                    or repos[0].get("id") != 1452572723 or repos[0].get("full_name") != REPOSITORY):
                raise ValueError("병합 자격의 대상 저장소가 다릅니다")
            bot = self._api("GET", "/users/c03-execution-lab-merge%5Bbot%5D", self.token)
            if bot.get("id") != BOT_ID or bot.get("type") != "Bot":
                raise ValueError("병합 실행 identity가 다릅니다")
            self.identity = {"kind": "github_app_installation", "repository": REPOSITORY,
                "actor_id": BOT_ID, "app_id": APP_ID, "installation_id": INSTALLATION_ID,
                "permissions": PERMISSIONS}
            return self
        except BaseException:
            self.close()
            raise

    def merge(self, plan):
        if (self.token is None or self.identity is None or plan.get("method") != "PUT"
                or not re.fullmatch(r"repos/joungGo/c03-execution-lab/pulls/[1-9][0-9]*/merge",
                                    plan.get("endpoint", ""))
                or plan.get("write_enabled") is not False
                or set(plan["body"]) != {"sha", "merge_method", "merge_action", "bypass_rules"}
                or not re.fullmatch(r"[a-f0-9]{40}", plan["body"]["sha"])
                or plan["body"]["merge_method"] != "squash"
                or plan["body"]["merge_action"] != "direct_merge"
                or plan["body"]["bypass_rules"] is not False):
            raise ValueError("검증된 정확한 PR head의 일반 병합 요청이 필요합니다")
        return self._api("PUT", "/"+plan["endpoint"], self.token,
                         {"sha": plan["body"]["sha"], "merge_method": "squash"})

    def close(self):
        if self.token is not None:
            self._api("DELETE", "/installation/token", self.token)
            self.token = None
            self.identity = None

    def __exit__(self, *exc):
        self.close()
