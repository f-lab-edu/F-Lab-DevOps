# AI 이미지 등록 전용 Terraform

B-03의 AI ECR·등록 IAM만 관리하는 독립 root다. `terraform/`의 기존 production 인프라와 **코드·backend key·초기화 디렉터리**를 분리한다. 같은 S3 bucket을 사용하지만 state 파일과 잠금 파일은 다르다. 별도 AWS 계정이나 별도 EKS 클러스터를 만드는 작업은 아니다.

| 항목 | 기존 인프라 | AI 이미지 등록 |
|---|---|---|
| 실행 폴더 | `terraform/` | `terraform/ai-ops-registry/` |
| S3 state key | `urlshortener/production/terraform.tfstate` | `urlshortener/ai-ops/practice/terraform.tfstate` |
| 관리 범위 | 기존 VPC·EKS·RDS·앱 ECR·OIDC 등 | AI ECR·lifecycle policy·IAM Role·inline policy 4개 |
| 기존 GitHub OIDC | 생성·관리 | `data`로 조회만 함 |
| 기본 AI 이름 | 해당 없음 | `urlshortener-practice-ai-ops` / `urlshortener-practice-ai-ops-image-role` |

계정 기본값은 `716174522908`, 리전은 `ap-northeast-2`, 태그 환경은 `practice`다. 이 태그는 기존 EKS나 B-02 조사 대상의 환경을 바꾸지 않는다. `project_name`은 `-practice`로 끝나는 접두사만 받는다. provider의 `allowed_account_ids`와 S3 backend의 계정 허용 목록을 각각 검사한다. 계정을 바꾸려면 변수뿐 아니라 backend bucket·허용 계정과 기존 OIDC 존재 여부도 함께 검토해야 한다.

## 파일과 처리 흐름

1. `backend.practice.hcl`이 AI state 위치를 정한다. 기존 `terraform/.terraform`의 backend를 바꾸지 않는다.
2. `providers.tf`가 잠금 파일의 AWS provider 6.40.0과 실제 인증 계정을 확인한다. Terraform CLI는 1.14.7이다.
3. `main.tf`의 data source가 기존 GitHub OIDC를 조회하고 `sts.amazonaws.com` audience를 확인한다. OIDC를 새로 만들거나 import하지 않는다. 기존 provider가 삭제되면 이 역할의 인증도 영향을 받는다.
4. `enable_ai_ops_registry=true`이면 ECR·lifecycle·등록 역할·정책만 계획한다. 기본값은 false이며 예제 입력에서 명시적으로 활성화한다. 생성 후 false로 바꾸면 삭제 계획이 생기므로 일시 중지 스위치로 사용하지 않는다.
5. `outputs.tf`가 저장소 URL과 등록 역할 ARN을 반환한다. 이미지 빌드·등록과 EKS 배포는 별도 단계다.

기존 root의 미적용 AI 정의·변수·output은 제거했다. 분리 당시 AWS 조회에서 AI 리소스와 AI state가 없음을 확인했으므로 **분리에 state 이동·import·기존 리소스 삭제는 필요 없었다.** 기존 production state 소유권을 이 폴더로 옮기지 않는다.

## 검토와 이후 실행 방법

2026-10-17 후속 작업에서 이 전용 S3 backend에 ECR/IAM 4개를 생성했다. 적용 후 plan은 변경 없음이며 GitHub 등록 환경·변수도 설정했다. 실제 OIDC 인증·이미지 등록은 아직 실행하지 않았다. [적용·재조회 기록](../../ai-ops/docs/B3-registry-apply.md)에 현재 상태와 남은 순서를 정리했다.

AWS 없이 구성만 검사하는 명령이다. CI도 이 경로까지만 실행한다.

```sh
terraform -chdir=terraform/ai-ops-registry fmt -check
terraform -chdir=terraform/ai-ops-registry init -backend=false -lockfile=readonly -input=false
terraform -chdir=terraform/ai-ops-registry validate
```

실제 적용을 준비할 때 사용할 절차는 아래와 같다. 저장소 루트에서 실행하며, 기존 `terraform/` 폴더에는 `init -reconfigure`나 `init -migrate-state`를 실행하지 않는다.

```sh
cp terraform/ai-ops-registry/terraform.tfvars.example terraform/ai-ops-registry/terraform.tfvars
terraform -chdir=terraform/ai-ops-registry init -backend-config=backend.practice.hcl -lockfile=readonly -input=false
terraform -chdir=terraform/ai-ops-registry plan -input=false -out=plan.out
```

최초 적용 전 예상 결과는 create 4/update 0/delete 0이었으며, 현재 같은 입력의 plan은 변경 없음이어야 한다. managed resource에 OIDC·EKS·RDS·VPC가 없어야 한다. 출력 이름·권한·backend key도 확인한다. 위 절차의 원격 plan은 정상적인 state 잠금을 사용하며, 이번 점검에서 사용한 `-lock=false` 임시 local 사본 방식과 구분한다. `plan.out`과 `terraform.tfvars`는 Git에서 제외된다. 이 실행 안내와 별도로 후속 적용 기록을 보관하며, 추가 적용 전에는 최신 AWS 상태로 다시 계획해야 한다.

GitHub `ai-ops-image` environment의 main 제한·검토자와 네 가지 등록 변수는 설정했다. main의 수동 workflow 반영과 실제 OIDC 인증·이미지 등록·digest 대조가 다음 작업이다. 자세한 미완료 항목과 점검 결과는 [AWS 사전 점검 기록](../../ai-ops/docs/B3-aws-preflight.md), [이미지 등록 안내](../../ai-ops/docs/B3-image-and-observations.md)를 참고한다.

## S10 선택 실습 CI

`enable_b6_lab_ci=true`는 기존 4개에 실습 역할·inline policy·EKS group entry 3개를 추가한다. production cluster 생성 뒤 적용하고 AI root를 먼저 정리한다. 같은 state의 후속 apply에서 활성 값을 빠뜨려 삭제 계획이 나오면 중단한다. [인증·복구·재설치 절차](../../ai-ops/docs/B6-ci-experiment.md)를 따른다.
