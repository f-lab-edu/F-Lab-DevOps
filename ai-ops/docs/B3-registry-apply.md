# B-03 AI 전용 ECR·IAM 적용과 GitHub 등록 환경

최신 후속 상태: AI 전용 [PR #32](https://github.com/f-lab-edu/F-Lab-DevOps/pull/32)에 AI root가 원격 등록됐다. 사용자 요청으로 미사용 SonarCloud 자동 분석을 비활성화하고 workflow를 제거했다. main 병합·수동 이미지 등록 검증은 아직 수행하지 않았다. [main 반영 검토 기록](B3-image-main-review.md)을 참고한다.

2026-10-17 KST 기준 AI 이미지 등록 기반을 실제 AWS에 생성했다. **Terraform 적용은 생성 4개·수정 0개·삭제 0개**, 적용 후 plan은 종료 코드 0의 `No changes`였다. 이미지 등록이나 EKS 배포가 완료된 단계는 아니다. B-03 전체는 진행 중이다.

## 무엇을 만들었는가

[AI 전용 Terraform root](../../terraform/ai-ops-registry/README.md)에서 다음 리소스를 생성했다.

| 리소스 | 실제 이름·설정 | 역할 |
|---|---|---|
| ECR 저장소 | `urlshortener-practice-ai-ops` | 앱 이미지와 분리해 AI 이미지를 보관 |
| ECR lifecycle policy | 태그 없는 이미지만 1일 뒤 만료 | 태그 있는 배포·롤백 이미지는 이 정책으로 자동 삭제하지 않음 |
| IAM Role | `urlshortener-practice-ai-ops-image-role` | GitHub 등록 environment의 OIDC 토큰만 신뢰 |
| IAM inline policy | `ai-ops-image-only` | 인증 token 발급과 해당 AI 저장소 push/pull/조회만 허용 |

- 계정·리전: `716174522908` / `ap-northeast-2`.
- 저장소 URL: `716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops`.
- 등록 역할 ARN: `arn:aws:iam::716174522908:role/urlshortener-practice-ai-ops-image-role`.
- S3 bucket: `urlshortener-tfstate-716174522908-ap-northeast-2`.
- AI state key: `urlshortener/ai-ops/practice/terraform.tfstate`.

실제 ECR에서 IMMUTABLE·scan-on-push·AES256과 lifecycle 설정을 재조회했다. AI state 객체의 AES256 암호화와 managed resource 4개도 확인했다. 기존 GitHub OIDC는 data 조회만 하며 기존 production에서 계속 관리한다. 기존 `terraform/`의 backend와 production state의 ETag·LastModified·크기·VersionId는 적용 전후 동일했다. EKS/RDS/VPC·앱 ECR은 이번 적용 대상에 없다.

## 적용 과정과 중단 처리

이전 적용 시도는 사용량 제한으로 자동 승인 검토를 완료하지 못해 명령 자체가 실행되지 않았다. 사용자의 재개 요청 후 최신 plan을 새로 만들고, 전용 backend·계정·입력과 managed resource 4개의 create-only 동작을 대조한 뒤 저장한 계획을 적용했다. 원격 S3 잠금을 사용했으며 state 이동·import·production backend 재설정은 하지 않았다.

`terraform.tfvars`는 예제와 같은 비밀값 없는 활성 입력으로 로컬에 유지한다. `enable_ai_ops_registry=true`를 false로 바꾸면 기존 AI 리소스의 삭제 계획이 생기므로 일시 중지 목적으로 변경하지 않는다. 실행이 끝난 `plan.out`은 삭제했다. 이후 적용이 필요하면 최신 상태로 계획을 다시 생성한다.

## GitHub 환경과 변수

[ai-ops-image environment](https://github.com/f-lab-edu/F-Lab-DevOps/settings/environments)의 설정을 생성·재조회했다.

- 배포 허용 규칙은 **branch 타입의 `main` 하나**다. 태그와 현재 작업 브랜치는 등록 환경의 허용 대상이 아니다.
- 필수 검토자는 현재 사용자 계정 `joungGo`다. `prevent_self_review=false`여서 본인이 실행한 job도 본인이 수동 승인할 수 있다. 실제 workflow의 환경 승인은 아직 수행하지 않았다.
- 아래 변수는 저장소 전체가 아닌 **`ai-ops-image` environment 범위**에 설정했다. 기존 repository variable이나 Secret은 변경하지 않았다.

| 변수 | 설정값 |
|---|---|
| `AI_OPS_AWS_ACCOUNT_ID` | `716174522908` |
| `AI_OPS_AWS_REGION` | `ap-northeast-2` |
| `AI_OPS_ECR_REPOSITORY` | `urlshortener-practice-ai-ops` |
| `AI_OPS_IMAGE_ROLE_ARN` | `arn:aws:iam::716174522908:role/urlshortener-practice-ai-ops-image-role` |

IAM trust는 기존 GitHub OIDC ARN, audience `sts.amazonaws.com`, subject `repo:f-lab-edu/F-Lab-DevOps:environment:ai-ops-image`만 허용한다. 환경 보호 규칙과 실제 IAM 문서를 각각 확인한 것이며 GitHub가 발급한 토큰으로 STS 인증까지 시험한 것은 아니다.

## 확인 결과와 한계

| 확인 항목 | 결과 |
|---|---|
| 최신 plan → 적용 | 생성 4·수정 0·삭제 0, apply 성공 |
| 적용 후 refresh/plan | 변경 없음, 종료 코드 0 |
| 실제 ECR·IAM 설정·state 범위 | 기대 설정·관리 대상 4개 확인 |
| production state·기존 backend | 적용 전후 변경 없음 |
| IAM 시뮬레이션: AI 저장소 `ecr:PutImage`·`ecr:BatchGetImage` | allowed |
| IAM 시뮬레이션: 앱 저장소의 같은 두 작업 | implicitDeny |
| IAM 시뮬레이션: AI 저장소 `ecr:DeleteRepository`·기존 클러스터 `eks:DescribeCluster` | implicitDeny |
| GitHub branch 규칙·검토자·환경 변수 | 재조회 일치 |
| main의 `ai-ops-image.yml` | 현재 404, 아직 미반영 |
| ECR 이미지 | 0개. 실제 push·registry digest·scan 결과·등록 artifact 미검증 |
| GitHub OIDC로 역할 인증 | 미실행. IAM 정책 시뮬레이션과 구분 |

시뮬레이터는 실제 API 호출이나 OIDC 인증 성공을 대신하지 않는다. 유료 AI 호출·이미지 push·main merge·EKS 배포는 이번 단계에서 수행하지 않았다. Terraform 분리 코드와 CI 추가 변경은 아직 작업 트리에 있으며 이 변경의 원격 CI도 미실행이다.

## 다음 순서

1. [수동 이미지 workflow](../../.github/workflows/ai-ops-image.yml)와 필요한 소스를 검토해 main/default branch에 포함한다. 기존 앱 배포 workflow가 main 변경에 반응하므로 영향 범위를 확인하고 반영한다.
2. main에서 `publish=false`로 독립 수동 workflow의 빌드·시험·등록 전 artifact 보존을 먼저 확인한다. 이 경로는 `ai-ops-image-build` environment를 사용하며 등록 IAM trust에 포함되지 않는다.
3. main에서 `publish=true`를 실행하고 사용자 검토자가 environment job을 승인한다. GitHub OIDC 인증·같은 이미지 push·digest pull 대조·등록 artifact를 확인한다.
4. 확인한 registry digest를 사용해 B-03 실환경 배포·ServiceAccount 허용/거부·CNI·PVC 검증을 진행한다. B-02 이력 import, 모델 proxy·내부 수신 TLS·자체 관측과 B-04 worker/queue는 별도 미완료다.

선택 전 비교와 state 분리 검증은 [AWS 사전 점검 기록](B3-aws-preflight.md), 이미지 등록 동작과 관측 artifact의 범위는 [이미지·배포 관측 안내](B3-image-and-observations.md)를 참고한다.
