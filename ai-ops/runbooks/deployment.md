# 경보·배포 사건·GitOps 복구 판단 런북

- document_id: `deployment`
- version: `1`
- 확인일: 2026-09-27
- 코드 기준: `e4c51585bbc449f444527ed3c0614aac4b4b5700`
- 근거: [경보 rule](../../url-shortener/url-shortener-chart/templates/prometheusrule.yaml), [Alertmanager](../../cluster-addons/prometheus/values.yaml), [CI/CD](../../.github/workflows/ci-cd.yml), [배포 검증 스크립트](../../.github/scripts/verify-gitops-deployment.sh), [Argo Application](../../url-shortener/k8s/argocd/application.yaml), [Deployment](../../url-shortener/url-shortener-chart/templates/deployment.yaml), [ServiceMonitor](../../url-shortener/url-shortener-chart/templates/servicemonitor.yaml).

## 1. 대상과 경보의 의미

코드의 운영 Argo Application은 `url-shortener`, namespace는 `url-shortener`다. 이 release 이름으로 렌더링하면 workload는 `url-shortener-api`, service는 `url-shortener-svc`다. 실제 연결 대상과 cluster ID는 확인하지 않았으므로 자동 등록된 라이브 대상이라고 취급하지 않는다.

경보 rule은 namespace/service로 조회를 필터링하지만 집계 후 라벨을 제거한다. rule labels에는 severity만 명시되어 있다. DB 경보는 operation, API 경보는 route를 보존하지만 namespace/service/workload 식별을 보장하지 않는다. 객체 metadata의 label은 개별 alert label과 구분한다. external labels와 실제 webhook payload도 아직 확인하지 않았다. AI 수신 전에 명시적 대상 라벨 또는 검증된 매핑과 target registry가 필요하다.

다음은 모두 최근 5분 rate·quantile을 사용하는 선언된 조건이다. 실제 firing 증거는 별도다.

| 경보 | 조건·지속 시간 | 해석·제한 |
|---|---|---|
| HighErrorRate | health/ready 제외 5xx 비율 >5%, 전체 >1 req/s, 2분 | 사용자 오류 증가. 원인은 별도 증거 필요 |
| CacheHitRateLow | hit/(hit+miss) <50%, hit+miss >1/s, 5분 | 효율 경보. error/unavailable/bypass/consistency_primary는 이 분모에 없음 |
| CacheOperationErrorHigh | error+unavailable 비율 >5%, 전체 operation >1/s, 2분 | 미설정·초기 연결·조회 오류·JSON 오류 구분 필요 |
| DBQueryLatencyHigh | operation별 P99 >0.5초, count >1/s, 3분 | 애플리케이션 측정 구간이며 DB 서버 원인 확정 불가 |
| APIRequestLatencyHigh | health/ready 제외 route별 P95 >1초, count >1/s, 3분 | 사용자 경로 지연. DB/캐시/Pod 등 상관관계 조사 |
| Watchdog | vector(1), severity none | 하트비트 경로. 앱 장애 조사 trigger에서 제외 |

rule 평가 주기는 1분이고 chart의 serviceMonitor.enabled가 활성일 때 생성된다. scrape·평가·지속 시간 때문에 사건 시작과 알림 시각은 다를 수 있다. CacheHitRateLow의 or vector(0)은 시계열 처리 식일 뿐, 누락된 관측 자료를 정상 0으로 판정하는 근거가 아니다.

Alertmanager는 기본 Slack, Watchdog는 별도 heartbeat receiver로 라우팅한다. Watchdog의 repeat/group interval은 5분이고 send_resolved는 false다. Slack은 resolved를 전송한다. 실제 전달 여부는 확인하지 않았고 AI receiver는 현재 없다. heartbeat 수신은 앱·모델·전체 시스템 정상의 증명이 아니다.

## 2. 기존 배포와 자동 롤백

1. main push의 앱/Dockerfile/의존성 변경이 CI/CD를 실행한다. deploy concurrency는 같은 ref에서 직렬화하고 cancel-in-progress=false다.
2. 이전 `api.image.tag`를 기록한 뒤 lint·이미지 빌드/게시를 수행하고 새 SHA image tag를 운영 values에 커밋·push한다.
3. 검증 스크립트는 **정확한 GitOps 커밋 revision + Synced + Healthy**를 기다린 뒤 `/readyz` status=ready와 `/items` list 응답을 확인한다. 기본 600초 deadline을 두 단계가 공유한다.
4. 검증 성공 시 해당 image digest와 일치하는 release tag를 보존한다.
5. 검증 step 실패 시 이전 image tag로 values를 되돌려 커밋·push하고 동일 스크립트로 복구 revision을 검증한다. kubectl 직접 rollout undo를 수행하는 구조가 아니다.
6. 배포 검증이 실패했던 run은 복구 성공 후에도 마지막 step에서 실패로 남는다. 롤백 step 자체 실패도 가능하므로 마지막 안내 문구만으로 복구 성공을 판단하지 않는다.

이전 tag는 **변경 전 desired image**이며, 이번 run의 코드만으로 이전 tag가 검증된 정상 이미지라고 보장되지 않는다. 3차 해결에서는 과거 성공 기록·digest·검증 자료로 정상 후보를 확인해야 한다.

Argo는 main과 automated selfHeal/prune을 사용하고 HPA replicas 차이를 제외한다. 차트 prod ingress는 꺼져 있고 별도 Traefik routing Application을 사용한다. 예전 nginx 흐름이나 직접 kubectl 변경을 현행 복구 절차로 제공하지 않는다.

## 3. 사건 조사 순서

1. 허용된 대상·사건 시간과 source SHA, CI run ID, 실패 image/digest, GitOps 배포 SHA, 이전 tag, 롤백 SHA를 각각 확보한다. 서로 같은 값이라고 가정하지 않는다.
2. 실패 당시 Pod 상태·image pull event·정제 로그·Argo revision/health·검증 오류를 확인한다. ImagePullBackOff만으로 잘못된 tag를 확정하지 않고 이미지 존재·접근·인증 문제를 후보로 둔다.
3. 롤백 뒤 현재 revision·image·Pod·readyz·items·관련 경보를 별도로 확인한다.
4. 과거 실패 원인 후보와 현재 상태(affected/recovered/healthy/unknown)를 구분하여 설명한다.

| 증거 | 판단 |
|---|---|
| 실패 image 변경 + pull 이벤트 | 이미지 접근 문제 후보. 정확한 원인은 추가 자료 필요 |
| 실패 revision의 검증 오류 + 롤백 revision의 성공·사용자 응답 | 과거 배포 실패, 현재 복구 확인 |
| 현재 정상만 있고 당시 자료 없음 | 현재 관측 상태만 설명, 과거 원인 판단 유보 |
| CI 실패만 존재 | 배포 실패·롤백 성공/실패 구분 불가 |
| Healthy/Synced지만 목표 revision 불일치 | 목표 변경의 완료로 판단하지 않음 |

## 4. 증거 보존·대응·검증

현재 workflow는 검증 로그를 남기지만 실패 전후 Pod/event 및 구조화된 사건 artifact를 자동 수집·업로드하지 않는다. 이전 Pod가 사라지면 자료를 복원할 수 없으므로 후속 B단계에서 제한 시간 내 보존을 설계한다. 수집 실패 때문에 기존 복구를 지연하지 않는다.

조사 런북의 조치는 제안이다. 첫 C단계 실행 범위는 실습 대상에서 검증된 이전 정상 이미지로 values-only GitOps 변경이며, 정확한 diff/revision 승인과 별도 실행 identity가 필요하다. 기존 자동 롤백과 경쟁하지 않도록 현재 revision을 재검사한다.

복구 확인에는 목표 revision·사용자 경로·관련 경보와 관측 시각을 남긴다. PR 생성, image 갱신, CI 상태 하나만으로 해결을 선언하지 않는다. 삭제·생성·모든 API 동작의 정상은 현행 smoke 검증 범위 밖이다.
