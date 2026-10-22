> 실제 S10-04 저장 snapshot의 오프라인 재분석이다. 새 클러스터 수집·장애 재실행·모델 분석 결과가 아니다.

# 운영 조사 보고서

> 수집 시점의 captured 증거 묶음을 규칙으로 분석한 결과입니다. 이후 상태 변화는 확인하지 않았으며 AI 분석·변경 실행은 하지 않았습니다.

- 사건 ID: `inc-c8b2da238eb345d39de51c1298d4e63a`
- 실행 ID: `run-083b5a166c834cc29600fa4b6b864989`
- 보고서 상태: partial
- 요청 시각: 2026-10-22 13:33:28 KST (UTC+09:00)
- 조사 범위: 2026-10-22 13:04:07 KST (UTC+09:00) ~ 2026-10-22 13:19:08 KST (UTC+09:00)
- 진단: 증거 부족
- 최신 상태 판단: 확인 불가
- 입력 묶음 SHA-256: `5660b46ffce46bdae41be71a28e9026cc8f5b40d838451c1be36128e60b9a4d0`

## 관측 사실

- 수집 당시 확보한 Pod 상태 1개 중 1개가 Running/Ready였다. 이 관측이 과거 CI 실패를 지우거나 현재 전체 서비스 복구를 입증하지는 않는다. (근거: ev-pod-0-status)

## 원인 후보와 반대 근거

- 제시할 원인 후보가 없습니다.

## 부족한 증거

- kubernetes/pod-0-log.json: truncated
- kubernetes/event-0.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-1.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-2.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-3.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-4.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-5.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-6.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-7.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-8.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-9.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-10.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-11.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-12.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- kubernetes/event-13.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- deployment/deployment-history-0.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- deployment/deployment-history-1.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- deployment/deployment-history-2.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- deployment/deployment-ci-deployment.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- deployment/deployment-ci-rollback.json: 관측 시각이 조사 범위 밖인 오래된 자료\(stale\)여서 판단에서 제외했다.
- prometheus/metric-cache-outcome-gaps.json: empty\_response
- prometheus/metric-cache\_hit\_ratio.json: empty\_response
- prometheus/metric-http\_error\_ratio.json: no\_traffic
- prometheus/metric-api\_latency.json: no\_traffic
- prometheus/metric-db\_latency.json: no\_traffic

## 추가 확인

- 같은 run/attempt의 실패·원복 artifact와 대상 UID·revision을 함께 확인한다.
- 사용자 HTTP 경로·DB·Redis의 복구는 해당 관측 이후의 별도 자료로 확인한다.

## 대응 제안

- 실행한 변경이나 대응 제안이 없습니다.

## 증거 시각

| 증거 ID | 상태 | 관측 시각 | 수집 시각 |
|---|---|---|---|
| `ev-pod-0-status` | available | 2026-10-22 13:19:07 KST (UTC+09:00) | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-pod-0-log` | missing | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-pod-0-config` | available | 2026-10-22 13:19:07 KST (UTC+09:00) | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-0` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-1` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-2` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-3` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-4` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-5` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-6` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-7` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-8` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-9` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-10` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-11` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-12` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-event-13` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-deployment-object` | available | 2026-10-22 13:19:07 KST (UTC+09:00) | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-deployment-argo` | available | 2026-10-22 13:17:49 KST (UTC+09:00) | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-deployment-history-0` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-deployment-history-1` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-deployment-history-2` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-deployment-ci-deployment` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-deployment-ci-rollback` | stale | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-metric-cache-outcome-gaps` | missing | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-metric-cache_outcomes` | available | 2026-10-22 13:19:07 KST (UTC+09:00) | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-metric-cache_hit_ratio` | missing | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-metric-http_error_ratio` | missing | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-metric-api_latency` | missing | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |
| `ev-metric-db_latency` | missing | 없음 | 2026-10-22 13:19:08 KST (UTC+09:00) |

## 한계와 실행 정보

- 수집 시점의 CI 배포 이력을 규칙으로 해석한 결과다. AI 분석이나 변경 실행은 하지 않았다.
- 배포 관측과 CI 단계 결과만 해석하며 다른 경보 원인이나 현재 전체 서비스 건강을 판정하지 않는다.
- 사용자 요청 경로 복구는 확인하지 않았다. 자동 원복 기록만으로 HTTP·DB·Redis 정상이나 서비스 복구 완료를 주장하지 않는다.
- 동일 run/attempt·artifact·소스·대상 UID와 시간 순서를 만족하는 실패/원복 쌍이 없어 자동 원복 흐름을 확정하지 않았다.
- CI 자료 ev-deployment-ci-deployment의 원래 관측은 2026-10-22 13:02:13 KST \(UTC\+09:00\)이다. 현재 조사 범위 밖인 오래된 자료\(stale\)여서 최초 실패·자동 원복·원인 판단의 근거에서 제외했다.
- CI 자료 ev-deployment-ci-rollback의 원래 관측은 2026-10-22 13:02:33 KST \(UTC\+09:00\)이다. 현재 조사 범위 밖인 오래된 자료\(stale\)여서 최초 실패·자동 원복·원인 판단의 근거에서 제외했다.
- 실행 방식: 규칙 (`rules-captured-ci-v1`), 도구 호출 0회
