# B-06 S4 실습 DB 세션 지연과 복구

상태: **실제 기준선·750ms 주입 3회·규칙 보고서·지연 해제·원복 통과**. 로컬 전체 274개 시험(신규 S4 7개)이 통과했다. [검증 요약](assets/B6-db-delay-validation.json), [최종 실측](assets/B6-s4-db-session-delay.json), [조사 전 관측](assets/B6-s4-db-session-delay.ground-truth.json)을 함께 확인한다.

이 단계는 **실습 앱이 사용하는 DB 세션에만 짧은 지연을 넣고, 실제 지연·관측 지표·규칙 보고서·해제 후 응답을 대조하는 작업**이다. 운영 RDS나 운영 앱을 변경하지 않는다. 실습의 단일 PostgreSQL에서 수행하므로 RDS 서버 장애나 실제 Replica 지연의 재현으로 표현하지 않는다.

## 처리 흐름

실행 잠금·이전 305초 창 확인 → 실습 소유·읽기 권한·Pod·Prometheus 표본 검사 → 고유 아이템 생성 → 지연 없는 DB 조회 기준선 → 캐시 우회와 750ms 지연 요청 3회 → 주입 관측 별도 저장 → 규칙 v2 보고서 수집·대조 → finally에서 지연 헤더 없는 조회·데이터 삭제 → 실습·운영 식별자 불변 확인 순서다.

`src/ai_ops/b6_scenarios.py`와 `scripts/run_b6_scenario.py`의 기존 S2·S1 경로에 S4를 추가했다. 운영자 실행기가 실습 요청을 보내며, 수집기에는 쓰기 권한을 주지 않는다. 앱 이미지나 배포 설정을 변경하지 않아 재빌드·재배포는 필요하지 않다. 기존 `ENABLE_FAULT_INJECTION=true`, `MAX_FAULT_DELAY_MS=1000`인 실습 앱만 대상으로 한다.

## 무엇을 넣고 무엇을 측정하는가

기준선과 지연 요청 모두 `X-Week12-Bypass-Cache: true`를 사용한다. 캐시 hit 때문에 DB를 건너뛰는 경우를 제거해 같은 DB 조회 경로를 비교한다. 지연 요청에만 `X-Week12-Read-Delay-Ms: 750`을 추가하며, 앱의 `_inject_read_delay`가 사용하는 세션에서 `pg_sleep(0.75)`가 실행된다. 지연 헤더는 해당 요청에만 적용되므로 DB나 앱에 영구 설정을 남기지 않는다.

각 관측은 GET 전후 `/metrics/`를 읽고 다음 조건을 확인한다.

- HTTP 200과 자기 아이템 반환.
- cache bypass counter의 실제 증가와 error/unavailable 증가 없음.
- `db_query_latency_seconds_count{operation="select_one"}`가 한 표본 증가.
- DB histogram sum의 실제 증가와 클라이언트 응답 시간.
- 기준선은 0.5초 미만, 지연 요청은 기준선보다 0.5초 이상 느리고 DB sum 증가가 0.65초 이상.

이 조건은 작은 실습 지연이 적용됐는지 검사하는 한 번의 하위 사례 기준이다. 서비스 SLO나 통계적 성능 기준이 아니다. 지연 3회 사이의 35초 간격은 Prometheus 표본을 얻기 위한 시간이며, 수집기는 실제 scrape 품질도 검사한다. /metrics/·원복 확인을 포함한 수동 앱 HTTP는 최대 20회다.

## 보고서 판독 기준

보고서에는 원인이나 주입 명령을 알리지 않고 기존 중립 질문으로 조사한다. 실제 주입 관측은 조사 전에 `.ground-truth.json`에 별도 저장한다. 새 기록은 `status=observations_recorded`, `execution_status_final=false`로 종료 전 기록임을 명시한다. 이전 S1/S2의 실패 우선 status 기본값과 구분하며 기존 기록을 다시 쓰지 않는다.

수집한 DB P99가 0.5초 이상이고 보고서 사실이 `ev-metric-db_latency`를 인용하며, 원인 후보 0개·현재 상태 unknown을 유지하면 `app_db_latency_observed_cause_deferred`로 기록한다. 이는 **앱 측 DB 조회 구간이 느렸다는 설명과 원인 확정 유보**의 확인이다. RDS 서버 또는 SQL 자체 문제를 밝혀낸 결과가 아니다. 다른 원인 후보를 내거나 지연 근거가 없으면 실제 주입이 성공했더라도 `needs_review`다.

Prometheus의 5분 histogram은 이전 표본을 포함하며 분위수는 추정치다. 개별 응답 0.75초와 P99가 정확히 같을 필요는 없다. 복구 직후 5분 P99가 즉시 낮아져야 한다고 요구하지 않는다. 복구는 지연 헤더를 보내지 않은 실제 요청과 그 요청의 DB sum 증가로 확인한다.

## 실행·복구

```bash
export AI_OPS_FILES_ROOT=/private/tmp/ai-b6-lab-20261021
export PYTHONPATH=ai-ops/src
python ai-ops/scripts/run_b6_scenario.py --scenario S4 --state-dir "$AI_OPS_FILES_ROOT/scenarios" --output "$AI_OPS_FILES_ROOT/s4-db-session-delay.json"
```

실습 환경·제어 폴더는 [S1/S2 실행 절차](B6-cache-scenarios.md)와 같다. 새 폴더로 이전 원복·지표 창 차단을 우회하지 않는다. 출력 파일도 덮어쓰지 않는다. 일부러 느린 기준선, 적용되지 않은 지연, 부족한 DB 표본을 통과로 간주하지 않는다.

finally는 수집 실패나 지연 관측 실패에도 `delay-recovered`를 실행한다. 이 요청은 캐시 우회만 사용하고 지연 헤더를 보내지 않는다. HTTP 200·우회 counter·DB 한 표본 증가·응답/DB sum 각각 0.5초 미만을 확인한다. 이후 자기 아이템을 삭제하고 Ready·Redis PING과 운영 식별자 불변을 확인한다. 복구 확인이 실패하면 `recovery_failed`로 다음 실행을 차단한다. 강제 프로세스 종료·컴퓨터 종료의 한계와 대응은 기존 실습 제어 절차를 따른다.

## 검증 범위

신규 시험은 실제 지연 확인, 지연 미적용 실패, 수집 실패 후 복구, 원복 실패 차단, 보고서 DB 근거 누락, 앱 측 지연으로 DB 서버 원인을 확정하지 않음, 잘못된 지연 헤더·표본 부재 거부를 다룬다. 라이브 모델 호출이나 운영 장애 자동 해결은 포함하지 않는다. 실측 결과를 기록한 뒤에도 B-06 전체와 C 진입 조건은 별도로 유지한다.

## 2026-10-21 실제 결과

15:36~15:39 KST 실행은 passed였다. 기준 응답 약 9.31ms에서 주입 중 758.25~763.90ms로 증가했고, 지연 헤더 없는 복구 요청은 약 5.97ms였다. DB histogram은 주입 요청마다 한 표본씩 증가했고 sum 증가는 각각 0.754024, 0.753821, 0.753598초였다. 캐시 bypass가 각 요청에서 증가하고 error/unavailable 증가는 없었다. 수동 HTTP는 20회, 전체 약 176.87초에는 표본 수집 간격과 원복을 포함한다. MTTR·통계적 성능 개선 측정이 아니다.

규칙 v2 보고서는 사실 4개·원인 후보 0개·partial/insufficient_evidence/unknown이었다. DB P99 약 0.993초와 해당 증거 ID를 사실로 설명했으며, RDS 서버 원인을 확정하지 않았다. 로그 truncated·Argo/CI 부재·일부 지표 부족은 그대로 남았다.

finally의 지연 해제·자기 데이터 삭제·Redis PING과 운영 앱/AI StatefulSet/PVC·실습 UID/이미지 불변이 통과했다. 새 이미지나 권한을 배포하지 않았고 외부 모델 호출·API 비용은 0이다. 다음은 S3 잘못된 이미지의 실패 관측·원래 digest 복구이며 GPT-6 SOL high를 추천한다. B-06 전체와 C 진입은 계속 보류한다.
