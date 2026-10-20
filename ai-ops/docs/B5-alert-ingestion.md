# B-05 경보 연동 — 첫 구현 묶음

현재 상태: **경보 접수·TLS 수신기·배포 정의·기존 알림 분기 준비의 로컬 구현**이다. 전체 Python 237건과 Helm 정적 검사를 통과했다. 운영 클러스터의 수신기·TLS Secret·Alertmanager 분기와 앱 AI label은 아직 활성화하지 않았다. B-05 전체 완료가 아니다.

## 구현한 기능과 동작 흐름

기존 수동 접수 대신 인증된 Alertmanager 경보를 조사 큐에 넣는 경로다. **TLS와 Bearer 파일 인증 → 본문·개별 경보 검사 → 등록 대상·허용 경보 대조 → episode 중복/해제 기록과 큐를 원자 저장 → HTTP 202 → 기존 worker의 읽기 수집·고정 증거·규칙 보고서 → 운영자 CLI 조회** 순서다. 수신기는 조사가 끝날 때까지 HTTP 연결을 붙잡지 않는다.

이번 코드로 Kubernetes나 외부 모델의 권한을 늘리지 않는다. 경보의 설명문·URL·프롬프트·selector·PromQL을 실행 입력으로 사용하지 않는다. 원시 webhook 본문·annotations·generatorURL·externalURL은 저장하거나 방문하지 않는다. 질문은 허용 경보 이름을 포함한 고정 문장으로 만든다.

## 파일별 역할

| 파일 | 역할 |
|---|---|
| `src/ai_ops/alerts.py` | 개별 경보 대상·시각·차원 검사, 원자 접수, 중복/해제 장부와 운영자 시각 조회 |
| `src/ai_ops/alert_receiver.py` | HTTPS POST `/v1/alerts`, 인증 파일 교체·본문 크기·연결 시간·접수 빈도 제한 |
| `src/ai_ops/alert_routing.py` | 기존 Slack·Watchdog를 보존하는 검토용 Alertmanager 분기 복사본 생성 |
| `src/ai_ops/job_queue.py` | 수동 접수와 경보 접수가 같은 트랜잭션 경로를 사용하도록 접수 내부 함수 분리 |
| `chart/templates/receiver.yaml` | 내부 ClusterIP Service·Alertmanager namespace와 Pod labels의 TLS ingress 제한 |
| `chart/templates/statefulset.yaml` | 명시적 활성화 시 같은 Pod의 별도 수신 컨테이너와 Secret 파일 마운트 |
| `config/b5-values.receiver.example.yaml` | 비활성 수신기 설정 초안. 실제 Secret·labels·검증 이미지 필요 |
| `scripts/prepare_alertmanager.py` | 기존 values를 읽어 보호 폴더에 검토용 분기 초안만 생성 |
| `scripts/check_alert_routing.py` | 실제 앱 Helm 경보 labels와 Watchdog 불변 검사. 기존 배포 정적 검사에서 호출 |
| `../url-shortener/url-shortener-chart/templates/_aiops.tpl` | 서비스 경보 다섯 개에 명시적 대상 labels를 추가하는 기본 비활성 helper |

## 대상·시간·중복 계약

`alerts[]`의 개별 labels에 있는 `cluster_id/namespace/workload/service/environment`가 하나의 등록 Target과 모두 같아야 한다. commonLabels나 groupLabels에서 빠진 대상을 채우지 않는다. 파일 인증에 고정한 cluster ID도 대조한다. `ai_ops=enabled`와 허용 alertname을 함께 요구한다. `HighErrorRate`, `CacheHitRateLow`, `CacheOperationErrorHigh`, `DBQueryLatencyHigh`, `APIRequestLatencyHigh`만 허용하고 Watchdog는 제외한다. route/operation을 보냈다면 등록된 차원이어야 한다. 첫 자동 장애 시험은 기존 계획대로 대표 경보부터 선별한다.

중복 키는 인증 cluster ID + 16자리 경보 fingerprint + startsAt의 SHA-256이다. 시작 시각은 RFC3339 UTC로 정규화하면서 최대 나노초 정밀도를 보존한다. KST 입력도 동일한 순간의 UTC 키로 통합된다. fingerprint는 권한 증명이 아니며 전체 Target과 label HMAC을 별도로 검사한다. 같은 키에 다른 label이 오면 거부한다.

미래 시작/종료는 현재보다 최대 5분까지만 허용하고 resolved의 endsAt은 startsAt보다 빠를 수 없다. firing의 endsAt은 Alertmanager의 미래 만료 예정 또는 zero time일 수 있어 해제 판정에 사용하지 않는다. 오래 지속된 경보 시작 시각을 수집 구간으로 사용하지 않으며 worker의 현재 수집 예산과 구간을 유지한다.

- `firing` 새 episode: 장부와 기존 jobs/episodes를 **한 SQLite 트랜잭션**으로 commit하고 202를 반환한다.
- 반복 firing: 기존 job을 재사용하며 새 수집·attempt·모델 호출을 만들지 않는다. 보고서가 7일 뒤 삭제돼도 같은 episode는 재조사하지 않는다.
- `resolved`: episode의 해제 시각·상태만 갱신한다. 진행 중 조사를 임의로 취소하거나 서비스 복구 성공·원인 해결 완료로 표시하지 않는다.
- resolved가 먼저 오거나 같은 묶음에 firing/resolved가 있으면 해제 기록부터 반영한다. 뒤늦은 firing은 새 job을 만들지 않는다. 같은 fingerprint라도 startsAt이 바뀐 재발은 새 episode다.
- 미허용 대상/경보/차원은 `rejected` 건수로 남기고 해당 job을 만들지 않는다. 유효한 다른 경보는 접수한다. 잘못된 envelope·fingerprint·시각·잘린 전송은 400으로 거부하며 해당 묶음의 작업을 만들지 않는다.
- 큐 포화·DB 쓰기/commit 실패는 503이며, 묶음에서 일부만 접수한 뒤 성공으로 표시하지 않는다. 기존 수동 큐의 미완료 100개 한도를 함께 사용한다.

`alert_episodes`는 원문 대신 식별 HMAC/해시·고정 경보 이름·발생/해제/접수 UTC 시각·job 참조를 보존한다. 기존 v2 큐에 별도 표를 추가하고 기존 자료나 예약 장부를 초기화하지 않는다. replay를 막는 tombstone은 자동 삭제하지 않으며 최대 10,000행을 넘는 새 episode는 503으로 차단한다. 장기 보관/아카이브 정책은 운영 평가에서 별도로 정해야 한다. 해시·참조도 운영 메타데이터이므로 외부 공개 자료로 자동 취급하지 않는다.

## TLS·인증·접근 경계

기본 chart는 `receiver.enabled=false`다. 켰을 때 worker 모드, 기존 Secret 이름, 비어 있지 않은 Alertmanager Pod selector가 필요하며 잘못된 설정은 Helm schema가 거부한다. Secret에는 `token`, `tls.crt`, `tls.key`가 있어야 한다. 새 Secret을 만들거나 Git에 값/개인키를 기록하는 코드는 없다. 수신 포트는 내부 TLS 9443이며 공개 Ingress·LoadBalancer를 만들지 않는다.

인증값은 32~256자 전용 값이며 요청마다 파일을 다시 읽고 상수 시간 비교한다. 파일 교체 후 기존 token은 거부한다. 인증 파일이 없거나 권한/형식이 깨졌으면 503으로 실패한다. 잘못된 token은 401이다. TLS 인증서/키는 시작 때 읽으므로 회전 시 수신기 재시작과 클라이언트 CA 전환 절차가 필요하다. TLS 1.2 이상을 요구한다.

본문은 최대 64KiB·경보 32개이고 중복 JSON 키·비유한 수·truncatedAlerts를 거부한다. Content-Length 하나와 JSON 본문을 요구하며 chunked·압축·중복 인증 헤더는 거부한다. 연결은 2초 제한, 인증 후 분당 60회 초과는 429다. 오류 응답·고정 코드 로그에 원문·인증값·질문을 복제하지 않는다. 202는 영속 접수 결과이지 조사 완료가 아니다.

수신 컨테이너는 비루트·읽기 전용 루트·capability 제거를 사용하고 Kubernetes projected token을 마운트하지 않는다. worker에도 수신용 TLS/인증 Secret은 마운트하지 않는다. 같은 Pod의 네트워크와 PVC·UID를 공유하므로 서로 다른 신뢰 영역을 강하게 격리하는 구성은 아니다. Pod NetworkPolicy의 허용 egress는 공유한다. `pods/exec`·Secret API 읽기·운영 쓰기 권한은 추가하지 않는다.

HTTP 조회/관리 API는 제공하지 않는다. 수신 token으로 보고서를 읽을 수 없고, 운영자는 기존 Kubernetes 인증/exec와 CLI를 사용한다. readiness/liveness TCP 검사는 포트 수신만 확인하며 실제 TLS/auth·큐 쓰기·알림 전달 성공까지 보증하지 않는다. 이 항목은 실제 전달 시험으로 확인해야 한다.

## 기존 Slack·Watchdog 경로 보존

분기 준비 함수는 기존 Watchdog·Slack receiver·Secret·inhibit 규칙을 복사하고 아래 sibling 순서만 추가한다.

1. Watchdog → 기존 heartbeat receiver, 후속 분기로 진행하지 않음.
2. `ai_ops=enabled` → AI receiver, `continue=true`.
3. Slack catch-all → 기존 Slack receiver.

자식 route에 매칭됐다고 부모 기본 Slack에도 자동 전달된다고 가정하지 않는다. [Alertmanager 공식 route·webhook 계약](https://prometheus.io/docs/alerting/latest/configuration/)을 기준으로 명시적 후속 Slack 분기를 둔다. 준비한 AI webhook은 내부 HTTPS·전용 CA·Bearer credentials_file·리다이렉트 금지·send_resolved=true·timeout 5초를 사용한다. 기존 inhibition을 우회하지 않는다.

앱 chart의 `monitoring.aiOps.enabled`도 기본 false다. 켜려면 clusterId와 practice/production을 명시해야 하며 서비스 경보 다섯 개에 Target labels를 추가한다. Watchdog·PromQL·severity·annotations는 바꾸지 않는다. **현재 실제 `cluster-addons/prometheus/values.yaml`, production 활성값과 기존 알림 경로는 수정하지 않았다.**

## 사용·검토 예

배포 전 초안 생성만 수행한다. 출력은 등록된 개인 저장 폴더의 새 JSON 경로여야 한다. 실제 source는 고정된 저장소 values이며 arbitrary 입력 파일을 받지 않는다. 이 도구는 로컬 개발용 Ruby YAML 파서를 사용하고 이미지 의존성을 추가하지 않는다.

```sh
PYTHONPATH=ai-ops/src python ai-ops/scripts/prepare_alertmanager.py \
  --output "$HOME/.local/share/url-shortener-ai-ops/b5-alertmanager-values.json"
```

수신기를 배포한 이후 운영자가 경보 job을 확인할 때는 다음을 사용한다. 경보 발생·접수·worker 시도 시각은 UTC로 구분하며 기존 보고서의 사람 표시 시각은 KST다. job ID는 webhook 성공 응답에 노출하지 않으며 인증된 운영자 큐 조회에서 확인한다.

```sh
python -m ai_ops alert-show --store /data/incidents --job job-실제반환ID
# 반환된 incident_id/run_id는 기존 show 명령으로 확인한다.
```

## 검증과 남은 활성화 작업

현재 checkout의 전체 Python **237건**(신규 경보 21건: 접수 13·실제 TLS 6·분기 보존 2)을 통과했다. 동시 전송·묶음의 용량 실패 롤백, 해제 선도착·재발·보관 삭제 후 중복, 시각/원문 미저장·운영자 시각 조회·장부 포화를 확인했다. 실제 localhost TLS 소켓으로 파일 인증·교체·잘못된 CA·크기/빈도/형식·저장 실패 응답을 대조했다. Helm은 수신기 비활성 기본값·Secret 참조·정확한 Alertmanager ingress·토큰 미마운트와 위험 변경 거부, 앱 경보 5개 Target·Watchdog 불변을 확인했다. 원격 main보다 이 checkout의 기존 회귀 시험이 1개 적으므로 원격 CI 건수와 혼동하지 않는다.

다음 묶음은 main 기반 원격 CI와 이미지 등록, TLS/인증 Secret 준비·내부 수신기 배포, 실제 대상 labels·Alertmanager 분기 연결이다. 실제 경보의 AI 접수와 기존 Slack/Watchdog 전달, AI 중지·TLS/auth 실패 시 사람 알림 유지, 경보→큐→수집→보고서의 실제 end-to-end 검증이 남았다. 외부 AI 전송·운영 변경은 별도 범위다. 실제 경보를 인위적으로 전송해 외부 Slack/heartbeat에 메시지를 보내는 시험은 사용자 승인된 시험 범위에서만 수행한다.
