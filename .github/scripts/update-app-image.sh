#!/usr/bin/env bash
set -euo pipefail

: "${IMAGE_TAG:?검증한 이미지 태그가 필요합니다}"
[[ "$IMAGE_TAG" =~ ^sha-[0-9a-f]{7}$ ]] || { echo "유효하지 않은 이미지 태그입니다" >&2; exit 1; }
values_file=url-shortener/url-shortener-chart/values.prod.yaml

# 같은 이미지의 GitOps 후속 커밋만 수용하고 새 배포를 덮어쓰지 않는다.
git fetch origin main
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]]; then
  changed_files=$(git diff --name-only HEAD origin/main)
  test "$changed_files" = "$values_file"
  remote_tag=$(git show "origin/main:$values_file" | yq e -r '.api.image.tag' -)
  test "$remote_tag" = "$IMAGE_TAG"
  git merge --ff-only origin/main
fi

yq e ".api.image.tag = \"${IMAGE_TAG}\"" -i "$values_file"
