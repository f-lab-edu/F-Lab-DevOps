"""앱 이미지의 신규 등록과 검증된 기존 이미지 재사용을 처리한다."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def run(args):
    """명령 실패를 숨기지 않고 표준 출력을 반환한다."""
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"명령 실패: {args[0]} {args[1]}\n{result.stderr}")
    return result.stdout


def execution_mode(event, ref, requested):
    """운영 main의 자동 배포와 명시적인 수동 실행만 허용한다."""
    if ref != "refs/heads/main":
        raise ValueError("앱 이미지 등록·배포는 main에서만 실행할 수 있습니다")
    if event == "push":
        return "deploy"
    if event == "workflow_dispatch" and requested in {"image-only", "deploy"}:
        return requested
    raise ValueError("허용되지 않은 실행 이벤트 또는 실행 모드입니다")


def lookup_digest(repository, tag):
    """명시적인 이미지 부재만 신규 빌드로 처리한다."""
    result = subprocess.run(
        ["aws", "ecr", "describe-images", "--repository-name", repository,
         "--image-ids", f"imageTag={tag}", "--output", "json"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        if "(ImageNotFoundException)" in result.stderr:
            return None
        raise RuntimeError(f"ECR 이미지 조회 실패: {result.stderr}")
    digest = json.loads(result.stdout)["imageDetails"][0]["imageDigest"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("유효하지 않은 이미지 digest입니다")
    return digest


def verify_image(image, source_sha):
    """태그 대신 고정 digest로 받아 전체 커밋과 실행 아키텍처를 검증한다."""
    run(["docker", "pull", "--platform", "linux/amd64", image])
    details = json.loads(run(["docker", "image", "inspect", image]))[0]
    labels = details.get("Config", {}).get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != source_sha:
        raise ValueError("이미지의 전체 커밋이 다르거나 출처 정보가 없어 재사용할 수 없습니다")
    if details.get("Os") != "linux" or details.get("Architecture") != "amd64":
        raise ValueError("이미지 실행 환경이 linux/amd64가 아닙니다")


def ensure_image(registry, repository, source_sha, context):
    """동일 태그를 덮어쓰지 않고 빌드 또는 재사용 결과를 통일한다."""
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("40자리 원본 커밋 SHA가 필요합니다")
    if not re.fullmatch(r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com", registry):
        raise ValueError("유효하지 않은 ECR registry입니다")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", repository):
        raise ValueError("유효하지 않은 ECR repository입니다")
    tag = f"sha-{source_sha[:7]}"
    full_image = f"{registry}/{repository}:{tag}"
    digest = lookup_digest(repository, tag)
    reused = digest is not None
    if not reused:
        with tempfile.TemporaryDirectory() as directory:
            metadata_file = Path(directory) / "build.json"
            run(["docker", "buildx", "build", "--platform", "linux/amd64",
                 "--label", f"org.opencontainers.image.revision={source_sha}",
                 "--tag", full_image, "--metadata-file", str(metadata_file),
                 "--push", str(context)])
            built_digest = json.loads(metadata_file.read_text())["containerimage.digest"]
        digest = lookup_digest(repository, tag)
        if digest is None or digest != built_digest:
            raise ValueError("빌드 결과와 ECR 등록 digest가 일치하지 않습니다")
    verify_image(f"{registry}/{repository}@{digest}", source_sha)
    return {"image_tag": tag, "full_image": full_image, "image_digest": digest,
            "source_sha": source_sha, "reused": reused}


def main():
    """GitHub 출력과 결과 파일에는 공개 이미지 식별 정보만 기록한다."""
    mode = execution_mode(os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"],
                          os.environ.get("REQUESTED_MODE", "image-only"))
    if sys.argv[1:] == ["mode"]:
        values = {"mode": mode, "deploy": str(mode == "deploy").lower()}
    elif sys.argv[1:] == ["image"]:
        values = ensure_image(os.environ["ECR_REGISTRY"], os.environ["ECR_REPOSITORY"],
                              os.environ["GITHUB_SHA"], Path("url-shortener"))
        values.update(mode=mode, run_id=os.environ["GITHUB_RUN_ID"],
                      run_attempt=os.environ["GITHUB_RUN_ATTEMPT"])
        record = Path(os.environ["RUNNER_TEMP"]) / "app-image/image-registration.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n")
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("## 앱 이미지 확보 결과\n\n")
            summary.write(f"- 실행 모드: `{mode}`\n- 처리: {'기존 이미지 재사용' if values['reused'] else '신규 빌드·등록'}\n")
            summary.write(f"- 이미지: `{values['full_image']}`\n- digest: `{values['image_digest']}`\n- 원본 커밋: `{values['source_sha']}`\n")
            if mode == "image-only":
                summary.write("- 이미지 확보만 완료했습니다. GitOps 수정·배포·롤백은 실행하지 않습니다.\n")
    else:
        raise ValueError("mode 또는 image 명령이 필요합니다")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key, value in values.items():
            output.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


if __name__ == "__main__":
    main()
