"""C-02 단일 사용자 로컬 사건 조회·해결 요청 인터페이스. 변경 실행 경로는 없다."""

from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import subprocess

from ai_ops.c_local_auth import authenticated_user
from ai_ops.c_projection_client import ProjectionClient
from ai_ops.c_recovery import RecoveryDraftStore, create_request, read_incident_projection
from ai_ops.contracts import canonical_json_bytes


MAX_REQUEST = 1024
MAX_RESPONSE = 4096


def _socket_path(root: Path) -> Path:
    return root / "operator.sock"


class OperatorHandler(socketserver.StreamRequestHandler):
    """커널 peer와 GitHub ID를 확인한 뒤 고정된 읽기/요청 명령만 처리한다."""

    def handle(self):
        self.request.settimeout(8)
        try:
            user_id = authenticated_user(self.request, self.server.allowed_github_id,
                                         runner=self.server.github_runner)
            raw = self.rfile.readline(MAX_REQUEST + 1)
            if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST:
                raise ValueError("C 요청 크기가 유효하지 않습니다")
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"action", "incident_id", "run_id"}:
                raise ValueError("C 요청 형식이 유효하지 않습니다")
            if value["action"] not in {"show", "request"}:
                raise ValueError("C 명령을 허용하지 않습니다")
            projection = (self.server.projection_client.fetch(value["incident_id"], value["run_id"])
                          if self.server.projection_client is not None
                          else read_incident_projection(self.server.incident_root,
                                                        value["incident_id"], value["run_id"]))
            if value["action"] == "show":
                reply = {"status": "ok", "projection": projection.model_dump(mode="json")}
            else:
                request = create_request(projection, user_id)
                self.server.drafts.save_request(request)
                reply = {"status": "ok", "request": request.model_dump(mode="json")}
            payload = canonical_json_bytes(reply)
            if len(payload) > MAX_RESPONSE:
                raise ValueError("C 응답 크기가 유효하지 않습니다")
            self.wfile.write(payload + b"\n")
        except (ValueError, OSError, TypeError, KeyError, http.client.HTTPException):
            # 저장소·GitHub 오류나 내부 경로·본문은 호출자에게 노출하지 않는다.
            self.wfile.write(b'{"status":"rejected"}\n')


class OperatorServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, state_root: Path, incident_root: Path | None, allowed_github_id: int,
                 *, github_runner=None, projection_client: ProjectionClient | None = None):
        self.drafts = RecoveryDraftStore(state_root)
        self.incident_root = incident_root
        self.projection_client = projection_client
        if (incident_root is None) == (projection_client is None):
            raise ValueError("사건 조회 입력은 로컬 또는 인증된 원격 중 하나여야 합니다")
        self.allowed_github_id = allowed_github_id
        self.github_runner = github_runner or subprocess.run
        path = _socket_path(self.drafts.root)
        if path.exists() or path.is_symlink():
            raise ValueError("기존 C 소켓이 있습니다. 서버 상태를 확인하세요")
        super().__init__(str(path), OperatorHandler)
        os.chmod(path, 0o600)

    def server_close(self):
        path = Path(self.server_address)
        try:
            super().server_close()
        finally:
            if path.exists() and stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()


def _client(path: Path, action: str, incident_id: str, run_id: str) -> dict:
    if action not in {"show", "request"}:
        raise ValueError("C 읽기/요청 명령만 지원합니다")
    data = canonical_json_bytes({"action": action, "incident_id": incident_id, "run_id": run_id}) + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(path))
        connection.sendall(data)
        with connection.makefile("rb") as reader:
            raw = reader.readline(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE or not raw.endswith(b"\n"):
        raise ValueError("C 응답이 유효하지 않습니다")
    return json.loads(raw)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="C-02 로컬 사건 조회·해결 요청")
    parser.add_argument("action", choices=("serve", "show", "request"))
    parser.add_argument("--state-root", type=Path, default=Path.home()/".local/share/url-shortener-ai-ops/c-approvals")
    parser.add_argument("--incident-store", type=Path)
    parser.add_argument("--projection-token-file", type=Path)
    parser.add_argument("--projection-ca-file", type=Path)
    parser.add_argument("--projection-port", type=int, default=9444)
    parser.add_argument("--allowed-github-id", type=int)
    parser.add_argument("--incident-id")
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    if args.action == "serve":
        remote = args.projection_token_file is not None or args.projection_ca_file is not None
        if (args.allowed_github_id is None or
                (remote and (args.projection_token_file is None or args.projection_ca_file is None
                             or args.incident_store is not None)) or
                (not remote and args.incident_store is None)):
            parser.error("서버에는 허용 GitHub ID와 로컬 사건 저장소 또는 원격 조회 자격 한 쌍이 필요합니다")
        projection_client = (ProjectionClient(args.projection_token_file, args.projection_ca_file,
                                              port=args.projection_port) if remote else None)
        with OperatorServer(args.state_root, args.incident_store, args.allowed_github_id,
                            projection_client=projection_client) as server:
            server.serve_forever(poll_interval=0.5)
        return 0
    if not args.incident_id or not args.run_id:
        parser.error("사건 ID와 실행 ID를 함께 지정해야 합니다")
    result = _client(_socket_path(args.state_root), args.action, args.incident_id, args.run_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
