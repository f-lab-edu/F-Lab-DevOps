# B-01~B-05 완료 조건 대조 — 2026-10-28 KST

목적: 원본 구현 계획의 `AI 작업 리스트` B-01~B-05 체크 항목을 **현재 코드·기록된 실측·이번 읽기 조회**로 나눠 판정한다. 원래 목록에는 2026-10-16~21 당시의 “미배포” 표현과 이후 사용자 결정 전의 practice 전제가 남아 있다. 그 표현을 지금의 미구현으로 단정하거나, 과거 실측을 현재 클러스터의 신규 시험으로 바꾸지 않는다. 원본 작업 리스트는 별도 checkout에서 관리 중이므로 이 감사에서는 수정하지 않았다.

| 단계 | 현재 판정 | 확인한 근거 | 아직 필요한 확인 |
|---|---|---|---|
| **B-01 실환경 계약·설계** | **설계·현재 production 대상 대조 완료** | 읽기 수집→정제→A 보고서와 경보/worker/최소 권한 경계를 설계했다. 사용자는 후속 작업에서 **현재 production 클러스터의 읽기 전용 조사**를 선택했고 [production 등록](../config/targets.production.json)과 [B-03 사전 점검](B3-cluster-preflight.md)에 반영했다. 16:40 KST 현재 등록 파일과 배포된 bindings, Service selector, Deployment→ReplicaSet→Pod 소유 관계, 서비스 경보 5개 Target labels, Argo source/destination이 일치했다. captured 원본은 외부 모델 전송이 거부되고 배포 runtime도 `llm_enabled=false`였다. | 원래 practice 전제는 production 읽기 연결 결정으로 대체됐다. 이번 대상 대조는 과거 CNI·TLS·패킷 시험을 새로 실행한 것이 아니다. 재설치 때 같은 대조와 설치 검증을 반복한다. |
| **B-02 실시간 증거 수집** | **원래 체크리스트 제한 범위 충족** | [production 일반 수집](B2-live-validation.md)의 UTC 저장/KST 재조회와 부분 결과를 확인했다. [기존 자료 시각·S10 대조](B2-cross-source-time-audit-20261028.md)에서 13:22 KST 실습 사건의 Event·허용 로그·지표·현재 Deployment 시각을 대조했다. **별도** 13:12 KST S10은 원본 GitHub artifact의 실패→자동 원복 3단계, 같은 worker 입력·CI/Argo/Event/Deployment 증거·규칙 보고서, UTC 원본/수집 시각·누락 이유를 확인했다. `+09:00` 변환과 모호한/미래 시각 거부는 집중 시험 4개가 통과했다. | 두 사례를 한 사건으로 합치지 않는다. S10 로그·지표는 누락이고 실제 offset은 모두 `Z`; production의 모든 출처 동시 가용성·시계 정확도·서비스 복구·저장 Event 원본 UID 재검증은 입증하지 않았다. 현재 Deployment 조회 시각을 배포 시각으로 쓰지 않는다. |
| **B-03 권한·배포·자체 관측** | **현재 수동 Helm 운영 범위 완료** | [진단 배포](B3-diagnostics-deployment.md)와 [자체 관측/복구](B3-observability-and-recovery.md)에 읽기 RBAC, egress 제한, 암호화 PVC/EBS, 권한 거부·통신·토큰 교체·중지/복구 실측이 있다. [배포 소유권 판정](B3-deployment-ownership-20261028.md)에서 현재 Helm deployed·StatefulSet Helm 표시·이미지 digest 일치, Argo Application 부재와 실행 자원/모델 비활성·Secret 참조·NetworkPolicy를 대조했다. | 수동 Helm이 B-03 배포·정리의 단일 소유자다. [Argo Application](../../cluster-addons/ai-ops/application.yaml)은 비활성 초안이고 GitOps 이관은 별도 작업이다. 이번 읽기 조회는 과거 권한/패킷 실측이나 ECR 스캔을 현재 재설치에서 다시 실행한 결과가 아니므로 설치마다 이미지·권한·비용 경계를 재검토한다. |
| **B-04 사건 큐·복구** | **계획 범위 완료** | [worker 실측](B4-worker-deployment.md)에서 같은 PVC를 유지한 Pod 강제 교체 후 checkpoint 재사용, 중복 키 보존, 재시도·부분 보고서를 확인했다. [예약 정리](B4-retention-schedule.md)는 합성 만료 자료 삭제와 미만료·운영 자료 보존을 검증했다. 이번 조회에서 `ai-ci` worker·relay도 각각 Ready 1/1이었다. | 실제 운영 자료의 7일 만료 삭제는 당시 기한 미도래였으므로 합성 만료 검증과 구분한다. 노드/AZ 전체 장애·모든 종류의 외부 API 실패 복구는 범위 밖이다. |
| **B-05 경보 연동** | **경보·Slack·Watchdog 수신 경로 확인 완료** | [경보 접수](B5-alert-ingestion.md)는 격리 발생/해제→큐→부분 보고서를 실측했다. [Slack 실패 경로](B5-slack-delivery.md)는 AI 중지·인증 401 중 기존 Slack 성공을 확인했다. [Watchdog 자연 전달](B5-watchdog-delivery-20261028.md)은 현재 active Watchdog→heartbeat receiver와 웹훅 요청/알림 22→23, 실패 0을 확인했다. 이후 동일 Check의 **17:06 KST 수신 OK·현재 Up**을 Healthchecks 화면에서 확인했다. | 최근 실제 수신은 약 10분 간격이며 소스의 5분 설정과 차이가 있다. 현재 수신 성공으로 장기 가용성이나 5분 정시 수신까지 입증하지 않는다. 인증서 회전·장기 tombstone 보관과 CNI standard 시작 구간은 운영 후속이다. |

## 현재 배포 조회의 범위

2026-10-28 15:54 KST에 AWS에서 현재 EKS endpoint를 다시 확인하고 **임시 kubeconfig**로 리소스 이름·준비 수·PVC 상태만 읽었다. 기본 kubeconfig와 클러스터 설정은 변경하지 않았다. `ai-ops/ai-ops` StatefulSet은 1/1, `data-ai-ops-0` PVC는 Bound였다. `ai-ops`에는 deny·metrics ingress·read egress·webhook ingress 정책과 headless·metrics·webhook Service가 있었다. `ai-ci/ci-worker`와 `ci-relay`는 각각 1/1이었다. 이 조회는 알림 실제 전달, 수집 내용, Secret 값, 이미지 최신 스캔을 다시 시험한 것이 아니다.

## 현재 production 대상 재대조 — 16:40 KST

현재 API에서 Namespace·Deployment·Service UID의 존재와 Deployment→ReplicaSet→Pod controller UID 연결을 확인했다. 등록 파일과 `ai-ops-config`의 binding이 같고, Deployment·Service 선택자와 두 소유 Pod의 label·`api` 컨테이너도 일치했다. 서비스 경보 다섯 개의 `cluster_id/namespace/workload/service/environment/ai_ops`가 등록 Target과 맞고 Watchdog에는 AI 대상 label이 없었다. Argo의 저장소·경로·대상 namespace/server가 등록값과 같았으며 **Ready 2개, Synced/Healthy**였다. UID·설정 원문·자격은 문서에 복사하지 않았다. 이 확인은 관측 시점의 대상 연결이며 전체 서비스 장애 부재를 판정하지 않는다.

## 다음 우선순위와 C 진입

1. **B-02**: 기존 S10의 원본 GitHub artifact와 저장 worker 보고서를 연결하고, 별도 수집 사례의 출처별 시각을 재검증해 원래 체크리스트의 남은 항목을 제한 범위에서 마쳤다. 실습 실패·원복을 production 서비스 복구로 표현하지 않는다.
2. **B-03/B-05**: B-03의 현재 단일 소유자는 수동 Helm이며 Argo 이관은 별도 작업이다. B-05의 Watchdog는 Healthchecks 동일 Check의 실제 수신·현재 Up까지 확인했다. 약 10분의 실제 수신 간격과 소스의 5분 설정 차이는 운영 후속으로 남겼다.
3. [B-06 최신 판정](B6-completion-gate-20261028.md)은 중지 후 장부의 보고서 1건 보존·worker 입력 결합까지 확인해 **격리 실습 범위의 필수 종료 조건 충족**으로 갱신했다. B-01~B-06의 **합의된 읽기 조사·격리 실습 범위 종료 조건은 충족**했다. 이후 [C-01 설계](C1-approval-recovery-design.md)와 [C-02 로컬 첫 구현](C2-local-request-and-proposal.md)을 작성했다. C-02의 실제 사건 조회 연결과 C-03~C-05 승인·변경 기능은 아직 없다. 현재 AI에게 쓰기나 복구 권한이 생긴 것은 아니다.

원래 B-02 목록은 출처별 시각과 실패/원복 자료 검증을 요구한다. production에서 모든 장애를 다시 만들거나, 실제 출처가 모두 UTC인데도 다른 offset의 실측 자료를 새로 만들 것을 추가 필수 조건으로 정하지 않는다. 저장 자료의 원본 시각·수집 시각·정규화와 누락 이유, 기존 CI 실패/원복 artifact의 연결로 판정했다. 독립 의미 검토·장기 가용성·실제 24시간 대기·유료 오류 반복도 이번 종료 필수 조건에 추가하지 않는다. 이 작업 브랜치의 문서·시험 변경은 아직 커밋 및 새 원격 CI 검증 전이다. 따라서 **기능 범위의 종료 판정과 변경분의 배포 준비 완료 판정은 구분한다.**

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
