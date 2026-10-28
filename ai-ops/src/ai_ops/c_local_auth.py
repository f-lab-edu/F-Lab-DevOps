"""C단계 단일 사용자 로컬 소켓의 OS·GitHub 사용자 확인 경계."""

from __future__ import annotations

import ctypes
import os
import re
import socket
import struct
import subprocess
import sys


def peer_uid(connection: socket.socket) -> int:
    """클라이언트가 보낸 필드가 아닌 커널의 소켓 상대 UID를 읽는다."""
    if connection.family != socket.AF_UNIX:
        raise ValueError("로컬 Unix 소켓만 허용합니다")
    if sys.platform == "darwin":
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        function = ctypes.CDLL(None).getpeereid
        function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        function.restype = ctypes.c_int
        if function(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise ValueError("소켓 사용자 확인에 실패했습니다")
        return uid.value
    if sys.platform.startswith("linux"):
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", raw)[1]
    raise ValueError("지원되지 않는 로컬 인증 플랫폼입니다")


def authenticated_user(connection: socket.socket, allowed_github_id: int,
                       *, runner=subprocess.run) -> int:
    """같은 OS 사용자와 GitHub 인증 주체가 모두 맞을 때만 불변 사용자 ID를 반환한다."""
    if (type(allowed_github_id) is not int or allowed_github_id <= 0
            or peer_uid(connection) != os.geteuid()):
        raise ValueError("C 로컬 사용자 인증을 거부했습니다")
    try:
        result = runner(["gh", "api", "user", "--jq", ".id"], capture_output=True,
                        text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("GitHub 사용자 확인에 실패했습니다") from exc
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"[1-9][0-9]{0,18}", value):
        raise ValueError("GitHub 사용자 확인에 실패했습니다")
    user_id = int(value)
    if user_id != allowed_github_id:
        raise ValueError("C 사건 접근 권한이 없습니다")
    return user_id
