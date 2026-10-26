# AI 이미지 등록 Terraform root 설치·정리

이 절차는 `terraform/ai-ops-registry/`의 **별도** S3 state를 다룬다. 기존 `terraform/`의 production state를 이 폴더로 이전하거나 GitHub OIDC를 import하지 않는다. AWS 계정 `716174522908`, 리전 `ap-northeast-2`, AI state key `urlshortener/ai-ops/practice/terraform.tfstate`, ECR `urlshortener-practice-ai-ops`, IAM Role `urlshortener-practice-ai-ops-image-role`이 대상이다. `practice`는 AI 리소스 이름·태그이지 새 EKS 클러스터가 아니다. Route53은 설치 시 기존 가이드의 검증된 Alias UPSERT 외에는 건드리지 않으며 **정리 시 절대 변경하지 않는다.**

등록 기반만 다루는 문서다. 전체 AI 재설치·정리는 [AI 클러스터 수명주기](ai-ops-cluster-lifecycle.md)를 필수로 함께 수행한다. 설치는 OIDC → AI root → 이미지 승인/검증 → 앱/Prometheus 준비 → AI Helm 순서이며, 정리는 AI Helm/PVC/EBS → 나머지 Kubernetes → AI root → production root → backend 순서다. Slack·Watchdog URL, GitHub Actions 읽기 토큰, 개인 API 키 등 [로컬 자격 원본](local-credential-preservation.md)은 이 root와 backend의 삭제 대상이 아니다.

## 공통 선행 확인

```bash
PROJECT_ROOT=/Users/jounghyeon/dev/Url-Shortener-EKS-Platform
STATE_BUCKET=urlshortener-tfstate-716174522908-ap-northeast-2
AI_STATE_KEY=urlshortener/ai-ops/practice/terraform.tfstate
aws sts get-caller-identity --query Account --output text
git -C "$PROJECT_ROOT" fetch origin main
git -C "$PROJECT_ROOT" ls-tree -r --name-only origin/main terraform/ai-ops-registry
test -f "$PROJECT_ROOT/terraform/ai-ops-registry/main.tf"
aws s3api head-object --bucket "$STATE_BUCKET" --key "$AI_STATE_KEY" \
  --query '[ContentLength,VersionId,LastModified]' --output text
```

계정이 다르거나 AI root가 사용할 **검토·반영된 코드**가 없으면 중단한다. 로컬에 파일이 있다는 것만으로 `main`의 설치 자동화에 포함된 것은 아니다. `HeadObject`의 404는 *새 설치 전*에는 가능하지만, 이미 ECR/IAM이 있으면 state 누락으로 간주하고 **destroy/apply 전에 복구·import 여부를 판단**한다. 403·네트워크 오류를 리소스 부재로 해석하지 않는다. 기존 root와 AI root는 backend key가 달라 Terraform 잠금이 서로를 막지 않는다. 따라서 설치·정리 중 두 Terraform root, `ai-ops-image` 이미지 등록 workflow, 기존 앱 배포 workflow가 동시에 실행되지 않도록 운영자가 작업 창을 조정한다.

## 설치: 기존 OIDC 생성 **후**, Kubernetes 설치 전

1. 기존 `terraform/bootstrap-state-bucket.sh`로 backend를 준비하고 production root를 적용한다. `module.github_oidc_provider.aws_iam_openid_connect_provider.this[0]`가 production state에 있고 AWS OIDC provider에 `sts.amazonaws.com` audience가 있는지 확인한다.
2. AI root의 `terraform.tfvars` 또는 명령 인수에 `enable_ai_ops_registry=true`를 **명시**한다. 기본값 `false`는 생성하지 않으며, 설치 후 `false`로 변경하면 삭제 계획이 된다.
3. AI root만 아래 backend로 초기화한다. production root에 AI backend를 `-migrate-state`로 적용하지 않는다.

```bash
terraform -chdir="$PROJECT_ROOT/terraform" state list | \
  rg '^module\.github_oidc_provider\.aws_iam_openid_connect_provider\.this\[0\]$'
aws iam get-open-id-connect-provider \
  --open-id-connect-provider-arn arn:aws:iam::716174522908:oidc-provider/token.actions.githubusercontent.com \
  --query ClientIDList --output text
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" init \
  -backend-config=backend.practice.hcl -reconfigure -lockfile=readonly -input=false
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" validate
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" plan \
  -var='enable_ai_ops_registry=true' -input=false
```

새 설치의 계획은 AI ECR·lifecycle policy·IAM Role·inline policy **4개 생성**, 다른 root의 OIDC·EKS·RDS·VPC 변경 **0개**여야 한다. 기존 설치라면 변경 0개여야 한다. state/AWS와 계획이 다르거나 삭제·교체가 보이면 여기서 멈춘다. 검토 후 **별도 명령**으로 적용한다.

```bash
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" apply \
  -var='enable_ai_ops_registry=true'
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" state list
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" output -raw ai_ops_ecr_repository_url
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" output -raw ai_ops_image_role_arn
aws ecr describe-repositories --region ap-northeast-2 \
  --repository-names urlshortener-practice-ai-ops \
  --query 'repositories[0].[repositoryName,imageTagMutability]' --output text
aws ecr get-lifecycle-policy --region ap-northeast-2 \
  --repository-name urlshortener-practice-ai-ops --query lifecyclePolicyText --output text
aws iam list-role-policies --role-name urlshortener-practice-ai-ops-image-role \
  --query PolicyNames --output text
aws s3api head-object --bucket "$STATE_BUCKET" --key "$AI_STATE_KEY" \
  --query '[ContentLength,VersionId]' --output text
gh variable list --repo f-lab-edu/F-Lab-DevOps --env ai-ops-image
```

`state list`의 `data.aws_iam_openid_connect_provider.github`는 조회용이며, managed resource는 네 개여야 한다. GitHub environment의 `AI_OPS_AWS_ACCOUNT_ID`·`AI_OPS_AWS_REGION`·`AI_OPS_ECR_REPOSITORY`·`AI_OPS_IMAGE_ROLE_ARN`을 Terraform output과 비교한다. ECR 재생성은 과거 이미지를 복구하지 않는다. 이미지 빌드·등록은 `main`에 workflow가 포함된 후 사용자 주도 별도 단계이며, 성공한 digest를 확인하기 전에는 AI 이미지 배포 완료로 기록하지 않는다.

## 정리: 기존 production Terraform destroy **전**

Kubernetes·ALB/NLB 종속 리소스 정리와 배포 동결 후, production root가 소유하는 GitHub OIDC와 state bucket이 살아 있는 동안 AI root를 먼저 정리한다. AI 이미지 등록 workflow가 실행 중·대기 중이면 끝날 때까지 기다린다. ECR 이미지 삭제는 복구가 어려우므로 보존 대상과 **전체 삭제 승인**을 먼저 확인한다. `force_delete=false`를 바꾸거나 AWS에서 저장소를 강제 삭제하지 않는다.

```bash
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" init \
  -backend-config=backend.practice.hcl -reconfigure -lockfile=readonly -input=false
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" state list
aws s3api head-object --bucket "$STATE_BUCKET" --key "$AI_STATE_KEY" \
  --query '[ContentLength,VersionId,LastModified]' --output text
aws ecr list-images --repository-name urlshortener-practice-ai-ops \
  --region ap-northeast-2 --query 'imageIds[*].[imageTag,imageDigest]' --output table
aws iam get-role --role-name urlshortener-practice-ai-ops-image-role \
  --query 'Role.Arn' --output text
```

state에는 AI managed resource 네 개만 있어야 하고 실제 ECR·IAM과 일치해야 한다. state가 없는데 AWS 리소스가 살아 있으면 **삭제하지 말고 state 복구/import를 판단**한다. 전체 이미지 삭제가 승인됐고 목록이 비어 있지 않다면, 확인한 각 **digest**마다 다음 명령을 실행하고 `failures`가 `[]`인지 확인한다. 태그가 없는 이미지도 포함한다.

```bash
aws ecr batch-delete-image --repository-name urlshortener-practice-ai-ops \
  --region ap-northeast-2 --image-ids imageDigest=sha256:REPLACE_WITH_VERIFIED_DIGEST
aws ecr list-images --repository-name urlshortener-practice-ai-ops \
  --region ap-northeast-2 --query imageIds --output json
```

이미지가 `[]`이고 AI workflow가 동결됐을 때만 다음 계획을 검토한다. 예상 대상은 AI managed resource 네 개뿐이다. **AI destroy 완료를 확인하기 전에는 production destroy/OIDC 삭제로 넘어가지 않는다.**

```bash
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" plan -destroy \
  -var='enable_ai_ops_registry=true' -input=false
# 위 계획과 삭제 승인을 확인한 뒤 별도 실행
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" destroy \
  -var='enable_ai_ops_registry=true'
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" state list
aws ecr describe-repositories --region ap-northeast-2 \
  --repository-names urlshortener-practice-ai-ops
aws iam get-role --role-name urlshortener-practice-ai-ops-image-role
```

state list가 비어야 한다. ECR 조회의 `RepositoryNotFoundException`과 IAM 조회의 `NoSuchEntity`만 부재의 정상 결과로 인정한다. 다른 오류는 조사한다. 그 후 기존 정리 가이드의 production root 절차를 진행한다.

## 모든 root 삭제 후 backend 정리

**AI와 production의 destroy 및 AWS 실물 부재 확인이 끝날 때까지 state 객체·이전 버전·삭제 마커·bucket을 삭제하지 않는다.** state를 먼저 지우면 살아 있는 리소스의 소유 정보가 사라진다. 이전 가이드의 bucket 보존 정책을 적용할 경우 이 절은 건너뛴다. 이번처럼 backend까지 완전 정리하기로 결정한 경우에만, 버전이 있는 state의 복구 가능성 상실을 승인하고 수행한다. RDS 최종 스냅샷과 Route53은 별도 보존 대상으로 남는다.

```bash
terraform -chdir="$PROJECT_ROOT/terraform" state list
terraform -chdir="$PROJECT_ROOT/terraform/ai-ops-registry" state list
aws s3api list-object-versions --bucket "$STATE_BUCKET" --output json | \
  jq -r '((.Versions // []) + (.DeleteMarkers // []))[] | [.Key,.VersionId,.IsLatest] | @tsv'
```

두 state가 비었고 ECR·IAM·OIDC 등 AWS 실물도 제거됐는지 확인한다. 위 목록에 production·AI state와 그 `.tflock` 외의 키, 특히 다른 팀의 state가 있으면 **bucket 삭제를 중단**한다. 과거 실습 state가 있다면 소유자와 삭제 여부를 별도로 결정한다. 현재 객체에 대한 단순 `delete-object`만으로는 이전 버전과 삭제 마커가 남는다.

전체 버전 목록의 **모든 키가 허용된 네 키**이고 건수가 S3 `DeleteObjects` 한도인 1,000개 이하인지 다음 읽기 전용 검사를 먼저 실행한다. 검사가 실패하거나 목록이 예상과 다르면 삭제하지 않는다. 1,000개 초과 시에는 정확한 키·버전으로 분할하고 각 배치의 결과를 검증하는 별도 계획을 세운다.

```bash
S3_VERSIONS_JSON=$(aws s3api list-object-versions --bucket "$STATE_BUCKET" --output json)
printf '%s' "$S3_VERSIONS_JSON" | jq -e \
  --argjson allowed '["urlshortener/production/terraform.tfstate", "urlshortener/production/terraform.tfstate.tflock", "urlshortener/ai-ops/practice/terraform.tfstate", "urlshortener/ai-ops/practice/terraform.tfstate.tflock"]' \
  '((.Versions // []) + (.DeleteMarkers // [])) | all(.Key as $key | $allowed | index($key) != null)'
S3_OBJECT_COUNT=$(printf '%s' "$S3_VERSIONS_JSON" | jq '((.Versions // []) + (.DeleteMarkers // [])) | length')
printf '삭제 검토 대상 version/marker: %s\n' "$S3_OBJECT_COUNT"
test "$S3_OBJECT_COUNT" -gt 0 && test "$S3_OBJECT_COUNT" -le 1000
```

위 목록·건수와 전체 삭제 승인을 다시 확인한 **뒤에만** 아래를 별도로 실행한다. `Errors`가 `[]`인지 확인하고, 아니라면 중단해 정확한 실패 버전을 조사한다. 이 단계 이후 Terraform을 다시 실행하지 않는다.

```bash
S3_DELETE_JSON=$(printf '%s' "$S3_VERSIONS_JSON" | jq -c \
  '{Objects: (((.Versions // []) + (.DeleteMarkers // [])) | map({Key,VersionId})), Quiet: false}')
S3_DELETE_RESULT=$(aws s3api delete-objects --bucket "$STATE_BUCKET" --delete "$S3_DELETE_JSON" --output json)
printf '%s' "$S3_DELETE_RESULT" | jq '{deletedCount:(.Deleted // [] | length), errors:(.Errors // [])}'
test "$(printf '%s' "$S3_DELETE_RESULT" | jq '(.Errors // []) | length')" -eq 0
```

마지막에 `list-object-versions`에서 `Versions`와 `DeleteMarkers`가 모두 빈 배열인지, `list-objects-v2`도 비었는지 확인한다. 그때만 전용 bucket에 대해 `aws s3api delete-bucket --bucket "$STATE_BUCKET" --region ap-northeast-2`를 실행한다. 이 명령은 **모든 root 종료 이후 별도의 파괴적 단계**이며 자동으로 이어붙이지 않는다. 실패하면 bucket 상태를 재조회하고 임의로 `--force` 옵션을 사용하지 않는다. GitHub `ai-ops-image` environment 설정은 AWS 삭제와 별개로 남으므로, 재설치 시 output과 다시 대조한다.

## S10 선택 CI 연결 추가

S10 활성 시 같은 AI state에서 `enable_b6_lab_ci=true`로 전용 OIDC 역할·inline policy·EKS access entry 3개도 관리한다. 관리자 정책은 연결하지 않는다. 현재 계정/클러스터·main 제한/필수 승인의 `ai-ops-lab-ci` 환경과 실습 RBAC가 필요하다. 기존 이미지 등록 역할은 확장하지 않는다. 전체 정리는 여전히 AI root → production root 순서이며 같은 state의 후속 apply는 활성 플래그 누락으로 3개 삭제 계획이 생기지 않게 확인한다. 기본 새 AI 설치는 실습 미활성이고 선택 연결 시 값을 명시한다. GitHub 환경은 AWS 정리와 별개다. [상세](../../ai-ops/docs/B6-ci-experiment.md).
