# AWS 계정 이전 작업 기록

## 목표와 계정

- 기존 계정: `716174522908` (`default` CLI 프로필)
- 새 계정: `764428911194` (`urlshortener-new` CLI 프로필)
- 리전: `ap-northeast-2`
- 새 계정에서 프로젝트 전체를 복원하고 검증한 뒤, 기존 계정의 과금 가능한 프로젝트 리소스를 정리한다. 두 계정의 명령에는 항상 프로필을 명시한다.
- 새 계정의 로그인은 사용자가 선택한 루트 계정의 임시 `aws login` 세션이다. 장기 루트 액세스 키를 만들거나 저장하지 않는다.

## 2026-11-02 완료: 최신 RDS 스냅샷 이전

1. 기존 계정의 최신 최종 스냅샷 `urlshortener-postgres-final-snapshot-urlshortener-postgres-e6632d71`을 선택했다. PostgreSQL 17.9, 20 GiB이며 원본은 `available` 상태로 유지한다.
2. AWS 관리형 RDS 키로 암호화된 원본은 계정 간 직접 공유할 수 없으므로, 기존 계정의 임시 고객 관리형 KMS 키로 `urlshortener-migration-share-20261102`을 만들어 새 계정에만 공유했다.
3. 새 계정에서 공유본을 새 계정 소유의 AWS 관리형 RDS 키로 다시 복사했다. 복사본 `urlshortener-migrated-final-20261102`은 `available`, 진행률 100%, PostgreSQL 17.9, 20 GiB다. ARN은 `arn:aws:rds:ap-northeast-2:764428911194:snapshot:urlshortener-migrated-final-20261102`이다.
4. 이전 완료 후 기존 계정의 임시 공유 권한을 회수하고 임시 공유 스냅샷을 삭제했다. 이전 전부터 있던 최종 스냅샷과 과거 수동 스냅샷은 DB 복원 검증 전까지 유지한다.
5. 임시 고객 관리형 KMS 키 `126f4ced-3727-496a-8f6c-87f16e1bdf54`는 이를 쓰는 RDS 스냅샷이 없음을 확인한 뒤 삭제 예약했다. 상태는 `PendingDeletion`, 예정 시각은 2026-11-09 12:04 KST다.

스냅샷 복사 직후에는 새 계정에 DB 인스턴스가 없었다. 이후 아래 Terraform 적용으로 DB와 클러스터를 만들었다.

## 새 계정 Terraform 입력 준비

2026-11-02에 기존 계정 기본값을 보존하면서 새 계정 전용 입력을 추가했다.

| 대상 | 새 계정 입력 | 안전 경계 |
| --- | --- | --- |
| 운영 backend | `terraform/backend.new-account.production.hcl` | 새 S3 버킷과 새 계정 ID만 허용한다. 기존 state를 복사하지 않는다. 기존 운영·실습 backend도 기존 계정 ID만 허용한다. |
| 운영 provider | `account.new.tfvars.example`의 `expected_account_id` | 다른 계정 자격이면 plan 단계에서 거부한다. |
| EKS 관리자 | 새 계정의 기존 IAM 사용자 `admin-user-pjh` | 루트 계정을 EKS 접근 항목에 넣지 않는다. |
| 이전 중 Kubernetes 작업자 | 새 프로젝트 전용 `urlshortener-operator`의 임시 AWS CLI 로그인 | 기존 `admin-user-pjh`의 생성 경위와 비밀번호를 사용자가 알지 못해 이를 변경하지 않는다. 새 사용자의 권한은 현재 EKS 클러스터 관리와 로그인에 필요한 범위로 제한한다. |
| RDS Primary | `urlshortener-migrated-final-20261102` | 신규 DB 이름·마스터 사용자·비밀번호 생성 입력을 복원 호출에 전달하지 않는다. |
| AI backend | `terraform/ai-ops-registry/backend.new-account.practice.hcl` | 운영과 별도 key를 쓰며 새 계정만 허용한다. |

새 계정 state 버킷 `urlshortener-tfstate-764428911194-ap-northeast-2`를 만들고 버전 관리·SSE-S3 암호화·퍼블릭 접근 차단을 확인했다. 운영·AI root 각각의 state는 아래 적용으로 새로 생성했다. 기존 계정 state를 복사하거나 이전하지 않았다. **기존 작업 디렉터리에서 `-migrate-state`를 사용하지 않는다.** DB를 스냅샷으로 복원하면 당시의 마스터 비밀번호가 이어진다. 복원 후 보관된 자격으로 실제 접속을 확인하고 필요한 경우 별도의 비밀번호 회전 절차를 계획한다. 스냅샷 식별자는 최초 복원 이후의 plan에도 계속 유지해 예기치 않은 DB 교체를 피한다.

현재 Terraform CLI 1.14.7의 S3 backend는 AWS CLI `aws login` 프로필을 직접 읽지 못했다. AWS의 [프로세스 자격 증명 절차](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html#cli-configure-sign-in-process)를 따라 별도의 임시 설정 파일을 만든다. 이 파일에는 키 자체가 없고, AWS CLI가 로그인 세션에서 단기 자격을 꺼내 Terraform에 전달하는 명령만 있다. 아래 파일은 Git에 넣지 않고 소유자만 읽을 수 있게 한다.

```bash
mkdir -p -m 700 /private/tmp/urlshortener-account-migration-20261102
cat > /private/tmp/urlshortener-account-migration-20261102/aws-terraform-config <<'EOF'
[profile urlshortener-new]
login_session = arn:aws:iam::764428911194:root
region = ap-northeast-2

[profile urlshortener-terraform]
credential_process = aws configure export-credentials --profile urlshortener-new --format process
region = ap-northeast-2
EOF
chmod 600 /private/tmp/urlshortener-account-migration-20261102/aws-terraform-config
```

루트 로그인 세션이 만료되면 사용자가 다시 `aws login --profile urlshortener-new`를 완료해야 한다. 장기 루트 액세스 키는 만들지 않는다.

```bash
aws sts get-caller-identity --profile urlshortener-new --query Account --output text
aws rds describe-db-snapshots --profile urlshortener-new --region ap-northeast-2 \
  --db-snapshot-identifier urlshortener-migrated-final-20261102 \
  --query 'DBSnapshots[0].{Status:Status,KmsKeyId:KmsKeyId}'

AWS_PROFILE=urlshortener-new EXPECTED_AWS_ACCOUNT_ID=764428911194 \
  bash terraform/bootstrap-state-bucket.sh

# 새 Git 작업 디렉터리에서만 실행한다. 첫 단계에서는 plan만 검토한다.
AWS_CONFIG_FILE=/private/tmp/urlshortener-account-migration-20261102/aws-terraform-config \
AWS_PROFILE=urlshortener-terraform terraform -chdir=terraform init \
  -backend-config=backend.new-account.production.hcl -reconfigure -lockfile=readonly
AWS_CONFIG_FILE=/private/tmp/urlshortener-account-migration-20261102/aws-terraform-config \
AWS_PROFILE=urlshortener-terraform terraform -chdir=terraform plan \
  -var-file=account.new.tfvars.example -out=/private/tmp/urlshortener-new-account.plan
```

운영 root는 새 계정 backend 초기화와 `terraform validate`를 통과했다. 비어 있는 새 state에서 실제 AWS를 조회한 plan은 **생성 102건·변경 0건·삭제 0건**이며, 계획된 리소스 값에 기존 계정 ID는 없었다. RDS Primary는 복사된 스냅샷을 사용하고 신규 DB 이름·사용자·비밀번호 입력은 null이며, EKS 관리자 접근 항목은 새 계정의 `admin-user-pjh`를 가리킨다. 이 계획을 새 계정에서 적용해 **102개 생성·변경 0·삭제 0**으로 완료했다. EKS `urlshortener`는 `ACTIVE`(1.35), 노드 그룹은 3대 `ACTIVE`, EBS CSI 애드온은 `ACTIVE`다. RDS Primary는 `available`·Multi-AZ·암호화, Replica는 `available`·암호화·Primary 복제 원본을 확인했다.

전용 IAM 사용자로 EKS에 일회성 `migration-check` namespace를 만들고, 보존된 로컬 DB 비밀번호의 줄 끝 개행을 제거한 소유자 전용 임시 복사본으로 Secret을 생성했다. PostgreSQL 17 클라이언트 Job이 TLS를 요구하는 연결에서 **Primary와 Read Replica 모두 기존 비밀번호로 인증**했다. `urldb`의 `items` 테이블이 있고, Primary `pg_is_in_recovery()=false`·Replica `true`이며 **두 곳 모두 0행**이다. 일회성 로컬 복사본, namespace, Secret, Job을 삭제했고 원본 자격 파일은 보존했다. 사용자는 기존 DB에도 저장된 아이템이 없어 0행이 기대한 결과라고 확인했다. 스냅샷 복원·인증·기본 데이터 상태의 검증을 통과했지만, 앱 연결·쓰기 경로와 최종 기능 검증 전에는 기존 스냅샷을 삭제하지 않는다.

새 계정 루트 세션으로 최초 EKS를 만든 뒤, 해당 세션의 `kubectl` 인증은 거부됐다. EKS 관리자 접근 항목에 IAM 사용자 `admin-user-pjh`만 등록돼 있기 때문이다. 사용자가 승인한 `urlshortener-migration-operator` IAM 역할·EKS 접근 항목·정책 연결 3개를 적용했지만, 실제 `kubectl` 시험에서 AWS가 **`Roles may not be assumed by root accounts`**라고 거부했다. 루트가 AssumeRole할 수 있다는 설계 가정이 잘못됐다. Terraform 계획에서 이 세 리소스의 삭제만 확인한 뒤 제거했고, 역할 `NoSuchEntity`와 기존 `admin-user-pjh` 접근 항목 유지를 검증했다.

`admin-user-pjh`는 2026-03-25에 만들어졌고 MFA·관리자 그룹·활성 액세스 키가 있으나 사용자는 생성 경위와 초기 비밀번호를 알지 못한다. 소유 관계를 확인하기 전에는 이 사용자의 비밀번호·키·권한을 바꾸지 않는다. 대신 사용자가 새 프로젝트 전용 사용자와 현재 EKS 클러스터 관리자 권한을 명시적으로 승인했다. `enable_project_operator=true` 계획의 **6개 추가·변경 0·삭제 0**을 적용해 IAM 사용자 `urlshortener-operator`와 EKS 접근 항목을 만들었다. AWS 권한은 `SignInLocalDevelopmentAccess`, 본인 비밀번호 변경, 현재 EKS 클러스터 `DescribeCluster`로 제한된다. EKS 접근 항목에는 현재 클러스터에만 `AmazonEKSClusterAdminPolicy`가 연결돼 있다. 이 정책은 Kubernetes 안에서는 전체 관리 권한이므로 계정 이전을 마친 뒤 유지 필요성을 검토한다. 사용자의 장기 액세스 키는 0개다. Terraform state 밖의 소유자 전용 파일에 초기 콘솔 비밀번호를 생성하고 첫 로그인에서 비밀번호 변경을 강제했다. CLI 요청 JSON은 적용 후 삭제했다.

사용자가 첫 콘솔 로그인과 비밀번호 변경을 완료했고, AWS API에서 `PasswordResetRequired=false`를 확인했다. 사용이 끝난 초기 비밀번호 파일을 삭제했다. `aws login` 임시 세션은 새 계정의 `arn:aws:iam::764428911194:user/urlshortener-operator`로 확인됐으며, 별도 kubeconfig로 EKS 노드 **3대가 모두 Ready**임을 조회했다. 사용자는 새 계정 루트 세션에서 이 IAM 사용자에 MFA를 직접 등록했다. AWS API에 MFA 디바이스 1개가 나타나고 콘솔 유형은 **가상 인증 앱**이다. 디바이스 이름에는 등록 준비 중 입력한 `passkey`가 남아 있으나 실제 유형은 패스키가 아니다. IAM 사용자의 MFA 자가 등록 권한은 추가하지 않았다. [AWS CLI 임시 로그인](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)은 만료 후 갱신해야 한다.

AI root는 `terraform/ai-ops-registry/account.new.tfvars.example`과 전용 새 계정 backend를 사용한다. 운영 root가 GitHub OIDC provider를 만든 뒤 AI root의 plan을 계산했고 **ECR 저장소·수명주기 정책·GitHub 이미지 등록 IAM 역할·정책 4개 생성, 변경·삭제 0**으로 적용했다. ECR `urlshortener-practice-ai-ops`는 새 계정 URI, 불변 태그, AES256 암호화, 푸시 검사를 확인했다. 이미지 자체는 아직 이전하지 않았다. 두 root의 계획 파일은 비밀 산출물로 취급하고 Git에 추가하지 않는다.

`ai-ops-image` GitHub 환경의 `AI_OPS_AWS_ACCOUNT_ID`와 `AI_OPS_IMAGE_ROLE_ARN`을 새 계정 값으로 바꾸고, `AI_OPS_AWS_REGION`·`AI_OPS_ECR_REPOSITORY`를 포함한 네 변수가 새 ECR/IAM 출력과 일치하는지 확인했다. **AI 이미지 등록 workflow는 아직 실행하지 않았고 AI ECR에는 이미지가 없다.** 앱 CI의 기존 계정 고정값은 저장소 변수 `APP_AWS_ACCOUNT_ID`를 읽도록 바꿨고 새 계정 값을 GitHub 변수에 저장했다. 해당 코드가 [PR #136](https://github.com/f-lab-edu/F-Lab-DevOps/pull/136)에서 `main`에 병합된 뒤 수동 앱 CI [#41393985677](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/41393985677)를 `image-only` 모드로 실행했다. 새 계정 ECR `urlshortener`에 `sha-8ea1673` 이미지가 등록됐고 digest `sha256:22f0d8d9ca2c06b83f93e2a5acff51dbf20fbd9382652f54a3c7fb6c4c7aab57`을 artifact와 AWS 조회로 대조했다. 이 실행은 앱을 배포하지 않았다. 운영 Helm 값의 ECR URI·태그를 이 이미지로 전환한 뒤 앱 설치를 진행한다.

AI 실습·모델 코드와 `ai-ops/lab/values.yaml`에도 이전 계정의 EKS ARN·ECR URI가 고정된 곳이 있다. 이를 새 계정 기준으로 수정·검증하기 전에는 해당 이미지를 클러스터에 배포하거나 모델 캠페인을 재개하지 않는다.

## 기존 계정 잔여물 중간 점검

새 계정 검증 전에는 기존 계정 `716174522908`의 복구 자료와 DNS를 삭제하지 않는다. 2026-11-02 중간 조회에서 프로젝트 EKS 클러스터·RDS 인스턴스·ECR 저장소는 없었고, `urlshortener` 이름의 수동 RDS 스냅샷 **21개**와 `bidservice.store.` Hosted Zone `/hostedzone/Z01710062CSM40Z2TLTGS`는 남아 있었다. S3 state 버킷과 다른 서비스 잔여물은 최종 정리 전에 별도 목록으로 대조한다. 스냅샷·Hosted Zone·S3 저장은 계정 이전이 완료되기 전까지 비용이 생길 수 있으므로, 새 계정 데이터·DNS 전환 검증 후 순서대로 정리하고 과금 항목을 재확인한다.

## 새 계정 비용 사전 점검

2026-11-02 KST에 새 계정 `764428911194`의 Billing 크레딧 화면에서 **$100 중 $87.93 잔액, $12.07 사용, 만료일 2027-03-24**를 확인했다. Billing 수치는 약 하루 늦게 갱신될 수 있다. 해당 계정에는 점검 당시 AWS Budgets 예산이 없었다. 아래 금액은 새 계정에 아직 적용하지 않은 Terraform 계획과 서울 리전 AWS Price List API의 온디맨드 단가로 계산한 **기본 시간 요금**이다.

| 리소스 | 수량 | 단가(USD/시간) | 하루 기본 요금(USD) |
| --- | ---: | ---: | ---: |
| EKS 클러스터, 표준 지원 | 1 | 0.1000 | 2.40 |
| EC2 `t3.large` Linux 노드 | 3 | 0.1040 | 7.488 |
| RDS PostgreSQL `db.t3.micro` Multi-AZ Primary | 1 | 0.0560 | 1.344 |
| RDS PostgreSQL `db.t3.micro` Single-AZ Replica | 1 | 0.0280 | 0.672 |
| NAT Gateway | 1 | 0.0590 | 1.416 |
| **최초 Terraform 계획 합계** | | **0.5550** | **13.32** |
| 이후 Traefik 설치 시 예상 NLB | 1 | 0.0225 | 0.54 |
| **NLB 설치 후 합계** | | **0.5775** | **13.86** |

EKS의 [표준 지원 클러스터 요금](https://aws.amazon.com/eks/pricing/)과 나머지 단가는 [AWS Price List API](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html)에서 조회했다. EC2 SKU `822X28N5YHCZG5KZ`, RDS Primary `XWTGXDNYFBADS2FT`, RDS Replica `ZGG2J4HPEAESP6A3`, NAT `P63FHTYZXQBC6HX5`, NLB `FTU9EQE348PCR5Y2`를 사용했다. NAT의 데이터 처리량, NLB의 용량 단위, RDS·EBS 저장공간, 퍼블릭 IPv4, 데이터 전송, CloudWatch, KMS, S3, Route53 등의 요금은 합계에서 빠졌다. 특히 [NAT는 시간과 처리 데이터에 각각 과금](https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-pricing.html)된다.

모든 요금이 해당 크레딧 적용 대상이고 잔액이 변하지 않는다는 가정에서도, NLB 설치 후 **$87.93 ÷ $13.86 ≈ 6.3일**이므로 이는 운영 가능 기간의 낙관적인 상한이다. 실제 기간은 추가 사용량·크레딧 적용 범위·청구 지연에 따라 짧아질 수 있다. 새 계정에는 크레딧을 제외한 총 사용액을 추적하는 `urlshortener-migration-monthly-gross` **월 $60**과 `urlshortener-migration-daily-gross` **일 $20** 예산을 생성했다. 생성 직후 두 예산의 계산 사용액은 $0이었으므로 Billing 크레딧 화면의 $12.07 사용액과 직접 같다고 해석하지 않는다. 사용자가 지정한 이메일 수신자 1명을 월간 **50%($30)·80%($48)·100%($60)**, 일간 **100%($20)** 실제 사용액 경보 각각에 등록했다. AWS API에서 네 경보와 각 수신자 1명의 연결을 확인했다. 경보 메일의 실제 도착 여부는 기준 초과 전이라 아직 검증할 수 없다. 이메일 주소 자체는 문서와 Git에 남기지 않는다. [AWS Budgets 알림은 보통 하루 단위로 갱신되며 과금을 강제로 멈추지 않는다](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-best-practices.html). 비용을 막으려면 사용 종료 시 인프라를 정리해야 한다.

## 다음 순서

1. 새 계정 비용 단가·크레딧 검토, 월간·일간 예산 생성, 수신 이메일 경보 연결은 완료했다. 실제 메일 도착은 기준 초과 시 확인한다. state bucket과 최초 plan 검증도 완료했다.
2. VPC·EKS·스냅샷 복원 RDS와 AI ECR/IAM root를 설치하고 실패한 임시 역할 3개를 제거했다. 전용 `urlshortener-operator` 생성·첫 로그인·비밀번호 변경·MFA·임시 CLI 로그인과 EKS 노드 3대 Ready 확인까지 완료했다. DB Primary·Replica의 기존 비밀번호 인증과 예상한 `items` 0행을 확인했다. 새 계정 ECR에 앱 이미지도 등록했다. 다음은 운영 Helm 값의 이미지 전환과 앱·모니터링·AI 리소스 복원이다.
3. 새 계정의 ECR/OIDC와 GitHub 환경 변수·자격, Route53 Hosted Zone 및 Gabia NS 이전, 외부 HTTPS를 검증한다. DNS 이전 중에는 기존 zone을 유지하고 새 zone의 레코드를 대조한다.
4. 새 계정에서 주요 기능, AI 수집·보고서, 설치/정리 경로를 검증한 뒤 기존 계정 잔여 리소스를 목록으로 대조해 삭제한다. 기존 계정의 RDS 스냅샷·S3 state 버전·Route53 zone은 복원과 DNS 전환 검증 전에는 삭제하지 않는다.
5. 기존 계정의 과금 가능한 리소스가 0인지 서비스별 조회와 Billing/Cost Explorer로 재확인한다. 이미 발생한 사용료는 청구에 늦게 반영될 수 있으므로 확인 시각을 기록한다.

## 주의

- `terraform/rds.tf`의 기존 신규 DB 생성 경로는 그대로 두고 스냅샷 복원 경로를 조건으로 추가했다. AWS 생성과 기존 비밀번호를 이용한 Primary·Replica 접속을 검증했고, 사용자는 `items` 0행이 기존 상태와 일치한다고 확인했다.
- 새 계정의 크레딧은 전체 EKS 상시 운영비를 무한히 보장하지 않는다. 설치 전 예상 일일 비용과 운영 기간을 다시 검토한다.
- 비밀번호, API 키, Slack·Watchdog URL, GitHub 토큰은 이 문서와 Git에 기록하지 않는다. 기존 소유자 전용 로컬 자격 파일을 설치 절차에서 재사용한다.
