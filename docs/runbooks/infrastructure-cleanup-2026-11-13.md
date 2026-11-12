# 2026-11-13 인프라 정리 완료

확인 시각: 2026-11-13T01:06:00.059273+09:00. 계정 `764428911194`·서울 리전의 프로젝트 **보존 대상을 제외한 실행 리소스 정리를 완료**했다. 이번 정리의 Terraform apply/destroy는 사용자의 직접 위임으로 Codex가 수행했다. 이후 일반 실행 선호는 기존 사용자 터미널 원칙을 유지한다.

## 실제 삭제 확인

- EKS·운영 VPC·Primary/Replica RDS·프로젝트 EC2·활성 NAT·LB/TG·VPC ENI·Karpenter SQS가 없다.
- 기록한 PVC/노드 루트 EBS **10개**, NAT EIP **1개**, ECR **2개**, 프로젝트 IAM Role **11개**의 부재를 확인했다. 기본 VPC/다른 워크로드 자원은 삭제하지 않았다.
- 운영/AI Terraform managed resource **각각0개**, 두 state의 잠금 없음. S3 state와 이전 버전은 보존했다.
- Terraform 밖의 프로젝트 CloudWatch 로그 그룹 **1개**도 원문 이벤트를 비공개 로컬 압축 파일에 백업·검증한 뒤 삭제했다. 다른 로그 그룹은 삭제하지 않았다.
- RDS Enhanced Monitoring의 프로젝트 소유 로그 스트림 **2개**도 백업·검증 후 삭제했다. 공유 `RDSOSMetrics` 그룹은 보존하며 남은 스트림은 **0개**다.
- 배포·실습 workflow6개는 중지 상태다. 삭제한 Argo 서버의 전용 Secret/변수와 임시 원복 Secret은 없고 GitHub 저장소/App/나머지 설정은 유지했다.

## 보존과 예약 삭제

- Route53 zone/레코드 전체가 삭제 전과 같다.
- IAM `urlshortener-operator`·로그인 프로필·기존 부착 정책과 로컬 원본 자격/키를 유지했다. 새 계정·프로필·권한을 만들지 않았다.
- 기존 수동 RDS 스냅샷 **6개**와 새 최종 스냅샷 `urlshortener-postgres-final-snapshot-urlshortener-postgres-6962a7a0`을 보존했다. 새 스냅샷은 **available·encrypted**, 복원에 필요한 KMS키는 **Enabled**다. 최소90일 보존 정책을 유지하며 자동 삭제를 구성하지 않았다.
- EKS 전용 KMS키는 AWS의 예약 삭제 상태다: [{"arn": "arn:aws:kms:ap-northeast-2:764428911194:key/8b896f34-957a-4c46-8bab-d037fd3d1d62", "status": "PendingDeletion", "deletion_at_kst": "2026-12-13 00:55 KST"}]. RDS 스냅샷 KMS키와 다르다. 예약 기간을 우회하지 않는다.
- S3 backend/state/버전, 소스·Git 이력·App 키·TLS/토큰 원본·조사 원문·OCI 이미지 사본은 보존한다. 보존 정책에 따른 DNS·스냅샷·S3 보관 대상은 남아 있다.

## state 복구 경위

Primary 삭제 요청 후 관리자 토큰이 만료돼 대기 조회·state 저장·잠금 해제가 실패했다. 오류state serial131과 마지막 원격130을 보존하고, 재인증 후 실제 DB부재/최종 스냅샷available을 먼저 확인했다. 잠금의 소유자/호스트·시각·경로·ID와 Terraform실행0개를 대조해 이 작업의 잠금만 해제했다. 동일lineage의 오류state를 **강제 덮어쓰기 없이 정상push**했고, 이미 삭제된 DB를 리소스변경0개인 새 계획으로 정합화했다(serial133). 이후 운영 잔여100개 계획을 별도로 검증·적용했다. 실패한 옛 계획을 반복 적용하지 않았다.

처음 RDS 보호 해제 시 저장 계획의 ephemeral 비밀번호 입력 누락은 쓰기 전에 거절됐고 보호된 기존 입력을 환경으로 연결해 1건 계획만 재검증·적용했다. 원문 비밀번호는 로그나 문서에 출력하지 않았다. 정리 사본의 RDS 삭제 보호 설정도 원래 기본값으로 복원했다.

## 재설치 자료와 다음 작업

작업 원본: `/Users/jounghyeon/.local/share/url-shortener-ai-ops/cleanup-20261112-after-ai-split`. `final-verification.json`, `state-final-verification.json`, `state-recovery/`, `CHECKSUMS.json`, `images.oci/`, `volumes/`, `retained-log-group-*.jsonl.gz`, `retained-rds-os-metrics-*.jsonl.gz`, `handoff/`에 검증/복구/이미지/조사 자료를 둔다. `images.oci`의 현재 이미지 blob40개·108,718,513바이트는 SHA256 검증했다.

기존 `continuation-20261112/`, `repository-separation-20261112/`, `install-20261112/`와 `~/.local/share/url-shortener-install/credentials/`도 유지한다. 기존 체크포인트 검증·별도 AI Git bundle·원본 자격12개의0600 권한 확인을 통과했다. 옛 state/오류state/plan·UID·토큰·승인은 역사 자료이며 새 설치에 적용하지 않는다. 정상적으로 비어 있는 현재 backend를 사용하고 새 계획을 만든다.

AI 코드는 `/Users/jounghyeon/dev/url-shortener-ai-ops`에서 관리한다. **인프라 없이 C04 로컬 검증과 PR1 미병합 변경 확인부터** 재개한다. 실환경 C04/C05 완료는 아직 아니다. 재설치 때 위 새 최종 RDS 스냅샷·새 비밀번호 회전·현재 이미지/권한을 검증하고, 새 PVC/UID/endpoint/CI binding·worker 로드·실행 연결을 다시 준비한다. 동결한 workflow는 그 이후 재활성화한다. 새 장애 실행 승인과 새 복구 PR의 정확한 head 사람 리뷰는 별도다.

[전체 정리 절차](infrastructure-cleanup.md)·[자료 보존](ai-ops-continuation-2026-11-12.md)·[AI 저장소 분리](ai-repository-separation-2026-11-12.md). 다음 추천: **GPT-6 SOL · High**.
