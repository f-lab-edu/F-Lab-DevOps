# 31번: Terraform 원격 state와 plan 파일 관리

## 현재 구성

- S3 버킷: `urlshortener-tfstate-716174522908-ap-northeast-2`
- 리전: `ap-northeast-2`
- 운영 state: `urlshortener/production/terraform.tfstate`
- 실습 state: `urlshortener/practice/terraform.tfstate`
- 잠금: 같은 key에 대한 S3 `.tflock` 파일 (`use_lockfile = true`)
- 버킷 보호: 퍼블릭 접근 차단, ACL 비활성화, 기본 SSE-S3 암호화, 버전 관리, 비 TLS 요청 거부

버킷은 [bootstrap-state-bucket.sh](../../terraform/bootstrap-state-bucket.sh)로 애플리케이션 Terraform root와 별도로 준비한다. 버킷을 관리 대상 root 안에 넣으면 최초 `terraform init` 시 버킷이 아직 없는 순환 의존성이 생긴다. 스크립트는 예상 AWS 계정을 확인한 뒤 버킷 설정을 적용한다.

2026-09-23 이전 시점에 사용자는 모든 관리 리소스가 제거됐다고 확인했다. 이전 직전 로컬 `terraform.tfstate`는 리소스 0개였으며, 오래된 `terraform.tfstate.backup`에는 리소스 113개가 있었다. **현재 빈 state만 운영 S3 key로 이전했다.** 오래된 백업은 최신 상태로 간주하거나 원격 state에 덮어쓰지 않는다.

## 작업 환경 분리

운영과 실습은 다른 Git 작업 디렉터리, 다른 S3 key, 다른 `project_name`을 사용한다. 기존 운영 디렉터리에서 실습용 `backend.practice.hcl`로 `terraform init -migrate-state`를 실행하지 않는다. 그 명령은 운영 state를 실습 key로 복사할 수 있다.

새 작업 디렉터리에서 초기화할 때:

```sh
cd terraform
terraform init -backend-config=backend.production.hcl
# 실습용 별도 작업 디렉터리에서는 대신:
# terraform init -backend-config=backend.practice.hcl
```

운영은 `environment=production`과 운영용 `project_name`을 사용한다. 실습은 `environment=practice`와 서로 다른 `project_name`을 지정한다. backend 설정 파일에는 자격 증명을 넣지 않는다. AWS 프로파일이나 역할을 통해 인증한다. `-backend-config`에 비밀값을 직접 넣으면 로컬 `.terraform` 디렉터리와 plan에 남을 수 있다.

## 접근 권한

버킷은 퍼블릭으로 열지 않는다. Terraform 실행자에게 필요한 경로만 IAM으로 허용한다.

| 대상 | 필요한 권한 |
| --- | --- |
| 버킷 | 해당 state 경로에 대한 `s3:ListBucket` |
| `.../terraform.tfstate` | `s3:GetObject`, `s3:PutObject` |
| `.../terraform.tfstate.tflock` | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` |

운영과 실습 실행 역할은 각각의 key와 lockfile만 다루도록 제한한다. state 파일 자체에는 `s3:DeleteObject`를 줄 필요가 없다. 현재 AWS 사용자 권한과 향후 CI 역할을 분리할 때 이 범위로 정책을 부여한다. S3 버킷 버전 관리는 삭제·덮어쓰기 사고 복구에 사용한다.

## 이후 일괄 점검 항목

요청에 따라 다음 점검은 아직 수행하지 않았다.

1. S3에서 운영 state 객체와 버전 관리가 실제로 보이는지 확인한다. state 원문은 로그나 이슈에 게시하지 않는다.
2. `terraform state list`로 운영 state가 의도한 빈 상태인지 확인하고, 현재 AWS 계정에 남아 있는 관리 대상 리소스가 없는지 대조한다.
3. `terraform plan`을 검토한다. 인프라를 다시 구축할 계획이 아니라면 대량 생성·삭제가 나타나는 상태에서 `apply`하지 않는다.
4. 두 실행 환경에서 동시에 state를 잠그려 할 때 두 번째 실행이 기다리거나 거부되는지 확인한다. 잠금 파일을 임의로 지우지 않는다.
5. 다른 작업 디렉터리에서 같은 운영 backend를 초기화해 동일한 state가 보이는지 확인한다.
6. 위 확인을 마친 후 로컬 `terraform.tfstate`와 `terraform.tfstate.backup`의 보관·보안 삭제 여부를 결정한다. 오래된 백업은 민감한 인프라 정보를 포함할 수 있다.

## Git의 plan 파일

`terraform/plan.out`은 Git 추적에서 제거했으며 `.gitignore`의 `plan.out` 규칙을 유지한다. 로컬 파일은 남아 있다. `.terraform.lock.hcl`은 계속 추적한다.

Git 추적 제거는 과거 커밋의 파일을 지우지 않는다. 과거 `plan.out`에 민감정보가 있었는지 별도 조사하고, 확인되면 해당 자격 증명 교체와 이력 처리 범위를 정한다. 앞으로 생성하는 plan은 임시 민감 산출물로 취급하고 CI artifact의 접근 범위와 보존 기간을 제한한다.
