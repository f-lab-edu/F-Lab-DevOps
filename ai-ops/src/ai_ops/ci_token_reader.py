"""클러스터 전달기에서만 전용 토큰 파일로 고정 GitHub API를 읽는다."""

from pathlib import Path
import re
import stat
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ai_ops.ci_history import LAB_RUNS_PATH, MAX_API_BYTES, PREFIX, READ_TIMEOUT_SECONDS


class NoRedirect(HTTPRedirectHandler):
    """인증 헤더가 다른 호스트로 자동 전달되지 않도록 리다이렉트를 직접 처리한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TokenGitHubReader:
    """토큰은 API 요청에만 사용하고 서명된 artifact URL에는 보내지 않는다."""

    def __init__(self, token_file):
        self.path = Path(token_file)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _token(self):
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 4096:
            raise ValueError("CI credential file rejected")
        token = self.path.read_text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{20,4096}", token):
            raise ValueError("CI credential rejected")
        return token

    def get(self, path, *, max_bytes=MAX_API_BYTES):
        if path != LAB_RUNS_PATH and not re.fullmatch(re.escape(PREFIX) + r"/(?:runs/[1-9][0-9]*(?:/artifacts\?per_page=100)?|artifacts/[1-9][0-9]*/zip)", path):
            raise ValueError("CI API path rejected")
        url = "https://api.github.com/" + path
        headers = {"Authorization": "Bearer " + self._token(), "User-Agent": "ai-ci-relay",
                   "X-GitHub-Api-Version": "2022-11-28"}
        for attempt in range(4):
            try:
                with self.opener.open(Request(url, headers=headers), timeout=READ_TIMEOUT_SECONDS) as response:
                    data = response.read(max_bytes + 1)
                    if len(data) > max_bytes:
                        raise ValueError("CI API response too large")
                    return data
            except HTTPError as error:
                try:
                    location = error.headers.get("Location", "")
                    if error.code not in (301, 302, 303, 307, 308) or not path.endswith("/zip"):
                        raise ValueError("CI API read failed") from None
                finally:
                    error.close()
                target = urlsplit(location)
                host = target.hostname or ""
                if (target.scheme != "https" or target.username or target.password
                        or target.port not in (None, 443) or target.fragment
                        or not (host.endswith(".blob.core.windows.net")
                                or host == "results-receiver.actions.githubusercontent.com")):
                    raise ValueError("CI artifact redirect rejected")
                url, headers = location, {"User-Agent": "ai-ci-relay"}
        raise ValueError("CI artifact redirect limit")
