# B-06 캐시 정상·접근 실패 재현과 복구

상태: **S2 cold-key·S1 연결된 Redis 실패의 실제 재현·보고서 대조·원복 통과**. 로컬 전체 시험 267개(신규 14개)가 통과했다. [검증 목록](assets/B6-cache-scenarios-validation.json)을 함께 확인한다.

이번 작업은 실습 환경에서 **S2 정상 캐시 미스와 S1 Redis 접근 실패를 실제로 만들고, 수집한 근거와 규칙 보고서가 관측을 어떻게 설명하는지 비교하는 단계**다. 설치 준비, 실제 주입 성공, 보고서의 설명 품질, 원복 성공을 각각 기록한다. 라이브 LLM 진단 정확도 시험은 아니다.

## 처리 흐름

실행 잠금·이전 지표 창 확인 → 실습 소유·Pod Ready·최소 권한·Prometheus 표본 검사 → 고유 테스트 아이템 생성 → miss/hit 기준선 → 제한된 주입 → 실제 counter·응답 확인 → 주입 기록을 별도 파일로 고정 → 중립적인 질문으로 실습 자료 수집·규칙 v2 보고서 생성 → 결과 대조 → finally에서 Redis·자기 데이터 복구 → 운영 식별자 불변 확인 순서다.

| 파일 | 역할 |
|---|---|
| `src/ai_ops/b6_scenarios.py` | 고정 실습 대상 명령, 앱 관측, S1/S2 진행과 finally 복구를 구현한다. |
| `scripts/run_b6_scenario.py` | 단일 실행 잠금·중단 기록·이전 5분 창·비공개 결과 출력을 관리한다. |
| `tests/test_b6_scenarios.py` | 응답 유실·수집 실패·원복 실패·다른 Redis UID·정상 사례의 허위 원인 등을 검사한다. |
| `evals/rubrics/b6-live/scenarios.json` | 기대 관측·금지할 해석·복구 기준이다. 실제 정답표와 분리해 조사 입력에 넣지 않는다. |

## S2 — 캐시에 데이터가 처음 없을 때

고유한 실습 아이템을 만들고 그 아이템의 현재 세대 캐시 키만 지운다. 최근 쓰기 marker가 존재하면 실제 만료를 확인한다. 첫 GET은 miss, 다음 GET은 hit이고 둘 다 HTTP 200이어야 한다. Redis PING도 정상이어야 한다. 첫 counter 표본 수집 후 같은 cold→hit 쌍을 한 번 더 실행해 이후 증가를 관측한다.

검토자는 `observations`의 counters_before/after에서 miss·hit 증가를 확인한다. error·unavailable이 증가했다면 정상 기준선 통과로 처리하지 않는다. 원래 없던 counter가 처음 값 1로 출현하는 것과 기존 값 0에서 1로 증가하는 것은 구분해 저장한다. 보고서가 원인 후보를 만들지 않으면 `no_false_cause`, 원인을 의심하면 `needs_review`로 남긴다. 전체 서비스 healthy나 정상 진단 완성을 요구하지 않는다.

자연 TTL 만료 실험은 이번 cold-key 시험과 다르며 후속이다. missing error 시계열을 인위적으로 만들어 0으로 채우지 않는다. 보고서가 자료 부족으로 판단을 유보하는 것은 허용한다.

## S1 — 연결된 Redis에 접근할 수 없을 때

먼저 miss→hit 기준선과 기존 캐시 연결을 확보한다. **lab-redis Deployment만** replicas 1→0으로 바꾸고 Pod 부재와 Ready 0을 확인한다. 제한된 조회를 세 번 수행하고 각 조회 사이에 지표 수집 시간을 둔다. HTTP 200·같은 아이템 반환과 error 또는 unavailable의 증가가 실제 관측 조건이다. Redis가 없는데 데이터 응답이 유지되면 별도 PostgreSQL fallback이 동작한 것으로 본다.

Prometheus에 보이는 error 증가와 보고서의 캐시 지표 근거 인용·suspected_cause가 함께 있으면 `cache_error_supported`, 그렇지 않으면 `needs_review`다. unavailable만 보였거나 자료가 부족한 결과를 억지로 error 원인 적중으로 바꾸지 않는다. 캐시 실패와 사용자 요청 실패도 구분한다. HTTP가 성공했다고 Redis 정상으로 서술하거나, error 하나로 Redis 서버 원인을 확정하면 안 된다.

finally에서 원래 UID의 Redis를 replicas=1로 복원하고 Ready·PING을 확인한다. Redis가 이미 1이면 중복 변경하지 않는다. 다른 UID로 바뀐 자원은 수정하지 않는다. 생성한 고유 아이템만 확인·삭제하고 /readyz를 확인한다. Redis는 emptyDir이므로 재생성 시 실습 캐시 전체가 초기화된다. 복구 후 이전 캐시 데이터가 그대로 보존됐다고 주장하지 않는다.

## 실행과 기록

기존 [실습 환경](B6-isolated-lab.md)의 준비 검증이 통과해야 한다. 실행기는 운영자로 실습을 조작하며, 수집기에 쓰기 권한을 추가하지 않는다. 준비된 앱·AI 이미지를 사용하므로 이번 로컬 실행기 변경만으로 운영 이미지를 다시 빌드할 필요는 없다.

```bash
export AI_OPS_FILES_ROOT=/private/tmp/ai-b6-lab-20261021
export PYTHONPATH=ai-ops/src
python ai-ops/scripts/run_b6_scenario.py --scenario S2 --state-dir "$AI_OPS_FILES_ROOT/scenarios" --output "$AI_OPS_FILES_ROOT/s2-cold-miss.json"
# 이전 실행 복구 완료 및 제어 파일의 not_before 경과 후 수행한다.
python ai-ops/scripts/run_b6_scenario.py --scenario S1 --state-dir "$AI_OPS_FILES_ROOT/scenarios" --output "$AI_OPS_FILES_ROOT/s1-redis-unavailable.json"
```

출력은 새 이름이어야 한다. 같은 실습 환경에는 **항상 같은 state-dir**를 사용하고 동시에 다른 운영자가 개입하지 않는다. 새 폴더를 만들어 잠금·대기 조건을 우회하지 않는다. 직전 원복 후 305초 동안 다음 사례를 거부해 5분 Prometheus 창의 혼합을 줄인다. 같은 앱 프로세스의 이전 오류 시계열·15분 로그는 남을 수 있으므로 원인 해석 시 여전히 시간 범위를 대조한다.

한 번의 실행은 앱에 수동으로 보내는 HTTP 요청을 최대 20회로 제한하며 조회 전후 /metrics/ 요청과 삭제·확인 요청도 포함한다. Prometheus의 기존 주기 scrape는 별도다. 35초 대기는 새 counter 표본을 확보하기 위한 간격이며 표본 품질의 증명으로 대신하지 않는다. 수집기는 실제 up·scrape 품질을 확인하고 부족하면 누락으로 남긴다.

`출력이름.ground-truth.json`에는 보고서 수집 전에 실제 주입 관측을 저장한다. 최초 S1/S2 파일의 status는 종료 전 실행의 실패 우선 기본값이며 최종 판정이 아니다. S4 추가 이후 새 기록은 observations_recorded·execution_status_final=false로 명시한다. 최종 결과 파일의 status와 cleanup을 함께 확인한다. 최종 결과에는 근거 ID·상태·counter·시각·해시와 원복 결과를 담는다. 원문 로그·아이템 내용·암호·AI 초안은 외부로 내보내지 않는다. 내부 기록은 UTC, 사람이 읽는 결과 설명은 KST다.

## 실패·중단 시 대응

- 생성 응답을 잃어도 앱 Pod의 고유 소유 기록과 정확한 이름으로 자기 아이템만 찾아 삭제한다. 다른 아이템을 일괄 삭제하거나 Redis FLUSHALL을 사용하지 않는다.
- 수집 실패도 finally에서 원복한다. 원복이 실패하면 `recovery_failed`이며 후속 사례를 차단한다. 결과가 `needs_review`면 실제 관측은 성공했어도 보고서 설명 조건을 만족하지 못한 것이다.
- 실행 프로세스가 강제로 종료되거나 컴퓨터가 꺼지면 finally가 보장되지 않는다. `control.json`의 active·run_id·출력 경로를 보존하고 실습 Redis replicas와 앱 Pod의 `/tmp/b6-owned-<run_id>.json`을 확인한다. 운영 자원을 건드리지 말고 정확한 UID의 Redis 복원·자기 아이템 삭제·PING/Ready를 확인한 뒤 제어 상태를 복구한다. 제어 파일을 지우고 새 실행을 시작하지 않는다.
- Pod가 재생성돼 소유 기록을 잃었다면 고유 이름·생성 기록을 먼저 대조한다. 복구할 근거가 부족하면 자동 삭제하지 않는다. 실습 결과를 따로 보관한 후 namespace 재생성으로 초기화하는 별도 절차를 사용한다.

B-06 전체 완료나 C 실행 권한 부여는 이 두 사례로 결정하지 않는다. S3·S4·S9·S10·S11과 라이브 모델 비교의 미검증 범위는 평가표에 유지한다.

## S2 실제 결과 — 2026-10-21

14:14~14:16 KST 실행은 통과했다. 두 차례의 cold→hit에서 앱 get_item counter는 miss 1→2, hit 1→2로 관측됐으며 모든 데이터 조회는 200이었다. 첫 counter 부재는 `{}`로 보존하고 0으로 채우지 않았다. 앱 수동 HTTP는 /metrics/·복구 확인을 포함해 17회였고 테스트 아이템을 삭제했다. 운영 앱·AI PVC 식별자와 실습 UID·이미지는 같았다. 전체 실행 약 100.63초에는 지표 수집 간격과 복구가 포함되며 MTTR이 아니다.

규칙 v2는 사실 4개·원인 후보 0개·partial/insufficient_evidence/unknown이었다. miss·hit는 Prometheus에도 관측됐지만 error 등 일부 결과와 HTTP 오류 분자는 존재하지 않는 시계열이었다. 로그는 상한으로 truncated이고 Argo·CI 이력도 없다. 정상 캐시 동작에 허위 원인을 만들지 않은 하위 조건을 통과했으며 전체 정상 진단 성공으로 채점하지 않는다. [최종 실측](assets/B6-s2-cold-miss.json), [조사 전 주입 기록](assets/B6-s2-cold-miss.ground-truth.json)을 참고한다.

## S1 실제 결과 — 2026-10-21

S2 복구 뒤 305초 창을 지나 14:21~14:24 KST 실행했다. 기준 miss→hit 후 실습 Redis Pod를 제거해 Ready 0을 확인했고, 중단 중 GET 세 번에서 앱 error counter가 처음 1로 출현한 뒤 2·3으로 증가했다. 세 응답은 모두 200이며 DB의 같은 아이템을 반환했다. 수동 HTTP 요청은 /metrics/·복구 확인 포함 20회다. 전체 약 186.30초에는 기준선·지표 간격·원복이 포함되며 MTTR이 아니다.

보고서는 partial/suspected_cause/unknown, 사실 3개·원인 후보 1개였고 ev-metric-cache_outcomes를 근거로 인용했다. Prometheus error increase 약 2.481은 추정치이며 앱에서 직접 확인한 3회와 구분한다. 실제 Redis 중단이 정답 기록에 있지만 보고서는 캐시 조회/데이터 처리 오류 가능성까지만 설명했다. Redis 서버 원인 확정이나 서비스 전체 장애를 진단했다고 확대하지 않는다.

finally에서 동일 UID Redis replicas=1·Ready·PING, 자기 아이템 삭제를 확인했다. 운영 앱·AI StatefulSet·PVC 식별자와 실습 UID·이미지를 유지했다. [최종 실측](assets/B6-s1-redis-unavailable.json), [조사 전 주입 기록](assets/B6-s1-redis-unavailable.ground-truth.json)을 참고한다. 단일 Primary fallback 하위 사례이며 초기 미연결 클라이언트의 unavailable 변형·진짜 Replica·자연 TTL·라이브 모델은 검증하지 않았다. 다음은 S4 제한된 DB 세션 지연 기준선·주입·복구이며 GPT-6 SOL high를 추천한다.
