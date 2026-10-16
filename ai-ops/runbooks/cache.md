# 캐시 상태와 일관성 판단 런북

- document_id: `cache`
- version: `1`
- 확인일: 2026-09-27
- 코드 기준: `e4c51585bbc449f444527ed3c0614aac4b4b5700`
- 범위: `/items` 목록·단건 조회와 생성·삭제. 코드 검토 기준이며 실클러스터 시험 결과가 아니다.
- 근거: [cache.py](../../url-shortener/app/core/cache.py), [item.py](../../url-shortener/app/api/routes/item.py), [cache_consistency.py](../../url-shortener/app/core/cache_consistency.py), [config.py](../../url-shortener/app/core/config.py), [metrics.py](../../url-shortener/app/core/metrics.py).

## 1. 관측값의 의미

| 관측 | 코드에서 확인한 동작 | 판단 시 필요한 맥락 |
|---|---|---|
| `unavailable` | REDIS_URL 미설정 또는 최초 client 생성·ping의 RedisError/ValueError로 client가 없음 | 캐시 사용 의도, URL 설정 유무, 초기 연결 경고 |
| `error` | 조회 과정의 RedisError 또는 JSONDecodeError를 잡은 결과. cache를 비우고 write 세션으로 DB 조회 | 정제된 예외 종류, 조회 로그, 캐시 데이터 형식 |
| `miss` | Redis 조회를 시도했지만 사용 가능한 캐시 값이 없음 | TTL·세대 변경·cold key, 트래픽과 error/unavailable |
| `hit` | 캐시 값이 존재하여 hit 카운터를 올리고 JSON을 읽음 | 카운터만으로 데이터 파싱 성공을 보장하지 않음 |
| `bypass` | 실습 fault injection을 켠 상태에서 우회 헤더를 사용 | 환경·옵션·시험 시간. 기본 read 세션으로 조회 |
| `consistency_primary` | 최근 쓰기 marker가 있어 캐시를 건너뛰고 write 세션으로 조회 | 최근 생성·삭제, marker 시간, 설정된 일관성 구간 |
| 캐시 저장 실패 경고 | DB 조회 후 setex 실패를 기록하고 응답은 계속 반환 | 저장 실패는 해당 `error` 카운터로 집계되지 않음 |

한 요청에서 hit 후 JSONDecodeError로 error도 증가할 수 있다. `cache_operation_total`을 모든 상황에서 요청당 하나만 증가하는 카운터로 취급하지 않는다. 형식이 맞는 JSON의 모델 검증 오류 등 위 except 범위 밖 오류까지 같은 fallback이 보장된다고 주장하지 않는다.

client가 처음부터 없으면 기본 read 세션을 사용한다. 이미 존재하는 client로 조회하다 잡힌 오류는 write 세션을 사용한다. 실제 DB 역할은 [DB 런북](database.md)에 따라 확인한다.

## 2. 정상 동작 기준

- 단건 캐시 TTL은 300초, 목록은 60초다. TTL 만료·처음 조회·쓰기 후 세대 변경은 정상 miss 후보다.
- 기본 일관성 구간은 60초이며 chart/config로 주입한다. 생성은 목록 marker, 삭제는 단건과 목록 marker를 DB commit 전에 설정하고, commit 후 해당 세대를 올린다.
- 이전 세대의 늦은 캐시 저장은 새 세대 조회에서 사용하지 않게 한다. 기존 테스트는 이 키 분리와 marker 존재 판단만 확인한다.
- 쓰기 이후 marker 구간의 Primary 조회는 정상 일관성 처리일 수 있다. marker는 쓰기 전에 생성되므로 존재만으로 commit 성공을 확정하지 않는다.
- marker 설정 실패·Redis 중단·실제 ReplicaLag가 구간보다 길 때 일관성이 자동 보장된다고 가정하지 않는다. 관측된 값과 시간 순서를 확인한다.
- 캐시를 의도적으로 끈 환경의 unavailable은 Redis 장애로 단정하지 않는다. Redis가 없어도 DB를 통해 응답할 수 있으나 실제 성공과 지연은 응답·로그로 확인한다.

## 3. 조사 순서와 증거

1. 대상·환경·사건 시간과 캐시 사용 의도(`cache_intended_enabled`), URL 설정 유무(`redis_url_configured`)를 확인한다. 두 필드는 향후 정제 입력 필드이며 현재 메트릭이 아니다. 비밀 URL은 입력하지 않는다.
2. 같은 시간의 endpoint별 hit/miss/error/unavailable/bypass/consistency_primary, HTTP 성공·오류·지연, 표본 수와 누락을 비교한다.
3. 초기 `Redis 연결 실패`, 조회 실패, 저장 실패, `reason=recent_write`를 구분하고 예외 종류를 마스킹하여 확인한다.
4. 최근 생성·삭제·TTL·세대 변화와 실제 DB 경로를 대조한다. 로그의 item 이름·ID 등 사용자 데이터는 공개 자료에서 제거한다.
5. 원인 후보마다 지지·반대 증거를 설명하고 추가 확인과 조치 제안을 구분한다.

| 상황 | 원인 후보 | 확정하지 말아야 할 내용·추가 증거 |
|---|---|---|
| 초기 unavailable + 캐시 활성 의도 | 설정 오류·초기 연결 실패 | URL 존재와 정제된 연결 오류 없이는 Redis 서버 중단 확정 불가 |
| error + 파싱 예외 | 캐시 JSON 형식 문제 | 네트워크 장애로 설명하지 않음. 원문 개인정보는 제공하지 않음 |
| miss 증가 + 정상 접근 | cold key·TTL·세대 변화 | Redis 재시작을 바로 제안하지 않음. 요청량·쓰기 이력 확인 |
| consistency_primary + 최근 쓰기 | 정상 일관성 경로 | marker 만료 전후와 응답을 확인. 라우팅 장애로 단정하지 않음 |
| 메트릭/로그 누락 | 정보 부족 | 정상으로 바꾸지 않고 누락 source·시간을 요청 |

## 4. 대응과 검증

설정 문제는 의도와 설정 주입 여부, 연결 문제는 허용된 네트워크·서비스 상태, 파싱 문제는 데이터 생산·캐시 스키마 변경을 먼저 확인한다. 변경은 제안으로 남기며 직접 Secret 조회·Redis flush·재시작을 수행하지 않는다.

수정 후 같은 대상·시간의 사용자 응답, 오류, 지연, 캐시 상태를 재확인한다. hit 비율만으로 복구를 선언하지 않는다. 실제 Redis 접근·TTL 만료·ReplicaLag·통합 fallback 검증은 후속 실습 범위다.
