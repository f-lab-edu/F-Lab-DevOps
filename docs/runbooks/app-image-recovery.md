# 앱 이미지 수동 복구와 재사용

앱 배포 workflow `.github/workflows/ci-cd.yml`은 기존 main push 경로 조건을 유지한다. 앱 소스·Dockerfile·requirements.txt·requirements.lock 변경이 main에 반영되면 자동 배포한다. 문서·Terraform·Helm values 변경만으로 앱 배포를 시작하지 않는다. 수동 실행은 main에서만 허용하며 기본 모드는 `image-only`다.

## 실행 모드

| 모드 | 이미지 확보 | GitOps 커밋 | Argo·PostSync 검증 | release 태그·실패 롤백 |
| --- | --- | --- | --- | --- |
| 수동 image-only | 수행 | 실행 안 함 | 실행 안 함 | 실행 안 함 |
| 수동 deploy | 수행 | 수행 | 수행 | 기존 조건으로 수행 |
| 기존 앱 main push | 수행 | 수행 | 수행 | 기존 조건으로 수행 |

첫 설치에서는 앱 Application이 없으므로 image-only로 먼저 이미지를 확보한다. 등록 결과의 태그를 별도 GitOps 변경으로 검토·main 반영하고, ECR digest를 확인한 다음 앱을 설치한다. 운영 중인 앱도 image-only 실행만으로 Git의 이미지 태그가 바뀌지 않는다.

```bash
gh workflow run ci-cd.yml --repo f-lab-edu/F-Lab-DevOps --ref main -f mode=image-only
# 앱·Argo 인증·PostSync가 준비된 환경에서 배포까지 수행할 때만 선택한다.
gh workflow run ci-cd.yml --repo f-lab-edu/F-Lab-DevOps --ref main -f mode=deploy
```

GitHub Actions 화면에서는 `자동화 CI/CD 파이프라인 → Run workflow → main → mode`를 선택한다. ECR·OIDC 역할·권한은 기존 Terraform 구성을 사용한다. 워크플로 변경이 main에 반영되기 전에는 main의 수동 실행 기능이 생기지 않는다.

## 이미지 식별과 재사용

태그는 실행 대상 커밋의 앞 7자리를 사용해 `urlshortener:sha-a1b2c3d` 형태를 유지한다. 원본 전체 커밋은 `org.opencontainers.image.revision` 이미지 label에 기록한다.

1. 해당 태그의 ECR digest를 조회한다.
2. 명시적인 ImageNotFoundException이면 GitHub 러너에서 linux/amd64 이미지를 빌드·등록한다. Buildx 결과 digest와 ECR digest가 같아야 한다.
3. 태그가 있으면 새로 빌드·푸시하지 않는다. 고정 digest로 pull하고 전체 커밋 label과 linux/amd64를 검증한다.
4. 권한·네트워크·저장소 부재, 잘못된 digest, 출처 누락·불일치는 실패다. 기존 이미지를 덮어쓰거나 조회 오류를 이미지 부재로 취급하지 않는다.

같은 실행 대상 커밋의 재실행에서만 같은 태그를 재사용한다. GitOps 자동 커밋·문서 변경 등으로 main HEAD가 달라지면, 앱 파일이 같아도 다음 수동 실행은 새 커밋의 새 태그를 사용한다. 재실행은 새 베이스 이미지로 강제 재빌드하는 기능이 아니다. 실제 빌드 입력을 변경하려면 새 커밋을 만든다.

이전 방식으로 만든 이미지에는 전체 커밋 label이 없을 수 있다. 이 경우 자동 재사용을 거부한다. 출처를 확인한 별도 마이그레이션 또는 새 커밋으로 등록하며, IMMUTABLE 설정을 풀거나 기존 태그를 삭제해 우회하지 않는다.

## 결과 확인

성공한 실행 요약과 `app-image-<run_id>-<attempt>` artifact의 `image-registration.json`에 이미지 주소·태그·digest·전체 원본 커밋·reused·실행 모드·run/attempt를 기록한다. JSON은 외부 서비스에 보낼 자격 증명을 포함하지 않는다. artifact 보관 기간은 30일이다. Docker 실행 이미지 자체는 ECR에서 관리한다.

image-only 성공은 이미지 확보 성공이다. deploy 성공은 기존 Argo 커밋 일치·Synced/Healthy·동기화/PostSync Succeeded 검증까지 포함한다. release 태그는 배포 검증에 성공한 경우에만 등록한다. 배포 검증이 실패하면 이전 이미지가 ECR에 있을 때만 GitOps 롤백을 수행하고 실행은 실패로 표시한다.

재실행 중 main이 바뀌었을 때는 동일 이미지 태그의 values.prod.yaml 변경만 포함한 후속 커밋을 fast-forward로 받아들인다. 다른 파일이나 다른 태그로 진전됐다면 오래된 이미지로 최신 배포를 덮어쓰지 않고 중단한다. force push는 하지 않는다.

## 검증

`.github/tests/test_app_image.py`는 신규 등록·재사용·출처/아키텍처 검증·AWS 오류·digest 불일치·빌드 실패·운영 브랜치 경계를 외부 AWS/Docker 호출 없이 검증한다. `.github/workflows/app-ci-checks.yml`은 관련 변경의 push/PR에서 이 시험을 실행한다. 실제 ECR·Argo 환경의 결과는 별도 실제 실행으로 확인한다.
