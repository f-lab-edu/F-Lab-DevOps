# B-05 Watchdog 자연 하트비트 전달 확인 — 2026-10-28 KST

판정: **Alertmanager 발신 경로와 Healthchecks의 실제 Watchdog 수신을 확인했다.** 별도 합성 경보나 수동 ping 없이 자연 하트비트를 관측했다. 사용자 로그인 후 수신 Check의 URL이 로컬 보존 원본과 일치함을 메모리에서 비교했고, **2026-10-28 17:06 KST 수신 OK·현재 Up**을 확인했다. 이는 해당 알림 경로의 확인이며 전체 서비스 정상이나 장기 가용성의 판정은 아니다.

## 확인한 경로

1. 현재 EKS의 `monitoring`에서 Alertmanager Pod가 Running/Ready이고, `url-shortener-alerts` PrometheusRule과 `alertmanager-heartbeat-secret`의 `webhook-url` 키가 존재했다. 보존된 로컬 Watchdog URL 파일은 0600·비어 있지 않은 일반 파일이었다. 클러스터 Secret의 값과 로컬 보존 원본을 **메모리에서만 비교**해 일치함을 확인했다. 값·해시·경로의 비밀 부분은 출력하지 않았다.
2. 실제 Alertmanager API에서 Watchdog 경보가 `active`이고 수신자가 `heartbeat-receiver` 하나임을 확인했다. 같은 시점의 다른 활성 경보는 Slack 수신자만 사용했다. [실행 설정](../../cluster-addons/prometheus/values.yaml)은 Watchdog를 별도 heartbeat 수신자로 라우팅하며 그 수신자는 URL 파일을 읽어 HTTPS 웹훅을 보낸다.
3. 실행 중인 Alertmanager의 `/metrics`에서 `integration="webhook"`의 `alertmanager_notification_requests_total`과 `alertmanager_notifications_total`이 **각각 22→23**으로 증가했다. `alertmanager_notification_requests_failed_total`은 전후 **0**, 웹훅의 `alertmanager_notifications_failed_total` 각 사유도 **0**이었다. 증가 뒤 활성 수신자 목록에도 heartbeat 1개와 Slack만 있었다. Alertmanager 컨테이너의 재시작 횟수는 0이었고 시작 시각은 이번 관측 전이었다.

이 관측은 **Alertmanager 쪽에서 외부 웹훅 요청을 실패 없이 처리했다고 판정할 근거**다. 지표에는 receiver 이름이 아니라 integration만 있어 정확한 요청 한 건을 Healthchecks 계정의 수신 기록과 1:1로 연결하지는 못한다. 전후 활성 웹훅 수신자가 Watchdog뿐임을 확인했지만, 두 조회 사이에 잠깐 발생했다가 해제된 다른 웹훅 경보까지 지표만으로 배제할 수는 없다. 이 계수 조회 당시에는 Healthchecks 수신 화면을 확인하지 않았다. 이후 수신자 쪽 확인은 아래 후속 기록을 따른다.

기존 [Slack 실패 경로 실측](B5-slack-delivery.md)과 이번 Watchdog 관측은 범위가 다르다. AI 중단/401 중 Slack 성공은 당시의 합성 경보 시험이고, 이번에는 현재 설정의 자연 Watchdog 재전송을 읽었다. 어느 결과도 AI의 자동 원인 해결이나 모델 품질을 검증한 것은 아니다.

## 사용자 로그인 후 실제 수신 확인 — 17:08 KST경

최초에는 로그인 화면까지만 접근할 수 있었으나 사용자가 직접 로그인한 후 `url-shortener` Check를 읽었다. 화면의 ping URL과 보존 원본을 메모리에서 비교해 **동일 Check**임을 확인했다. URL·Check ID·이메일·원문 본문은 문서에 복사하지 않았다. 화면의 시간대는 `Asia/Seoul`로 선택해 한국 시간으로 읽었다.

| 수신자 화면의 항목 | 확인 결과 |
|---|---|
| 현재 상태 | **Up**, 조회 당시 마지막 수신 약 2분 전 |
| 최근 수신 | **2026-10-28 17:06 KST, OK** |
| 앞선 수신 | **16:56·16:46·16:36·16:26 KST**, 모두 OK. 12:46 KST 이후의 반복 수신도 화면에 보존돼 있었다. |
| 발신 형식 | HTTPS POST, User-Agent **Alertmanager/0.32.0**. 발신 IP와 2,148바이트 원문 본문은 문서에 복사하지 않음 |
| 수신자 일정 | Period **5분**, Grace **10분** |
| 실제 보이는 간격 | 최근 행들은 **약 10분 간격**. 소스의 Watchdog `group_interval=5m`, `repeat_interval=5m`과 다르므로 실제 5분 주기를 검증했다고 표시하지 않음 |

수신 성공·현재 Up은 확인했다. 발신 계수 22→23의 특정 증가와 특정 수신 행을 요청 ID로 1:1 대조한 것은 아니며, 지연 분포·지속 가용성·통신 중단 시 알림 발송은 이번에 시험하지 않았다. 최근 10분 간격의 원인은 실행 설정과 스케줄을 별도로 읽어 확인할 운영 후속이다. 현재 5분 Period와 10분 Grace를 변경하지 않았다.

`Ping Now!`, Pause, 일정·알림 수정, 이벤트 삭제는 실행하지 않았다. 원문 URL이 보이지 않는 이벤트 화면은 개인 임시 파일 `/private/tmp/healthchecks-watchdog-receipts-20261028.png`로 보존했으며 Git에 추가하지 않았다. 로그인 전의 수신 미확인 항목은 이 확인으로 해소했다.

## 재설치 시 판정 기준

- 로컬 보존 URL과 새 클러스터 Secret의 일치, Watchdog 규칙의 firing, Alertmanager의 활성 수신자 `heartbeat-receiver`를 먼저 확인한다. Secret의 존재만으로 외부 수신 성공이라 기록하지 않는다.
- 자연 재전송 전후의 웹훅 요청/알림 계수와 실패 계수를 같은 Pod에서 비교한다. 다른 활성 웹훅 경보가 있거나 Pod 재시작으로 계수가 초기화되면 그 구간을 Watchdog 단독 결과로 사용하지 않는다.
- 수신자 계정 화면의 last ping 확인이 필요한 운영 기준이라면 별도 읽기 권한으로 확인하고, Alertmanager 측 성공과 구분해 기록한다. 수동 ping을 보내는 경우 자연 경로 검증으로 세지 않는다.

이번 작업에서 Secret 값 출력, 합성 경보 접수, 외부 모델 호출, 클러스터 설정 변경은 없었다. 임시 kubeconfig는 조회 후 삭제했다. 다음 B-02 기존 S10 시각·이력 대조의 권장 추론 깊이: **GPT-6 SOL high**.
