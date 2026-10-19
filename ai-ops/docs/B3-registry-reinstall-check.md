# B-03 AI 등록 인프라 재설치 확인

2026-10-19 KST 사용자의 AI 인프라 설치 요청에 따라 현재 상태를 확인했다. ECR과 IAM 역할의 생성 시각은 **2026-10-19 12:29 KST**였다. 이번 점검 전에 이미 재생성돼 있어 추가 apply나 중복 생성은 하지 않았다.

## 확인한 설치 상태

| 항목 | 확인 결과 |
|---|---|
| AWS 계정·리전 | `716174522908` / `ap-northeast-2` |
| AI ECR | `urlshortener-practice-ai-ops` 존재. IMMUTABLE·scan-on-push·AES256 일치 |
| lifecycle | 태그 없는 이미지만 1일 후 삭제 |
| 등록 역할 | `urlshortener-practice-ai-ops-image-role` 존재 |
| 신뢰 조건 | 기존 GitHub OIDC, audience `sts.amazonaws.com`, subject `repo:f-lab-edu/F-Lab-DevOps:environment:ai-ops-image` |
| 등록 권한 | AI ECR 전용 inline policy 하나. 추가 managed policy 없음 |
| 별도 backend | `urlshortener-tfstate-716174522908-ap-northeast-2`의 `urlshortener/ai-ops/practice/terraform.tfstate` |
| state 소유 | ECR·lifecycle·IAM role·inline policy 4개. 기존 OIDC는 data 조회만 함 |
| Terraform | 고정 provider로 init·validate 통과. 잠금을 사용한 plan 종료 코드 0, managed 4개 모두 no-op |
| GitHub 환경 | `ai-ops-image`의 main branch 제한·사용자 필수 승인 유지 |
| 환경 변수 | 계정·리전·ECR 이름·역할 ARN 네 값이 Terraform output과 일치 |
| ECR 이미지 | 0개 |
| EKS | `urlshortener` 클러스터가 목록에 존재. AI Pod 설치·준비 상태를 뜻하지 않음 |

이번에는 기존 production·Route53 설정을 변경하지 않았다. 실제 STS OIDC 인증이나 이미지 push를 실행한 결과가 아니며, 저장 계획 파일은 확인 후 삭제했다.

## 남은 설치 단계

[PR #32](https://github.com/f-lab-edu/F-Lab-DevOps/pull/32)는 아직 열려 있고 이미지 등록 workflow는 main에 없다. 등록 경로는 main과 사용자 환경 승인을 요구한다.

1. PR 변경 범위·최신 main과의 호환성을 검토하고 main에 반영한다.
2. 수동 workflow를 `publish=false`로 실행해 빌드·시험·artifact를 확인한다.
3. `publish=true` 실행 후 사용자가 GitHub 환경을 승인한다. OIDC 인증·동일 이미지 push·digest 대조를 확인한다.
4. 실제 대상·네트워크·StorageClass 값을 구성하고 B-03 diagnostics Pod를 배포·검증한다.

현재 완료 범위는 **AI 이미지 등록 인프라 복구 확인**이다. AI 에이전트의 클러스터 배포와 자동 조사 서비스 설치는 완료되지 않았다. 진단 Pod와 향후 worker의 차이는 [배포 안내](B3-deployment.md)를 따른다.
