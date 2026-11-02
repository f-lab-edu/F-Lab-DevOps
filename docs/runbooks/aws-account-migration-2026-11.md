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

새 계정에는 **스냅샷만 존재한다. DB 인스턴스와 프로젝트 인프라는 아직 복원하지 않았다.**

## 다음 순서

1. 최신 `main`을 기준으로 새 계정용 Terraform backend, 계정 허용 검사, EKS 관리자 주체, RDS `snapshot_identifier` 입력을 준비한다. 기존 계정의 state를 새 계정에 그대로 이전하지 않는다.
2. 새 계정의 비용 경보와 S3 state bucket을 준비하고, 새 계정 프로필을 명시한 Terraform plan에서 예상 생성 리소스와 계정 ID를 확인한다.
3. VPC·EKS·스냅샷 복원 RDS를 설치한다. 복원된 DB의 데이터·연결·비밀번호를 확인한 뒤 앱·모니터링·AI 리소스를 복원한다.
4. 새 계정의 ECR/OIDC와 GitHub 환경 변수·자격, Route53 Hosted Zone 및 Gabia NS 이전, 외부 HTTPS를 검증한다. DNS 이전 중에는 기존 zone을 유지하고 새 zone의 레코드를 대조한다.
5. 새 계정에서 주요 기능, AI 수집·보고서, 설치/정리 경로를 검증한 뒤 기존 계정 잔여 리소스를 목록으로 대조해 삭제한다. 기존 계정의 RDS 스냅샷·S3 state 버전·Route53 zone은 복원과 DNS 전환 검증 전에는 삭제하지 않는다.
6. 기존 계정의 과금 가능한 리소스가 0인지 서비스별 조회와 Billing/Cost Explorer로 재확인한다. 이미 발생한 사용료는 청구에 늦게 반영될 수 있으므로 확인 시각을 기록한다.

## 주의

- `terraform/rds.tf`는 현재 신규 DB 생성용이다. 스냅샷 복원 입력을 추가할 때 기존 계정의 기본 동작이 바뀌지 않도록 조건을 분리한다.
- 새 계정의 크레딧은 전체 EKS 상시 운영비를 무한히 보장하지 않는다. 설치 전 예상 일일 비용과 운영 기간을 다시 검토한다.
- 비밀번호, API 키, Slack·Watchdog URL, GitHub 토큰은 이 문서와 Git에 기록하지 않는다. 기존 소유자 전용 로컬 자격 파일을 설치 절차에서 재사용한다.
