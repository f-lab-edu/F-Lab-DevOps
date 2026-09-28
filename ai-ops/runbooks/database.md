# DB 역할과 지연 판단 런북

- document_id: `database`
- version: `1`
- 확인일: 2026-09-27
- 코드 기준: `e4c51585bbc449f444527ed3c0614aac4b4b5700`
- 근거: [database.py](../../url-shortener/app/core/database.py), [config.py](../../url-shortener/app/core/config.py), [item.py](../../url-shortener/app/api/routes/item.py), [health.py](../../url-shortener/app/api/routes/health.py), [metrics.py](../../url-shortener/app/core/metrics.py), [운영 values](../../url-shortener/url-shortener-chart/values.prod.yaml), [rds.tf](../../terraform/rds.tf).

## 1. 역할과 정상 경로

| 조건 | 코드에서 확인한 동작 | 판단 기준 |
|---|---|---|
| 생성·삭제 | WriteSession 사용 | 논리 Primary 경로 |
| 정상 캐시 miss·초기 client 없음·실습 bypass | 기본 ReadSession 사용 | 실제 Replica 여부는 engine 구성에 따라 다름 |
| 쓰기 marker 존재·조회 Redis/JSON 오류 | WriteSession 사용 | 최근 쓰기는 정상 후보, 오류는 캐시 원인과 구분 |
| read URL이 비어 있거나 write URL과 같음 | read_engine과 write_engine 공유 | `db_route=replica` 로그가 있어도 실제 Replica 연결 증거가 아님 |
| read URL이 존재하고 write URL과 다름 | 별도 read engine 생성 | URL이 다르다는 것만으로 접속 대상의 DB 역할 확정 불가 |

`/items/_db`는 각 세션의 `pg_is_in_recovery`, server_addr, db, user를 반환하는 진단 경로다. 승인된 수집에서 역할 검증에 쓸 수 있지만 주소·DB명·사용자는 필요한 범위로 정제한다. 현재 A0에서는 호출하지 않았다.

`/healthz`는 정적 응답이며 DB 연결을 확인하지 않는다. `/readyz`는 write 세션의 `select 1`을 확인하며 SQLAlchemyError 시 503이다. ready 성공이 Redis·Replica·모든 쿼리 정상이라는 의미는 아니다.

## 2. 연결 풀과 지연의 의미

- 현재 기본·운영 풀은 write `2+1`, read `3+1`, pool timeout 10초다. 값은 환경 설정으로 바뀔 수 있으므로 실제 정제 설정을 확인한다. 과거 `5+10`, `10+20` 설명은 사용하지 않는다.
- 운영 HPA max 10과 기본 RollingUpdate surge를 고려한 코드의 예산 가정은 최대 13 Pod, Primary 39·별도 read pool 52 연결이다. 실제 Pod 수·프로세스 수·다른 client·DB 한도까지 검증한 결과가 아니다. engine 공유 시 풀도 공유한다.
- `db_query_latency_seconds`는 애플리케이션에서 측정한 operation 구간이다. select는 실습 pg_sleep과 ORM 조회를 포함한다. insert는 캐시/commit/refresh 등, delete는 조회·marker·commit 등 경로의 일부가 포함되어 순수 SQL 서버 실행 시간으로 보지 않는다.
- 실습 지연은 `ENABLE_FAULT_INJECTION`이 켜진 경우 헤더를 받아 기본 최대 2000ms로 제한한다. 운영 values는 false다. 운영에서 지연 주입을 바로 수행하지 않는다.
- Terraform의 Primary는 `multi_az=true`이며 별도 Read Replica가 정의되어 있다. 이것은 선언된 구성이고 실제 AWS 상태·failover 실행 증거는 아니다. Standby와 Read Replica의 역할을 동일시하지 않는다.

## 3. 조사와 판단

1. 사건 대상·시간, 실제 정제 설정, `read_engine_role`(확인되지 않으면 unknown), 최근 쓰기·캐시 오류를 확인한다.
2. HTTP 지연·오류, operation별 DB latency/count, Pod 재시작·자원과 배포 변화를 대조한다.
3. 풀 대기/연결 오류·쿼리 오류 로그, 허용된 DB 서버 측 지표·ReplicaLag 자료가 있으면 비교한다. 지연 histogram만으로 slow SQL·락·ReplicaLag를 확정하지 않는다.
4. fault injection 여부와 시간을 확인한다. 실습으로 늘어난 지연을 실제 DB 서버 장애라고 설명하지 않는다.

| 관측 | 판단 | 추가 증거 |
|---|---|---|
| 최근 쓰기 + consistency_primary | 정상 일관성 경로 후보 | 쓰기 결과와 marker 구간·만료 이후 |
| replica 로그 + read URL 미설정 | 논리 read 경로, 실제 shared pool | 정제 구성·필요 시 역할 probe |
| DB P99·HTTP 지연 동반 증가 | DB 접근 경로 지연 후보 | 표본 수, 오류, pool/서버 지표, 실습 옵션 |
| healthz 정상 + readyz 실패 | 프로세스 응답과 Primary 준비 상태 불일치 | 연결 오류·준비 검사 시각 |
| 낮은 요청량·누락된 서버 자료 | 원인 판단 제한 | 누락을 0 또는 정상으로 처리하지 않음 |

## 4. 대응과 검증

풀·쿼리·자원 조정은 실제 병목과 전체 연결 예산을 확인한 뒤 제안한다. read 세션 로그만으로 URL 변경을 제안하거나 Read Replica 자동 승격을 가정하지 않는다.

대응 후 목표 대상의 readyz와 사용자 조회, 지연·오류·실제 역할을 함께 확인한다. 단위 helper 시험은 실제 DB 연결·ReplicaLag·읽기 일관성 통합 시험을 대신하지 않는다.
