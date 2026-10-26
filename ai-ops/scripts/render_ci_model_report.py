"""비공개 CI v2 모델 결과에서 검증된 KST HTML 보기 파일을 만든다."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import tempfile

from ai_ops.path_policy import cli_paths, permitted_path
from ai_ops.projected_ci_report_display import render_ci_model_html


def main() -> None:
    parser = argparse.ArgumentParser(description="저장된 CI v2 모델 보고서의 KST 보기 생성")
    parser.add_argument("--result", required=True, type=Path,
                        help="개인 저장소의 live-result.json")
    args = cli_paths(parser, parser.parse_args(), {"result": "input"})
    source = args.result
    if (source.parent.stat().st_uid != os.geteuid()
            or source.parent.stat().st_mode & 0o077):
        parser.error("보고서 폴더는 현재 사용자 전용이어야 합니다")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_size > 256 * 1024):
            parser.error("보고서 파일의 유형·권한·크기를 확인하세요")
        raw = stream.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        parser.error("보고서 파일이 너무 큽니다")
    result = json.loads(raw)
    page = render_ci_model_html(result["report"], result["view"])
    destination = permitted_path(source.with_name(source.stem + "-display.html"), "store")
    os.umask(0o077)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                         prefix=".model-report-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(page)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(destination)


if __name__ == "__main__":
    main()
