# B-05 경보 연동 — 구현·배포·검증

## 2026-10-20 실제 연결·검증 현황

[PR #44](https://github.com/f-lab-edu/F-Lab-DevOps/pull/44)는 Python 238건·이미지·앱·Helm·Terraform CI 통과 후 main `4da4add26f3ec4614b128cd1155f58582c811e29`에 병합했다. 아래 237건은 기존 로컬 checkout의 최초 기록이며, main 기반 회귀 시험은 238건이다.

전용 인증 Secret `ai-ops/ai-ops-webhook-identity`와 `monitoring/ai-ops-alertmanager-auth`를 준비했다. 인증값·개인키는 Git과 실행 출력에 기록하지 않았다. 서버 인증서 만료는 **2027-01-18 17:40:42 KST**이며 만료 전에 CA·서버 인증서·클라이언트 신뢰 설정과 수신기 재시작을 함께 계획해야 한다. Secret 생성만으로 수신기가 실행되거나 Alertmanager에 인증 파일이 마운트된 것은 아니다.

실제 Alertmanager 0.32.0의 `amtool config routes test`에 준비한 설정을 전달해 Watchdog 단독 heartbeat, AI 대상 경보의 AI+Slack, 일반 경보의 Slack 분기를 확인했다. 실행 중인 설정은 바꾸지 않았고 실제 Slack·heartbeat 시험 메시지도 보내지 않았다. 이는 분기 판정 검증이며 TLS 통신·전달 성공을 대신하지 않는다.

[이미지 등록 실행 39393659389](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39393659389)은 사용자 승인 후 성공했다. source `4da4add26f3ec4614b128cd1155f58582c811e29`, digest `sha256:c96654ccb9517e55161a1a0849d67c9de42638cfbaac77f7b07c8d2dc8c8d5fc`의 artifact·ECR manifest와 COMPLETE/지적 0건을 검증했다. Helm revision 4에 worker와 TLS 수신기 2개 컨테이너를 배포해 Ready를 확인했다.

실제 인증 시험에서 호스트가 생성한 EC 명시 매개변수 인증서의 Python TLS 호환 문제가 발견돼 **RSA CA/서버 인증서로 교체**하고 재시작했다. 검증을 우회하지 않았다. localhost port-forward로 실제 CA·호스트명 검증, 미신뢰 CA 차단, 잘못된 token 401, 신규 202, 중복·해제·늦은 firing 차단을 확인했다. 내부 합성 경보 1건은 규칙 보고서 `partial`과 결과 파일 5개를 생성했고 worker 시도는 1회다. 이는 자료가 완전하지 않음을 명시한 결과이며 원인 확정·복구 성공이 아니다. 기존 조사 2건과 보고서 해시·동일 PVC는 유지됐다. 이 시험은 NetworkPolicy 발신자 검증이나 실제 Alertmanager 발신을 대신하지 않는다.

[PR #45](https://github.com/f-lab-edu/F-Lab-DevOps/pull/45)는 Python **239건**·전체 CI 통과 후 main에 반영했고, 운영 Alertmanager 분기와 앱 경보 labels를 활성화했다. Argo CD의 모니터링·앱은 Synced/Healthy, 앱은 Ready 2개다. 실제 실행 중인 Alertmanager의 amtool로 AI+Slack과 Watchdog 전용 분기를 확인했다. 서비스 경보 5개의 등록 대상 labels와 Watchdog 제외도 실제 리소스로 확인했다.

실제 Alertmanager Pod에서 amtool의 CA·호스트명 검증 TLS 요청이 405에 도달했고, Prometheus Pod의 수신 포트 접근은 timeout으로 차단됐다. monitoring에는 발신 egress NetworkPolicy가 없으므로 후자는 수신 ingress 경계의 실제 차단 결과다. 405는 조회 API 미제공 응답이며 인증된 POST 접수를 증명하는 응답이 아니다.

같은 Alertmanager Pod 안에서 **동일 바이너리의 AI 전용 격리 프로세스**를 실행했다. 별도 loopback API·임시 저장소와 실제 CA/Bearer 파일을 사용해 발생·해제 webhook 2회, 실패 0회를 확인했다. 이 경보도 worker 1회·partial 규칙 보고서·결과 파일 5개와 해제 장부로 이어졌다. 운영 Alertmanager 본체에 시험 경보를 주입한 것은 아니며, 임시 프로세스와 폴더는 종료 시 정리했다. 기존 조사 2건의 보고서 해시와 동일 PVC/EBS는 보존됐고 내부 시험 조사만 2건 추가됐다.

**실제 연결과 내부 전달은 검증했지만 B-05 전체 완료는 아니다.** 외부 시험 메시지는 전송하지 않았으며 AI 중지·인증 실패 중 운영 Alertmanager→실제 Slack 전달 시험은 별도 승인 범위에서 남아 있다. 기존 Watchdog의 인위적 시험 전송도 하지 않았다. 외부 모델 호출은 0회다. 재설치 시 모니터링 전에 인증 Secret을 준비하고 worker overlay 뒤 `b5-values.receiver.yaml`을 적용한다. [Secret 설치·회전 절차](B5-webhook-identity.md)를 따른다.

최초 로컬 검증 기록: 경보 접수·TLS 수신기·배포 정의·기존 알림 분기 준비를 구현하고 당시 checkout의 Python 237건과 Helm 정적 검사를 통과했다. 당시 운영 Secret·수신기·분기는 미적용이었으며, 최신 준비 상태는 위 기록을 따른다. B-05 전체 완료가 아니다.

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

앱 chart의 `monitoring.aiOps.enabled`도 기본 false다. 켜려면 clusterId와 practice/production을 명시해야 하며 서비스 경보 다섯 개에 Target labels를 추가한다. Watchdog·PromQL·severity·annotations는 바꾸지 않는다. 최초 로컬 구현 당시에는 실제 운영값을 수정하지 않았다. 이후 PR #45에서 위 구조대로 Alertmanager 분기와 production 경보 labels를 활성화했다.

## 사용·검토 예

아래 초안 생성 명령은 AI 분기가 아직 없는 최초 values를 전제로 하는 초기 구현 예다. 이미 AI 분기가 활성화된 현재 main에서는 기존 분기 덮어쓰기를 막기 위해 거부한다. 현재 구성의 재검토는 `check_deployment.py`가 호출하는 `verify_routing` 검사로 수행한다. 출력은 등록된 개인 저장 폴더의 새 JSON 경로여야 한다. 실제 source는 고정된 저장소 values이며 arbitrary 입력 파일을 받지 않는다. 이 도구는 로컬 개발용 Ruby YAML 파서를 사용하고 이미지 의존성을 추가하지 않는다.

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

최초 로컬 checkout의 전체 Python **237건**(신규 경보 21건: 접수 13·실제 TLS 6·분기 보존 2)을 통과했다. 동시 전송·묶음의 용량 실패 롤백, 해제 선도착·재발·보관 삭제 후 중복, 시각/원문 미저장·운영자 시각 조회·장부 포화를 확인했다. 실제 localhost TLS 소켓으로 파일 인증·교체·잘못된 CA·크기/빈도/형식·저장 실패 응답을 대조했다. Helm은 수신기 비활성 기본값·Secret 참조·정확한 Alertmanager ingress·토큰 미마운트와 위험 변경 거부, 앱 경보 5개 Target·Watchdog 불변을 확인했다. 원격 main보다 이 checkout의 기존 회귀 시험이 1개 적으므로 원격 CI 건수와 혼동하지 않는다.

후속 연결에서 main 기반 원격 CI·이미지 등록, TLS/인증 Secret·내부 수신기 배포, 실제 대상 labels·Alertmanager 분기를 적용했고 실제 바이너리의 격리 발생·해제와 큐→수집→보고서 전달을 확인했다. 남은 것은 사용자 승인 범위의 실제 외부 알림 및 AI 중지·TLS/auth 실패 중 사람 알림 전달 검증이다. 외부 AI 전송·운영 변경은 별도 범위다. 실제 경보를 인위적으로 전송해 외부 Slack/heartbeat에 메시지를 보내는 시험은 사용자 승인된 시험 범위에서만 수행한다.
