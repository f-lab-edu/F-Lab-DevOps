"""검증한 이미지의 소스·의존성·등록 identity만 고정 형식으로 기록한다."""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


def write_record(output: Path, env: dict[str, str], root: Path) -> None:
    revision = env["GITHUB_SHA"]
    image_id = env["IMAGE_ID"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("invalid image identity")
    published = env.get("IMAGE_PUBLISHED", "false")
    if published not in {"true", "false"}:
        raise ValueError("invalid publication state")
    tag = env["IMAGE_TAG"]
    if not re.fullmatch(r"sha-" + revision + r"-r[1-9][0-9]*-a[1-9][0-9]*", tag):
        raise ValueError("invalid immutable tag")
    repository = digest = None
    if published == "true":
        repository, digest = env["IMAGE_REPOSITORY"], env["IMAGE_DIGEST"]
        if not re.fullmatch(r"[0-9]{12}\.dkr\.ecr\.[a-z]{2}-[a-z]+-[0-9]\.amazonaws\.com/[a-z0-9][a-z0-9._/-]*-ai-ops", repository):
            raise ValueError("invalid agent repository")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("invalid registry digest")
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        "ai-ops/Dockerfile", "ai-ops/requirements.lock", "ai-ops/runbooks/index.yaml")}
    record = {"schema_version": 1, "record_type": "ai_ops_image_registration",
              "source_revision": revision, "image_id": image_id, "tag": tag, "published": published == "true",
              "repository": repository, "registry_digest": digest, "source_hashes": hashes,
              "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        os.chmod(output, 0o600)
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    try:
        write_record(Path(os.environ["IMAGE_RECORD_PATH"]), dict(os.environ), Path.cwd())
    except Exception:
        raise SystemExit("이미지 등록 기록을 생성하지 못했습니다. identity와 설정을 확인하세요.") from None
