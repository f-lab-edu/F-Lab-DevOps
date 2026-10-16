"""운영자 CLI의 파일 접근을 등록된 자료·저장 폴더 안으로 제한한다."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
PERSONAL_ROOT = Path.home() / ".local/share/url-shortener-ai-ops"
Kind = Literal["input", "directory", "store", "output", "html", "checkpoint"]


def _roots(kind: Kind) -> tuple[Path, ...]:
    """추가 폴더는 CLI 인자가 아닌 운영자 환경 설정으로만 등록한다."""
    personal = [PERSONAL_ROOT, Path("/data/incidents")]
    extra = os.environ.get("AI_OPS_FILES_ROOT")
    if extra:
        configured = Path(extra).expanduser()
        if (not configured.is_absolute() or ".." in configured.parts or configured.is_symlink()
                or len(configured.parts) < 3 or configured.resolve() == Path.home().resolve()):
            raise ValueError("AI_OPS_FILES_ROOT에는 전용 절대 폴더를 등록해야 합니다")
        if (not configured.is_dir() or configured.stat().st_uid != os.geteuid()
                or configured.stat().st_mode & 0o022):
            raise ValueError("추가 폴더는 현재 사용자 소유이며 다른 사용자에게 쓰기가 허용되지 않아야 합니다")
        canonical = str(configured.resolve())
        repository = str(PACKAGE_ROOT.parent.resolve())
        if os.path.commonpath((canonical, repository)) in (canonical, repository):
            raise ValueError("추가 폴더는 프로젝트 저장소와 분리된 전용 위치여야 합니다")
        personal.append(configured)
    if kind == "html":
        return (PACKAGE_ROOT / "site",)
    if kind == "store":
        return tuple(personal)
    if kind in ("output", "checkpoint"):
        return (*personal, PACKAGE_ROOT / "site")
    return (PACKAGE_ROOT / "config", PACKAGE_ROOT / "evals", PACKAGE_ROOT / "runbooks",
            PACKAGE_ROOT / "site", Path("/etc/ai-ops"), *personal)


def permitted_path(value: str | Path, kind: Kind) -> Path:
    """정규화한 경로를 반환한다. 밖으로 나가는 링크·부모 탐색은 거부한다."""
    path = Path(value).expanduser()
    if ".." in path.parts:
        raise ValueError("상위 폴더 탐색은 허용하지 않습니다")
    resolved = path.resolve(strict=False)
    roots = _roots(kind)
    if any(root.is_symlink() for root in roots):
        raise ValueError("허용 폴더 자체를 심볼릭 링크로 바꿀 수 없습니다")
    if not any(os.path.commonpath((str(root.resolve()), str(resolved))) == str(root.resolve())
               for root in roots):
        raise ValueError("지정된 자료·저장 폴더 밖의 경로는 허용하지 않습니다")
    if kind in ("store", "output", "html", "checkpoint"):
        # 출력은 내부 링크도 따라가지 않는다. 플랫폼의 상위 /tmp 링크 등은 경계 밖이다.
        for item in (path, *path.parents):
            if any(item.absolute() == root.absolute() for root in _roots(kind)):
                if item.is_symlink():
                    raise ValueError("저장 폴더는 심볼릭 링크일 수 없습니다")
                break
            if item.is_symlink():
                raise ValueError("출력 경로의 심볼릭 링크는 허용하지 않습니다")
        if kind != "store" and resolved.suffix != (".html" if kind == "html" else ".json"):
            raise ValueError("출력 파일 확장자가 허용된 형식과 다릅니다")
        if kind == "checkpoint" and not re.fullmatch(r"a7-[a-z0-9][a-z0-9-]*-results\.json", resolved.name):
            raise ValueError("재개 가능한 결과는 a7-이름-results.json 형식이어야 합니다")
        if kind in ("output", "html") and path.exists():
            raise ValueError("기존 결과는 덮어쓸 수 없습니다. 새 파일 이름을 지정하세요")
    elif kind == "directory":
        if not resolved.is_dir():
            raise ValueError("자료 폴더가 존재하지 않습니다")
    elif not resolved.is_file() or resolved.suffix not in {".json", ".csv", ".html"}:
        raise ValueError("허용된 자료 파일이 아닙니다")
    return resolved


def cli_paths(parser: argparse.ArgumentParser, args: argparse.Namespace,
              policy: dict[str, Kind]) -> argparse.Namespace:
    """기본값까지 검사하고 검증한 새 인자 객체만 실행부에 전달한다."""
    values = vars(args).copy()
    try:
        for name, value in values.items():
            if isinstance(value, Path):
                if name not in policy:
                    raise ValueError("파일 경로 정책이 등록되지 않은 인자입니다")
                values[name] = permitted_path(value, policy[name])
    except (ValueError, OSError, RuntimeError):
        parser.error("파일 경로 정책 위반: 등록된 자료·저장 폴더와 파일 형식을 확인하세요")
    return argparse.Namespace(**values)


def write_private_json(path: Path, value: dict) -> None:
    """검증된 개인 경로에만 새 결과를 저장하며 기존 파일·링크를 덮어쓰지 않는다."""
    destination = permitted_path(path, "output")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = permitted_path(destination, "output")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".score-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # 완성한 파일만 원자적으로 게시한다. 대상이 이미 생겼다면 교체하지 않는다.
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
