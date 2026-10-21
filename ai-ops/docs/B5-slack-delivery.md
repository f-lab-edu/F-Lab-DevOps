# B-05 Slack 전달 복구 및 AI 실패 경로 검증

2026-10-21 **Slack 연동 복구와 AI 중단·인증 실패 중 기존 알림 유지 검증을 완료**했다. [검증 결과](assets/B5-slack-delivery-validation.json)를 함께 확인한다.

## 원인과 수정

기존 Alertmanager는 Slack `channel: "#test"`를 고정했고 실제 요청은 HTTP 404 `channel_not_found`로 실패했다. 같은 보호 Webhook에 채널 필드를 생략한 점검 메시지 1건은 HTTP 200 `ok`였다. 이 결과로 Webhook 자체가 아니라 고정 채널 지정이 이번 오류의 원인임을 확인했다.

`cluster-addons/prometheus/values.yaml`에서 고정 채널 지정만 제거했다. 실제 Alertmanager 0.32.0의 `amtool check-config`가 통과했고, [PR #48](https://github.com/f-lab-edu/F-Lab-DevOps/pull/48)은 앱·Helm·Terraform CI 통과 후 `31bd4adb820e33db13e3f98145c8ffcb629cd111`로 main에 병합했다. Argo의 새 revision 동기화뿐 아니라 실행 중인 설정 파일에 채널 지정이 없는 것까지 확인했다. Webhook·개인 API 키를 교체하거나 Git에 기록하지 않았다.

[Slack 공식 안내](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)는 Webhook의 목적 채널과 채널 오류를 설명한다. [사용 버전의 Alertmanager 구현](https://github.com/prometheus/alertmanager/blob/v0.32.0/notify/slack/slack.go)은 빈 channel을 요청에서 생략한다. 이번 수정은 해당 Webhook에서 실제 성공한 기본 채널을 사용한다.

## 실제 검증

| 조건 | AI 쪽 관측 | Slack 쪽 관측 |
|---|---|---|
| AI StatefulSet replica 0 | Pod 부재와 Webhook 요청 실패 증가 | 성공 알림 +1, 실패 증가 0 |
| Alertmanager 클라이언트에 임시 오인증값 적용 | 실제 401 및 Webhook 요청 실패 증가 | 성공 알림 +1, 실패 증가 0 |

각 시험은 `validation_run`·`validation_case`가 있는 명시적 합성 경보를 운영 Alertmanager에 접수했다. 경보 문구에 실제 앱 장애가 아님을 표시했고 종료 후 해제했다. Slack 성공은 실제 Alertmanager의 알림 성공·실패 계수로 확인했다. Slack 대화 이력 API로 메시지 원문을 재조회한 검증은 아니다.

첫 검증 도구는 재시도 중인 Webhook 실패를 최종 알림 실패 계수로 기다려 시간 초과됐다. 제품 실패로 판정하지 않았으며 원상 복구했다. 실제 시도 실패를 나타내는 `alertmanager_notification_requests_failed_total`로 도구를 고쳐 두 조건을 검증했다. 처음의 실패 기록은 개인 작업 폴더에 별도 보존했다.

## 원상 복구와 한계

원래 클라이언트 인증을 Secret·실제 마운트 파일 양쪽에서 비교 확인했다. AI replica 1·Pod 2/2 Ready·worker probe·같은 PVC UID·Prometheus up=1·예약 정리 enabled=1을 확인했고 해당 시험의 활성 경보는 0개다. 외부 모델 호출은 없었다.

실행 설정의 Watchdog 단독 heartbeat, AI+Slack, 일반 Slack 분기는 실제 `amtool config routes test`로 확인했다. Watchdog의 인위적 외부 heartbeat는 보내지 않았고 Healthchecks 수신 화면을 조회하지 않았다. 이번 결과는 AI 자동 복구나 모델 분석 품질 검증을 의미하지 않는다. 인증서 자동 갱신·장기 tombstone 보관·CNI standard 초기 허용 한계 등 기존 후속 범위는 유지한다.
