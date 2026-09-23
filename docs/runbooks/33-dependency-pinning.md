# 33. 빌드·배포 의존성 고정과 갱신

## 고정 위치

| 대상 | 고정 위치 | 기준 |
|---|---|---|
| GitHub Actions | `.github/workflows/*.yml` | commit SHA와 사람이 읽는 버전 주석을 함께 수정 |
| yq·Argo CD CLI | `.github/workflows/ci-cd.yml` | 릴리스 버전과 Linux/amd64 SHA-256 |
| ruff·uv | `.github/requirements-ci.txt` | PyPI Linux/amd64 wheel 버전과 SHA-256 |
| Python 애플리케이션 | `url-shortener/requirements.txt`, `requirements.lock` | 직접 의존성 및 Python 3.11 Linux/amd64 전이 의존성·배포 파일 해시 |
| Python base 이미지 | `url-shortener/Dockerfile` | builder/runtime의 3.11.16 태그와 이미지 manifest digest |
| Terraform | `terraform/*.tf`, `.terraform.lock.hcl` | 모듈 exact 버전 및 provider lock; provider 제약은 `~> 6.0` 유지 |
| Karpenter AMI | `url-shortener/k8s/karpenter/ec2nodeclass.yaml` | EKS 1.35용 AL2023 `v20260917` |
| GitOps 외부 Helm chart | `cluster-addons/*/application.yaml` | chart 소스의 구체 `targetRevision` |

Terraform 모듈 버전은 이번 작업 전 로컬 `.terraform/modules/modules.json`에 설치된 값을 기준으로 고정했다. Provider는 `.terraform.lock.hcl`에 버전과 해시가 이미 있으며, 일반 실행에서는 `terraform init -lockfile=readonly`를 사용한다. `terraform init -upgrade`는 의존성 갱신 PR에서만 사용하고 변경된 lock 파일을 검토한다.

Karpenter AMI는 서울 리전의 공개 SSM 파라미터 `/aws/service/eks/optimized-ami/1.35/amazon-linux-2023/x86_64/standard/recommended`에서 확인한 `1.35.8-20260917`에 대응한다. EKS 버전을 바꿀 때 AMI alias와 Karpenter 호환성을 함께 검토한다.

## Python lock 재생성

`requirements.txt`는 직접 의존성을 선언하고, Docker는 해시 검사를 켠 `requirements.lock`을 설치한다. 갱신 PR에서 uv **0.12.18**로 아래 명령을 실행한다.

```bash
cd url-shortener
uv pip compile requirements.txt \
  --python-version 3.11 \
  --python-platform x86_64-manylinux_2_36 \
  --only-binary :all: \
  --generate-hashes \
  --output-file requirements.lock
```

PR 검증 작업은 같은 명령을 다시 실행하고 lock 파일의 Git diff가 없음을 확인한다. 직접 의존성 변경 PR에서 lock 파일도 함께 갱신해야 한다. Python 버전·아키텍처를 바꾸면 새 대상 플랫폼으로 다시 생성하고 Docker 설치를 확인한다.

## 갱신 PR과 확인 순서

Dependabot은 GitHub Actions, Docker, Terraform, Python 직접 의존성 및 CI 도구에 대해 매월 PR을 제안한다. `requirements.lock`, yq·Argo CD CLI checksum, AMI alias, GitOps Helm chart 버전은 담당자가 별도로 갱신한다. SHA만 변경되고 버전 주석이 남는 일이 없도록 두 값을 함께 수정한다. 로컬 실습 가이드 디렉터리는 Git에서 제외되어 있으므로 배포 기준은 추적되는 `cluster-addons` Application 파일이다.

1. 공식 릴리스·보안 공지·지원 버전을 확인하고, 태그와 commit SHA, 바이너리 checksum, 이미지 digest를 공급자 배포 정보와 대조한다. 새 digest는 같은 이미지 태그의 대상 아키텍처를 가리켜야 한다.
2. Python 직접 의존성 변경 시 lock을 다시 생성한다. Terraform module 또는 provider 변경 시 `terraform init -upgrade`로 module/provider 해석을 확인하고, provider lock 변경분과 예상 plan을 검토한다. chart 변경 시 values 키와 CRD 변경 사항을 확인한다.
3. `.github/workflows/dependency-compatibility.yml`의 PR 작업에서 Terraform 구성, Python lock, Ruff, Linux/amd64 Docker 빌드, 로컬 Helm chart lint가 통과하는지 확인한다. 배포 경로 변경은 기존 GitOps 검증과 별도의 비운영 배포 점검을 거친다.
4. AMI 변경은 노드 교체를 유발할 수 있다. EKS·Karpenter와의 호환성, 노드 준비, 워크로드 이동을 비운영에서 확인한다. 운영 반영 후에는 새 노드의 AMI와 Pod 상태를 관찰한다.

이 작업에서는 버전 고정과 PR 검증 구성을 추가했다. 사용자가 요청한 실제 빌드·배포 및 인프라 검증은 이후 일괄 점검에서 실행한다. 특히 외부 Helm chart의 새 버전은 설치 전 각 values 파일과 렌더 결과를 확인해야 한다.
