# B-03 AWS 적용 전 점검·Terraform 비교 계획

이 문서는 2026-10-16 적용 전 기록이다. **2026-10-17 후속 단계에서 AI 리소스 4개 생성·별도 state 저장과 GitHub 등록 환경/변수 설정을 마쳤다.** 최신 결과와 미검증 항목은 [실제 적용 기록](B3-registry-apply.md)을 따른다.

점검 시각: 2026-10-16 20:56:38 KST (내부 UTC: 2026-10-16T11:56:38.528767Z). 계정 `716174522908`, 리전 `ap-northeast-2`, 코드 기준 `05d22fd`다. **실제 리소스 생성·변경·이미지 등록·GitHub 설정 변경은 하지 않았다.**

## 확인한 실제 상태

- default AWS profile의 IAM 사용자 인증으로 읽기 조회가 가능했다. 키 값은 출력·기록하지 않았다.
- EKS `urlshortener`는 ACTIVE/1.35이며 public/private API endpoint가 모두 활성화돼 있다. 실제 Pod·노드·RBAC·통신 성공을 확인한 것은 아니다.
- RDS `urlshortener-postgres`와 `urlshortener-postgres-replica`는 available이다.
- 앱 ECR `urlshortener`는 존재하고 IMMUTABLE/scan-on-push가 활성화돼 있다. AI 전용 저장소·등록 역할은 없다.
- 기존 GitHub OIDC provider는 `token.actions.githubusercontent.com`과 `sts.amazonaws.com` audience를 사용한다. 기존 앱 등록 Role은 main ref subject만 신뢰한다.
- S3 state는 `urlshortener/production/terraform.tfstate`만 존재한다. 로컬 Terraform backend도 이 키를 사용한다. practice state는 없고 로컬 tfvars의 environment 미지정으로 production 기본값이 적용된다.
- GitHub environments는 0개다. repository variable 이름 목록에는 `ARGOCD_SERVER`만 있으며 `AI_OPS_AWS_ACCOUNT_ID`, `AI_OPS_AWS_REGION`, `AI_OPS_ECR_REPOSITORY`, `AI_OPS_IMAGE_ROLE_ARN`은 없다.
- main의 `.github/workflows/ai-ops-image.yml` 조회는 404다. 수동 이미지 workflow는 현재 작업 브랜치에만 있으며 main/default branch 반영·환경 보호 설정·등록 변수 준비가 남아 있다.

이전 대화의 미배포 상태와 현재 AWS 상태가 다르다. 기존 production을 비어 있는 실습 환경으로 취급하지 않는다. 이번 점검은 해당 계정·리전의 현재 시점 결과이며 다른 계정/리전의 리소스 존재 여부까지 확인한 것은 아니다.

## 계획을 만든 방식

원본 Terraform/backend/tfvars를 변경하지 않고 사용자 전용 임시 디렉터리(0700)를 만들었다. production state는 읽기 사본만 내려받아 0600으로 보관하고 비밀값은 출력하지 않았다. S3 backend 선언을 제외한 사본에서 local backend 기본값을 사용했다. 실제 설정의 비밀값 없는 항목과 state의 RDS rotation version 1을 대조했다.

`terraform plan -input=false -lock=false -detailed-exitcode -out=...`로 AWS 읽기 refresh와 변경 계산만 수행했다. 원격 state 잠금·쓰기·apply는 하지 않았다. 전체 root의 필수 ephemeral DB 비밀번호 입력에는 실제 비밀번호 대신 계획 전용 가상 값을 사용했다. 이 계획은 실행용 승인 산출물이 아니며 apply할 때 재사용하지 않는다. 변경 선택·환경·코드 확정 후 새로 plan해야 한다.

## 비교 결과

| 안 | 상태·코드 범위 | Terraform 결과 | AI 저장소/역할 이름 |
|---|---|---|---|
| 기존 production에 추가(분리 전 비교안) | 05d22fd 코드 + 실제 state 사본 + enable_ai_ops_registry=true | **4 create / 0 update / 0 delete**, 종료 코드 2(변경 있음) | `urlshortener-ai-ops` / `urlshortener-ai-ops-image-role` |
| 별도 AI 실습 state | AI ECR/IAM만 뽑은 임시 전용 root 초안. 기존 OIDC는 data 조회로 참조 | **4 create / 0 update / 0 delete**, 종료 코드 2 | `urlshortener-practice-ai-ops` / `urlshortener-practice-ai-ops-image-role` |
| 기존 전체 root를 새 practice로 사용 | 빈 local state + 제안 project_name=urlshortener-practice | **실패**: ALB 역할 name_prefix 길이가 38자 제한 초과 | 전체 실습 인프라를 포함하는 안이며 AI 등록만을 위한 대안으로 적용하지 않음 |

4개 생성 대상은 두 성공 안 모두 다음과 같다.

1. `aws_ecr_repository.ai_ops[0]`: IMMUTABLE, scan-on-push, AES256, force_delete=false.
2. `aws_ecr_lifecycle_policy.ai_ops[0]`: 태그 없는 이미지만 1일 후 만료. 태그 있는 배포/롤백 이미지는 자동 만료하지 않음.
3. `aws_iam_role.ai_ops_image[0]`: 세션 상한 3600초, 기존 GitHub OIDC provider, audience `sts.amazonaws.com`, subject `repo:f-lab-edu/F-Lab-DevOps:environment:ai-ops-image`.
4. `aws_iam_role_policy.ai_ops_image[0]`: 인증 token 발급 외에는 새 AI 저장소 ARN의 push/pull/조회로 제한. 앱 ECR·EKS·삭제·저장소 생성 권한 없음.

분리 전 production 비교 plan에서는 EKS·RDS·VPC·기존 IAM/ECR의 수정·삭제가 없었다. 위 표는 선택 전 비교 기록이다. 이후 사용자가 별도 state를 선택해 아래 전용 root를 구현했다. 당시 제안한 `urlshortener-practice`를 코드 기본 접두사로 사용하며 기존 클러스터 이름·환경은 변경하지 않는다. state 사본의 계획은 실제 적용 시점의 새 refresh/plan을 대신하지 않는다.

## 별도 AI state 선택·구현·재검증

2026-10-16 21:06:53 KST(UTC: `2026-10-16T12:06:53.076997Z`)에 분리한 코드로 AWS 읽기 refresh와 plan을 다시 확인했다. 사용자의 별도 state 선택에 따라 [전용 root](../../terraform/ai-ops-registry/README.md)를 만들었다.

- 기존 `terraform/ai_ops.tf`·활성 변수·AI output을 기존 root에서 제거하고 `terraform/ai-ops-registry/`로 이동했다. 기존 인프라의 backend나 `.terraform` 설정은 바꾸지 않았다.
- 같은 S3 bucket에 전용 key `urlshortener/ai-ops/practice/terraform.tfstate`를 사용한다. `backend.practice.hcl`에 암호화·native lockfile·계정 허용 목록을 명시했다. 실제 S3 backend 초기화는 실행하지 않았다.
- provider는 계정 `716174522908`을 대조하고 리전 `ap-northeast-2`·환경 `practice`·`-practice` 접두사 입력을 검증한다. 별도 state가 별도 계정/클러스터를 뜻하지는 않는다.
- 기존 OIDC는 ARN으로 data 조회만 한다. production이 소유하며, 새 root가 생성·수정·삭제·import하지 않는다. EKS/RDS/VPC와 DB 비밀번호 입력도 없다.
- 기본 비활성 변수는 유지하고 `terraform.tfvars.example`에서 true로 명시한다. AWS provider 6.40.0 잠금 파일과 Terraform 1.14.7을 사용한다.
- 호환성 CI에 전용 root의 `init -backend=false -lockfile=readonly`·validate를 추가했다. 이 변경의 원격 CI는 아직 실행하지 않았다.

| 재검증 대상 | 방법 | 결과 |
|---|---|---|
| 실제 전용 root + 예제 활성 입력 | backend 선언을 제외한 임시 사본·빈 local state·AWS 읽기 조회 | **4 create / 0 update / 0 delete**, 종료 코드 2 |
| AI 정의 제거 후 기존 production root | 현재 production state 읽기 사본·비밀값 없는 기존 입력·계획용 가상 DB 입력 | **0 create / 0 update / 0 delete**, 종료 코드 0 |
| 원본 보호 | 점검 전후 production state의 ETag·수정 시각·크기·버전 대조 및 로컬 backend key 확인 | 변경 없음. 새 AI state도 미생성 |
| 로컬 구성 | 두 root의 Terraform validate | 모두 통과 |

전용 root의 managed 생성 대상이 위의 4개뿐이며 OIDC·EKS·RDS·VPC 생성은 없음을 확인했다. ECR 이름·IAM 이름과 기존 OIDC ARN·environment subject도 대조했다. 새 AI 리소스는 미적용 상태이므로 이번 분리에 state 이동·import는 필요 없다. 원시 state·plan 사본은 검사 뒤 삭제하고 비밀값 없는 집계만 남겼다.

## 적용 전 남은 작업

1. 최신 상태로 전용 root의 plan을 다시 검토하고 실제 적용 단계에서 ECR/IAM 4개를 생성한다. 이번 작업에서는 apply하지 않았다.
2. GitHub `ai-ops-image` environment에 main만 허용하는 규칙·검토자를 준비하고 네 가지 AI 등록 변수를 설정한다. build-only environment는 등록 trust와 구분한다.
3. main/default branch에 수동 workflow가 포함되도록 검토·반영한다. 작업 브랜치 push 성공을 main 반영으로 간주하지 않는다.
4. 수동 등록 workflow·digest 대조·등록 artifact를 확인한 뒤 실제 EKS 대상과 배포 조건을 검증한다.

현재 검증 범위는 사전 점검과 변경 계획이다. 실제 ECR 등록·EKS 배포·ServiceAccount 허용/거부·CNI·PVC·배포/롤백 artifact 보존·B-02 import·proxy/TLS/자체 관측은 여전히 남아 있다. B-03 전체 완료로 표시하지 않는다. [이전 이미지/원격 CI 검증](B3-deployment.md), [이미지 등록·배포 관측 설계](B3-image-and-observations.md)를 함께 참고한다.
