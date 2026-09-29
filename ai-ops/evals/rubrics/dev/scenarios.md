# A2 개발 사례 8건의 검토 기준

이 파일은 개발용 채점·사람 검토 기준이다. `evals/fixtures/dev/`와 분리해 두며, 향후 agent의 증거 조회 루트나 모델 입력에 포함하지 않는다. 최종 holdout 정답은 별도로 준비한다.

| 사례 | 사람 검토 시 기대하는 구분 | 과장하면 안 되는 주장 |
|---|---|---|
| S1 `s1-cache-error` | `error=3`, 캐시 조회 `RedisError` 로그, event 권한 거부를 구분. 캐시 접근/설정/데이터 문제를 조사 대상으로 제시 | Redis 네트워크 단절이나 서버 장애 확정 |
| S2 `s2-normal-miss` | `miss=5`, `error=0`, 캐시 사용 의도·URL 설정 여부를 함께 보고 정상 miss 가능성을 유지 | miss만으로 장애 선언 또는 error=0을 데이터 누락으로 처리 |
| S3 `s3-image-tag` | image 변경과 `ImagePullBackOff` event의 시간·대상 연결 | tag가 없다는 합성 메시지만으로 실제 registry 인증 문제를 확정 |
| S4 `s4-db-latency` | 앱 측 P99 지연과 표본 수, 실습 지연 포함 가능성을 명시 | 순수 SQL 지연 또는 RDS 서버 장애 확정 |
| S5 `s5-insufficient-data` | metric 누락과 log 권한 거부를 그대로 보존하고 추가 확인 요청 | 누락을 값 0이나 정상 상태로 해석 |
| S6 `s6-healthy` | Ready·재시작 0·HTTP 오류 비율 0과 요청량을 함께 검토 | 증거 없이 장애 원인 생성 |
| S9 `s9-primary-consistency` | `consistency_primary=2`, 최근 쓰기 marker와 Primary 조회를 정상 일관성 경로 후보로 해석 | Primary 경로를 DB 장애·Replica 장애로 확정 |
| S10 `s10-rollback-recovered` | 실패 image/revision과 이후 rollback revision·현재 healthy를 시간순으로 구분 | 현재 정상이라는 이유로 과거 실패를 부정하거나 이전 이미지 정상성을 확정 |

여덟 사례는 합성 데이터다. `DEMO_SECRET_SENTINEL`은 마스킹 검사용 가짜 값이며 정규화된 `EvidenceBundle`·질문·도구 출력에 남아서는 안 된다. S7/S8/S11은 별도의 권한·실패 처리·입력 의미 기능 시험으로 남겨둔다. 이 기준은 A2에서 AI 진단 성능을 측정했다는 뜻이 아니다.
