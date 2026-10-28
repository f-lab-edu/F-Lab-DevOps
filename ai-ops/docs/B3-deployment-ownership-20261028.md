# B-03 AI 배포 소유권과 완료 기준 — 2026-10-28 KST

## 현재 결정

`ai-ops` 차트의 **현재 배포·업그레이드·정리 경로는 수동 Helm 릴리스 `ai-ops`**다. StatefulSet에서도 Helm 소유 표시를 확인했다. [클러스터 설치·정리 절차](../../docs/runbooks/ai-ops-cluster-lifecycle.md)는 이 방식을 사용한다. [Argo CD Application 초안](../../cluster-addons/ai-ops/application.yaml)은 `enabled=false`, 검토 SHA placeholder, 자동 동기화 없음으로 남아 있으며 **현재 클러스터에 적용하지 않는다**. 같은 리소스를 Helm과 Argo CD에 동시에 맡기지 않는다.

원래 작업 목록의 “Helm/Argo CD·버전·Secret·리소스·egress·비용 설정”은 **현재 선택한 수동 Helm 운영 경로의 구성·검증**으로 판정한다. Argo CD 이관 자체는 별도 변경이며, 현재 구현이 GitOps 운영까지 검증됐다고 표시하지 않는다. B-03의 다른 권한·관측 실측과 B단계 전체 완료 여부는 [단계 대조](B-stage-readiness-audit-20261028.md)에서 따로 판단한다.

## 현재 클러스터의 읽기 전용 대조

2026-10-28 KST에 AWS의 현재 EKS 주소로 임시 kubeconfig를 만들고 상태만 읽었다. 기본 kubeconfig와 클러스터 설정은 변경하지 않았다.

| 확인 항목 | 결과와 의미 |
|---|---|
| Helm 릴리스 | `ai-ops/ai-ops`가 **deployed**, revision **1**, chart **0.1.0**. 이번 재설치의 revision이며 10월 19일 과거 revision 4와 합산하지 않는다. |
| StatefulSet 소유 표시 | `meta.helm.sh/release-name=ai-ops`, release namespace `ai-ops`, `app.kubernetes.io/managed-by=Helm`; 원하는 복제수 1·Ready 1. StatefulSet의 PVC template 이름은 `data`다. |
| Argo CD | `argocd/ai-ops` Application 조회 결과 없음. 소스의 초안 파일이 실제 Argo 소유를 뜻하지 않는다. |
| 이미지 버전 | Helm 값의 고정 digest와 StatefulSet 컨테이너 이미지 digest가 **일치**했다. chart `appVersion=0.1.0`은 실행 코드의 고유 버전이 아니므로 이미지 등록 artifact의 source SHA·digest·스캔 결과를 설치 때 별도로 대조한다. 이번 조회로 ECR 재스캔을 수행한 것은 아니다. |
| 실행 설정 | Helm 최종 값은 `enabled=true`, `mode=worker`, `llm_enabled=false`, retention·receiver 활성. CPU 요청 250m/상한 1, 메모리 요청 256Mi/상한 1Gi, API `/32` 세 개, storageClass 설정을 확인했다. `per_incident_budget_usd=0.10`, `daily_budget_usd=1`은 runtime에 있지만 이 worker의 외부 모델 기능은 꺼져 있어 유료 호출 허용으로 해석하지 않는다. B-06 모델 캠페인 예산과도 별개다. |
| Secret·통신 | 수신기 값은 Secret **이름 참조**이며 `ai-ops-webhook-identity` 존재만 확인했다. Secret 값은 읽지 않았다. namespace에 deny, metrics ingress, read egress, webhook ingress NetworkPolicy 네 개가 있었다. 정책의 실제 패킷 허용/차단은 이번에 다시 시험하지 않았다. |

이 대조는 릴리스·설정·준비 상태의 현재 단면이다. [과거 배포/권한 실측](B3-diagnostics-deployment.md)과 [자체 관측/복구 실측](B3-observability-and-recovery.md)을 현재 재설치의 새 실패 주입 결과로 취급하지 않는다.

## 설치·업그레이드·정리의 단일 소유자 규칙

1. 설치 전 `argocd/ai-ops` Application의 **부재**, 기존 Helm 릴리스 상태, `ai-ops` 리소스의 Helm 소유 표시를 확인한다. Argo Application이 있거나 동일 리소스에 다른 소유 표시가 있으면 `helm upgrade`를 중단하고 소유권을 먼저 정리한다.
2. 검토한 main 소스·이미지 등록 artifact·ECR digest/스캔, 현재 클러스터의 Secret **참조 이름**, RBAC·NetworkPolicy·자원 한도·`llm_enabled=false`를 함께 검토한다. 비공개 현재 값과 worker/receiver overlay를 사용해 Helm template·server dry-run 후 **같은 릴리스**를 upgrade한다. chart 기본값은 비활성이므로 기본값만으로 설치 완료라 표시하지 않는다.
3. 설치 후 Helm `deployed`, StatefulSet Ready, PVC Bound·EBS 암호화, 이미지 digest 일치, worker probe·Prometheus와 실제 권한/통신 경계를 해당 설치 절차대로 확인한다. Ready 하나만으로 B-03 전체 완료나 B-06 모델 활성으로 기록하지 않는다.
4. 전체 정리에서는 AI 작업을 중지하고 **Helm uninstall → 잔여 PVC/PV/EBS 확인·삭제 → namespace/StorageClass → AI Terraform root** 순서를 따른다. Argo가 없다는 조건을 다시 확인해 삭제한 리소스를 다른 제어기가 재생성하지 않도록 한다.

Argo CD로 옮기려면 별도 설계와 검증이 필요하다. 먼저 Helm revision·이미지 digest·차트 최종 값·Secret 참조·PVC/PV 소유권을 고정하고 새 Argo manifest의 rendered resource가 현재 구성과 일치하는지 격리 환경에서 확인해야 한다. 그 뒤 **한 제어기만 쓰도록 이관 순서와 중단·롤백 방법**을 승인받아 진행한다. `FailOnSharedResource=true`만으로 Helm과 Argo의 중복 관리를 막는다고 가정하지 않는다. 현재 운영 리소스에 Argo Application을 바로 적용하거나 자동 동기화를 켜는 것은 이 문서의 완료 범위가 아니다.

이번 작업에서는 조회와 문서 변경만 수행했다. Helm upgrade·Argo 적용·모델 호출은 없었다. 다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
