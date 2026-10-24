# 2026-10-24 AWS 인프라 정리 실행 결과

상태: **2026-10-24 KST 프로젝트 AWS 실행 리소스 정리 완료**. 계정 `716174522908`, 리전 `ap-northeast-2`. 사용자의 AI 포함 전체 인프라 정리 요청에 따라 수행했다. Route53 공개 Hosted Zone·레코드, RDS 최종 스냅샷, Terraform S3 backend/state 객체와 이전 버전, GitHub 설정, 로컬 소스·개인 자격은 보존했다.

AI CI relay·worker를 중지하고 `ai-ci`, `ai-model`, `ai-ops-lab` 및 AI Helm·PVC·전용 StorageClass를 삭제했다. AI EBS `vol-040dd79a2cc0db861`과 `vol-0c324ee4548b95d3f`의 부재를 확인했다. Karpenter NodePool, 앱·라우팅·Traefik·모니터링·기타 Argo CD Application을 cascade 삭제하고 Redis·Argo CD·AWS Load Balancer Controller Helm release를 제거했다. 프로젝트 네임스페이스, 클러스터 소유 EBS, 현재 VPC의 LB·Target Group·NLB ENI·`k8s-` 보안 그룹이 0개인 것을 확인한 뒤 Terraform을 진행했다.

AI ECR에서 확인한 digest 3개를 삭제하고 별도 AI root 계획의 관리 리소스 7개(ECR·lifecycle·IAM 두 역할과 정책·EKS access entry)를 삭제했다. AI state의 관리 리소스는 0개다. 앱 ECR digest 3개도 삭제했다. production의 최초 삭제 계획은 추가·변경 없이 102개 삭제, Route53 변경 없음, RDS Primary 최종 스냅샷 생성으로 확인했다. 첫 적용은 RDS가 최종 스냅샷 생성 시점에 `available`이 아니어서 Primary 삭제만 실패했다. 당시 다른 리소스 삭제는 진행됐다. DB가 다시 `available`이고 삭제 보호가 `false`, Enhanced Monitoring 정책이 유지됨을 확인하고 새 계획의 잔여 11개만 재적용해 성공했다. 삭제 순서 보장을 위해 임시 작업 트리에서 RDS 모듈의 Enhanced Monitoring 정책 의존성을 지정했고, 실행 후 원본 코드로 되돌렸다.

최종 확인: production·AI 두 Terraform state의 관리 리소스 0개, EKS `urlshortener`·VPC `vpc-00b2a08b712358b40`·RDS Primary/Replica·앱/AI ECR 저장소·활성 NAT·NAT EIP·클러스터 EBS 부재. 이번 Primary 최종 스냅샷 `urlshortener-postgres-final-snapshot-urlshortener-postgres-6d2247e6`은 **available·100%**다. S3 버킷 `urlshortener-tfstate-716174522908-ap-northeast-2`와 두 state 객체가 남아 있다. EKS KMS 키 `6cc686bc-857b-404f-87d3-ce95965f905d`는 PendingDeletion이며 2026-11-23 20:17 KST 삭제 예정이다. Route53 공개 Zone `Z01710062CSM40Z2TLTGS` 및 `api`·`argocd.bidservice.store` A Alias는 남아 있고, 삭제된 이전 NLB를 계속 가리킨다. 재설치 때만 새 NLB로 갱신한다.

**보존 범위 차이:** 사전 안내에서는 로그 보존을 언급했지만 production Terraform 계획에 `/aws/eks/urlshortener/cluster` CloudWatch 로그 그룹이 포함됐고 이번 destroy에서 삭제됐다. 현재 해당 로그 그룹은 없다. 과거 로그 데이터는 state로 복원되지 않는다. 다음 정리에서 로그 보존이 필요하면 destroy 전에 해당 그룹을 내보내거나 Terraform 관리에서 분리한 뒤 계획을 다시 검사해야 한다. GitHub의 전용 Secret·변수와 로컬 파일은 이번 AWS 정리에서 변경하지 않았다.

이 기록은 삭제된 실습 PVC·큐·보고서·관측 데이터를 복원한다는 뜻이 아니다. 재설치 시 이미지를 새로 등록하고, 전용 자격을 재발급하며, 보존한 state와 Route53을 확인한다.
