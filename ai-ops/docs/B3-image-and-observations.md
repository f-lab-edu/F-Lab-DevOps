# B-03 두 번째 묶음 — 독립 이미지 등록과 배포 관측 보존

최신 후속 상태: **2026-10-17 AI 전용 ECR/IAM 4개 생성과 GitHub 등록 환경·변수 설정 완료**, 적용 후 plan 변경 없음. 실제 OIDC 인증·이미지 등록·클러스터 배포는 대기 중이다. [적용 기록](B3-registry-apply.md)을 참고한다.

2026-10-16 기준 코드와 로컬 검증을 마쳤으며 **B-03 전체는 진행 중**이다. Terraform apply·AWS 조회·이미지 등록·새 원격 workflow 실행·실환경 배포는 하지 않았다. 첫 묶음의 이미지 실제 빌드와 권한/통신/PVC 검증도 여전히 대기 중이다.

## 1. 독립 ECR과 등록 identity

[AI 등록 전용 Terraform](../../terraform/ai-ops-registry/README.md)은 별도 root·state에서 기존 앱 ECR/배포 Role을 수정하지 않고 `${project_name}-ai-ops` 저장소와 `${project_name}-ai-ops-image-role`을 별도로 정의한다. `enable_ai_ops_registry`의 기본값은 false라 이번 추가 리소스는 명시 활성화 전에는 생성되지 않는다.

기본 접두사는 `urlshortener-practice`이고 새 state key는 `urlshortener/ai-ops/practice/terraform.tfstate`다. 기존 GitHub OIDC는 data 조회로만 참조한다. 기존 root의 미적용 AI 정의는 제거했으며 state 이동·import는 필요 없다. 이 분리는 클러스터 환경 변경이나 새 EKS 생성을 뜻하지 않는다.

저장소는 IMMUTABLE·scan-on-push·AES256·force_delete=false다. 태그가 있는 이미지는 자동 만료하지 않고 태그 없는 이미지만 1일 뒤 만료한다. 역할은 GitHub OIDC의 `sts.amazonaws.com` audience와 `repo:f-lab-edu/F-Lab-DevOps:environment:ai-ops-image` subject만 신뢰한다. 인증 token 발급만 Resource `*`, 나머지 push/pull/조회 권한은 해당 AI 저장소 ARN으로 제한한다. 삭제·저장소 생성·앱 저장소·EKS·Git 쓰기 권한은 없다. [AWS ECR push 권한 안내](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-push-iam.html)

GitHub environment를 사용하면 OIDC subject는 브랜치가 아닌 environment를 가리킨다. 따라서 **`ai-ops-image` environment에 main만 허용하는 배포 브랜치 규칙과 검토자를 설정해야 한다.** workflow의 main 검사도 함께 사용하며 이 파일만으로 GitHub의 보호 규칙이 생겼다고 간주하지 않는다. [GitHub AWS OIDC 안내](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)

## 2. 수동 이미지 workflow

[ai-ops-image.yml](../../.github/workflows/ai-ops-image.yml)은 workflow_dispatch만 받고 `publish` 기본값은 false다. 일반 push/PR의 이미지 빌드는 기존 [AI 전용 CI](../../.github/workflows/ai-ops-ci.yml)가 맡고 이 workflow는 수동 등록 경로다.

1. `publish=false`는 `ai-ops-image-build` environment에서 잠긴 의존성·단위 시험·linux/amd64 빌드를 확인한다. 통신 없는 read-only/non-root 컨테이너에서 CLI와 런북을 검사한다. AWS 인증·조회·push는 하지 않는다. 이 build environment에는 등록 Role의 trust가 없다.
2. `publish=true`는 `ai-ops-image` environment와 main이 필요하다. 아래 GitHub variables를 검사하고 전용 OIDC 역할을 사용한다. 저장소가 지정 계정·IMMUTABLE·scan-on-push·AES256인지 확인한다.
3. 빌드 검증한 **같은 이미지 ID**에 `sha-<전체 source SHA>-r<run ID>-a<attempt>` 태그를 붙여 push한다. 다시 빌드하지 않는다. ECR의 registry digest로 pull한 이미지 ID가 검증 이미지와 일치해야 등록을 성공으로 기록한다. tag는 시도마다 달라 IMMUTABLE 재실행 충돌을 줄인다.
4. [write-ai-ops-image-record.py](../../.github/scripts/write-ai-ops-image-record.py)는 source revision·로컬 image ID·tag·실제 등록 여부·registry digest·Dockerfile/lock/runbook index hash·UTC 시각만 기록한다. 빌드 전용이면 repository와 registry digest는 null이다. 환경변수 전체·token·키를 저장하지 않는다.
5. `ai-ops-image-<run ID>-<attempt>` artifact에 `image-registration.json`을 30일 보존한다. 업로드 실패는 workflow 실패로 남는다. GitOps values 수정·Git push·자동 배포는 하지 않는다.

| GitHub variable | 설정 근거 |
|---|---|
| `AI_OPS_AWS_ACCOUNT_ID` | 검토한 AWS 계정 12자리 |
| `AI_OPS_AWS_REGION` | 실제 ECR 리전 |
| `AI_OPS_ECR_REPOSITORY` | `${project_name}-ai-ops` 이름. 앱 저장소 이름은 거부 |
| `AI_OPS_IMAGE_ROLE_ARN` | Terraform의 `ai_ops_image_role_arn` output |

`ai_ops_ecr_repository_url` output과 등록 artifact의 digest를 검토한 뒤 별도의 환경 values에 `image.repository`·`image.digest`를 기록한다. Argo source revision도 chart가 포함된 검토 커밋으로 고정한다. 로컬 image ID와 registry manifest digest는 서로 다른 값이므로 바꿔 쓰지 않는다. 빌드 통과·등록 성공이 보안 취약점 없음이나 실환경 수집 성공을 뜻하지 않는다. scan-on-push 설정을 확인할 뿐 취약점 결과의 severity gate는 아직 없다.

## 3. 배포·롤백 전후 자료의 처리 흐름

기존 앱 [ci-cd.yml](../../.github/workflows/ci-cd.yml)의 main 전용 배포와 [verify-gitops-deployment.sh](../../.github/scripts/verify-gitops-deployment.sh)의 판정 로직은 유지한다. 검증을 감싼 셸에 시작/종료 UTC 출력만 추가하며 원래 종료 코드를 그대로 반환한다.

1. 앱 배포 검증 직후 성공/실패 결과·예상 GitOps commit·예상 image·검증 시각을 준비한다.
2. [capture-deployment-observation.py](../../.github/scripts/capture-deployment-observation.py)가 같은 앱의 Argo 응답을 메모리에서 한 번 조회한다. 응답은 1MiB, 읽기 기한은 8초로 제한하고 stderr와 원시 JSON 파일을 남기지 않는다. token은 기존 환경변수로만 전달하며 새 수집 명령 인자에 넣지 않는다.
3. Application 이름·Argo namespace·repo/path·배포 namespace/server를 대조한다. revision·고정 enum 상태·원본 관측 시각과 허용한 앱 image 참조만 선별한다. `name`·사용자 URL·메시지·Helm values·전체 env·인증값·원시 오류는 저장하지 않는다. image는 현 CI의 sha/release 태그·초기 v1·sha256 digest만 허용하고 나머지는 제외 개수만 기록한다.
4. `deployment.json`을 `deployment-observation-<run ID>-<attempt>-deployment` artifact에 보존한다. 검증 실패에 대한 이 처리는 **롤백 전에** 수행한다.
5. 이전 이미지가 있으면 기존 GitOps 롤백과 복구 검증을 실행한다. 성공·실패 뒤 같은 방식으로 `rollback.json`을 별도의 `...-rollback` artifact에 보존한다. Git push 등 검증 시작 전 실패는 실제 복구 commit/검증 시각을 null로 남길 수 있다.
6. 최초 배포 검증이 실패했다면 복구가 성공해도 최종 job은 실패다. 관측 실패를 복구 성공이나 정상 보고서로 바꾸지 않는다. 이전 이미지가 없거나 롤백이 실행되지 않으면 rollback artifact를 만들어 실행한 척하지 않는다.

수집·업로드는 각 단계 continue-on-error와 1분 제한을 사용해 실패가 롤백을 막지 않게 한다. 다만 자료를 원격 보존하는 동안 **롤백 시작이 지연될 수 있다**. 정상 동작 시 수집은 8초 이내 읽기 기한이고 업로드 단계는 최대 1분이다. runner 장애·강제 취소·GitHub artifact 서비스 장애에는 보존을 보장하지 않는다. artifact 업로드 실패는 단계 로그에서 확인해야 하며 정상 보존으로 간주하지 않는다. v4 artifact는 단계별로 고유한 이름을 사용한다. [업로드 action의 동작·제한](https://github.com/actions/upload-artifact/tree/ea165f8d65b6e75b540449e92b4886f43607fa02)

## 4. 관측 JSON이 말하는 것과 말하지 않는 것

| 필드 | 뜻 |
|---|---|
| `stage`, `verification_result` | 어느 검증 단계가 성공/실패했는지. 현재 Argo 상태와 별개 |
| `expected_revision`, `expected_image` | CI가 해당 시도에서 확인하려던 대상. 롤백 시작 전 실패면 commit이 없을 수 있음 |
| `verification_started_at`, `verification_finished_at` | 검증 실행 시각. 확인하지 못하면 null |
| `observed_at` | 자료 선별 시점. 장애 발생 시각으로 바꾸지 않음 |
| `observed.reconciled_at` | Argo 응답에 들어 있던 reconcile 시각을 UTC로 변환한 값 |
| `observed.revision`, `operation_revision`, 상태·image | 조회 때 확인한 현재 자료. 과거 실패 당시 상태를 완전히 재현하지 않음 |
| `collection_status`, `reason` | available 또는 unavailable. 설정 누락·조회 실패·timeout·과대 응답·대상 불일치·무효 응답은 고정 사유와 observed=null |

현재 Healthy이거나 다른 revision을 보고 있어도 `verification_result=failure`를 success로 바꾸지 않는다. artifact에는 원인 확정·전체 Pod 건강·복구 완료를 추론하는 필드가 없다. repo/namespace 정보는 있지만 실제 cluster identity를 검증한 기록은 아니며 practice target으로 임의 재표기하지 않는다.

**아직 B-02 자동 수집/분석 입력에는 연결하지 않았다.** worker에 GitHub/ECR 자격증명을 주지 않는다. 운영자가 artifact를 취득한 뒤 출처·run/attempt·target·시간·hash를 검토하는 import 계약과 누락/충돌 처리, 원격 취득 identity·통신 경로는 후속이다. 따라서 현재 B 수집기의 `missing_history`를 제거하지 않았으며 S10 롤백 진단의 실환경 성공도 주장하지 않는다.

## 5. 검증과 남은 작업

- Python 3.12.14/Pydantic 2.13.5 전체 **142건** 통과. 새 release/관측 시험은 12건이다.
- 원시 비밀 문자열·다른 앱/namespace/repo·잘못된 JSON/상태·큰 응답·timeout·HTTP 인증 전송 차단·UTC 변환·파일 권한을 확인했다.
- 실제 검증/롤백 셸을 가짜 git/yq/Argo CLI와 함께 실행해 검증 성공/실패, 복구 성공/실패, Git push 조기 실패와 최종 실패 유지까지 확인했다. 실제 Git 수정·push·AWS·Argo 접속은 하지 않았다.
- Terraform 1.14.7의 `validate`와 **변경한 세 파일의 fmt 검사**가 통과했다. 로컬 전체 재귀 fmt는 기존 Git 제외 `terraform.tfvars`의 서식 차이를 보고했으며 그 파일은 변경하지 않았다.
- Helm 정적 검사, workflow YAML·셸 구문과 compileall·Git 공백 검사를 통과했다. AI CI는 관측 script·앱 workflow·이미지 workflow 변경에도 작동하도록 paths를 보완했다.
- 후속 로컬 검증에서 linux/amd64 이미지 실제 build/run·통신 없는 합성 규칙 조사/재조회·배포 로컬 진단을 통과했다. B3-deployment.md의 후속 기록을 참고한다. 새 workflow·보호 environment·OIDC·push/digest 확인·artifact 업로드는 원격 실행 전이다.

실제 적용 전에는 별도 state/환경·ECR/IAM plan 검토, GitHub environment 보호 규칙·variables, 이미지 빌드·등록을 확인해야 한다. 이어 실제 EKS 대상·API/DNS/Prometheus·CA/token·CNI·StorageClass를 대조하고 읽기 허용/거부·PVC 재연결을 검증한다. 모델 proxy·내부 수신 TLS·자체 관측과 worker/queue도 아직 연결하지 않았다. [첫 묶음 배포 진단 안내](B3-deployment.md), [AI 작업 리스트](../../실습%20가이드%20문서%20모음/AI/구현계획/AI%20작업%20리스트.md)를 함께 참고한다.

후속 실제 AWS 읽기 점검과 비교 plan은 [AWS 적용 전 점검 기록](B3-aws-preflight.md)에 별도로 기록했다. 새 리소스 적용은 하지 않았다.
