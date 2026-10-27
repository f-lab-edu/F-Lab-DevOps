# B-06 정상 캐시 요청 순서 증거 설계

상태: **설계 완료·로컬 계약/모의 검증 구현.** [후속 구현 기록](B6-normal-sequence-local-implementation.md)에 제한 로그 대조·v3 정제·미전송 본문과 모의 보고서를 추가했다. 실제 수집 연결·새 실습·모델 전송·클러스터 변경은 수행하지 않았다. [2026-10-26 정상 S2 일회 비교](B6-normal-model-comparison-20261026.md)의 입력 공백을 해결하기 위한 설계이며 B-06 완료나 C단계 진입을 인정하지 않는다.

후속 [실제 순서 수집](B6-normal-sequence-capture.md)에서 GET 기록·같은 Pod 로그 연결과 실습 한 건·자기 데이터 삭제를 확인했다. 위 미실행 사항은 설계/로컬 계약 시점의 이력이다. v3 exporter·실제 모델 비교·상시 접수는 남아 있다.

## 왜 새 증거가 필요한가

현재 `model_projection.py`는 `workload_status`·`config_summary`·`metric`만 모델 view에 넣는다. S2의 `cache_outcomes`와 `cache_hit_ratio`는 5분 집계이므로 hit·miss가 모두 있었다는 사실은 보여도 **같은 아이템에 대해 첫 조회가 miss, 다음 조회가 hit였는지**는 증명하지 못한다. 실제 실습 실행기의 `observations`에는 순서·아이템 ID·counter 전후값과 주입 조작이 함께 있으나 이는 채점용 원본이다. 시나리오 이름이나 기대 결과를 그대로 모델에 넣지 않는다. 최근 보존 snapshot의 앱 로그 항목도 `missing`이어서 과거 입력을 사후에 보강할 수 없다.

목표는 다음 실습에서 **동일한 실습 아이템의 제한된 조회 순서**를 검증된 새 증거로 만들고, 모델에는 결과 순서만 전달하는 것이다. 운영 앱·다른 아이템·전체 서비스의 정상 여부는 포함하지 않는다.

## 선택한 출처와 검증

1. 공유 `LabLease`를 잡고 대상 `ai-ops-lab/lab-api`의 Deployment·Pod UID, 이미지 digest, 설정 해시와 관측 창을 고정한다. 한 실습 아이템만 생성한다. 두 쌍의 조회까지로 제한하며, 각 GET 직전·직후의 앱 `cache_operation_total{endpoint="get_item"}` 값, HTTP 상태와 요청 시각을 소유자 전용 원본에 기록한다. 현재 `check_read()`는 기대 outcome 증가와 error/unavailable 불증가를 확인하지만 다른 outcome의 동시 증가까지 배제하지는 않는다. 새 검증에서는 **각 조회의 기대 outcome만 정확히 1 증가**하고 다른 모든 outcome은 증가하지 않아야 한다. 최초 미출현 counter는 0으로 저장하지 않고 `absent→1`로 확인한다.
2. 같은 Pod UID에서 해당 관측 창의 앱 로그를 **크기·줄 수·시간을 제한해** 별도로 읽는다. `url-shortener/app/api/routes/item.py`가 기록하는 `cache_miss endpoint=get_item item_id=...` 및 `cache_hit endpoint=get_item item_id=...`의 고정 형식만 파싱한다. 원문 전체를 모델 view나 Git으로 옮기지 않는다. 로그의 아이템 ID가 비공개 소유 기록의 ID와 같고, 각 요청 구간에 기대 outcome 로그가 정확히 하나 있으며, HTTP 200·counter 증가·UTC 순서·고정 UID가 일치해야 한다. 로그가 누락·잘림·중복되거나 다른 요청과 counter가 섞이면 **순서 증거를 만들지 않는다.** `LabLease`만으로 트래픽이 완전히 격리됐다고 가정하지 않는다.
3. 실습 실행 결과·bounded 로그 조각·수집 snapshot/audit 각각의 해시와 대상 UID를 **비공개 검증 기록**에 결합한다. 앱이 `miss`를 기록한 것은 관측 결과이며, 실습기의 `cold` 명령·캐시 키 삭제·시나리오 ID·원인/정상 판정은 채점용으로만 보존한다. snapshot과 요청 기록의 대상·시각이 달라지면 결합을 거부한다. `check_read()` 결과만으로 별도 출처 검증을 통과했다고 취급하지 않는다.
4. 모델에 보낼 새 view에는 고정된 `probe-1` 별칭과 최대 네 단계의 **순번, `miss`/`hit`, HTTP 200 여부, 같은 아이템 여부**만 둔다. 필요하면 관측 창의 UTC 시각만 포함한다. 원본 아이템 ID, URL, 응답 본문, 로그 문장, 요청 헤더, Redis 키/주소, 주입 명령, 실습 정답, Pod/Deployment UID는 보내지 않는다. 이 요약은 `ev-view-*` 하나로 인용할 수 있어야 하며 출처 해시·원본 대응표는 로컬 비공개 기록에 남긴다. 모델은 “이 한 아이템의 관측된 miss→hit가 캐시 동작과 일치한다”까지만 설명할 수 있고 자연 TTL 만료나 전체 서비스 정상은 확정할 수 없다.

### 모델에 허용할 구조 예시

아래는 **스키마 예시**이며 실제 수집·전송 결과가 아니다. `probe-1`은 원본 ID와 무관한 고정 별칭이다.

```json
{
  "evidence_id": "ev-view-009",
  "kind": "cache_probe_sequence",
  "source_status": "available",
  "payload": {
    "probe_alias": "probe-1",
    "same_item": true,
    "reads": [
      {"order": 1, "cache_outcome": "miss", "http_status": 200},
      {"order": 2, "cache_outcome": "hit", "http_status": 200}
    ]
  }
}
```

## 버전·배포 경계

- 기존 `captured-practice-projection-v1`과 CI용 `v2`의 허용 필드·해시·저장 보고서는 변경하지 않는다. 특히 `model_comparison.py`의 `implementation_hash()`는 구 코드 파일을 결합하므로 해당 파일을 수정하면 보존된 일회 비교를 `show`로 다시 읽을 수 없어진다. 새 계약·전송 미리보기·저장소는 **별도 버전/모듈과 별도 비공개 저장 루트**로 먼저 구현한다. 새 정책 식별자는 예를 들어 `captured-practice-projection-v3`이며 v2 CI의 확장이 아니라 **v1 + 검증된 실습 조회 증거**로 정의한다. 기존 버전을 v3로 암묵 변환하지 않는다.
- v3 결합 해시에는 기존 snapshot bundle/audit·대상 identity 외에 **조회 검증 기록의 해시**, 새 view·원본 대응표·프롬프트/스키마·전송 코드 해시를 넣는다. 실습 결과 파일이 있다는 이유만으로 `available`을 만들지 않는다. 검증 실패면 `missing`과 고정 사유를 남기거나 v3 준비를 중지하며, 근거가 없는 순서를 모델이 추정하게 하지 않는다.
- 첫 구현은 로컬 오프라인 생성과 mock 전송까지만 한다. 새 실제 S2 한 건의 출처·원복을 확인한 뒤 전체 전송 미리보기를 만들고 **그 입력별 승인**을 받아야 외부 모델을 호출할 수 있다. 이전 S2 일회 승인과 중지된 캠페인 승인은 재사용하지 않는다. 두 요청·예산·보관·계정 데이터 공유 조건은 기존 [M1 정책](B6-model-data-policy.md)을 유지한다.
- 현재 `ContinuousCIBridge`는 검증된 CI 이력만 자동 접수한다. 따라서 로컬 v3 성공만으로 배포 경로의 정상 사례를 통과 처리하지 않는다. 후속 배포에는 임의 파일/HTTP 제출을 받지 않는 **실습 전용 검증된 probe 접수 경로**와 v3 정책 선택, 중복 방지·읽기 권한·캠페인 결합·중지/만료 검사를 별도로 설계·검증한다. 운영 대상은 허용하지 않는다.

## 구현 순서와 통과 기준

| 순서 | 구현·검증할 파일 | 통과 기준 |
|---|---|---|
| 1 | `src/ai_ops/b6_scenarios.py`와 별도 bounded 앱 로그 추출기 | 같은 item·Pod UID·요청 창의 로그, HTTP 200, 한 outcome만 +1, 순서를 모두 검증. 실패·동시 요청·로그 누락 시 순서 증거 없음. 실습 원본과 정답은 비공개 유지 |
| 2 | 새 `src/ai_ops/model_sequence_projection.py` 계약·exporter | 고정 별칭·최대 4단계·허용 enum만 view에 남기고 출처 해시를 별도 결합. ID/URL/로그/주입 명령 sentinel, 시간·UID·해시 변조를 거부. v1/v2 직렬화와 보존 비교 조회는 그대로 통과 |
| 3 | 새 일회 비교·프롬프트/보고서 검증 경로와 `src/ai_ops/model_policy.py`의 명시적 버전 선택 | 모델이 순서 증거 ID만 인용하고 한 아이템 범위를 넘지 않도록 검사. mock에서 miss→hit와 누락/섞임을 분리. 기존 저장소의 승인·실사용 장부를 덮어쓰지 않음 |
| 4 | 새 S2 한 건의 실측·미리보기·의미 채점 | 수집·원복을 먼저 확인하고, 미리보기/결합 해시를 검토한다. 실제 API 전송은 새 사용자 승인 후 1회만. [사전 의미 기준](B6-model-semantic-criteria-20261026.md)의 필수 설명과 금지 주장을 그대로 적용 |
| 5 | 별도 실습 자동 접수·배포 검증 | CI 전용 기존 경로와 구분해 신뢰된 probe만 접수. 캠페인/이미지/Secret 승인·예산·중지/만료·중복·실패 시 부분 보고서를 대조. 운영 앱·다른 namespace 읽기/쓰기 거부 |

**중단 조건:** 로그나 counter의 출처가 불명확함, 다른 요청의 지표 증가, 개별 아이템 연결 실패, UID/이미지 변경, 원본 제거 실패, 민감 필드 유출, 요청 크기·예산 초과 중 하나라도 있으면 그 실행을 정상 모델 평가로 채점하지 않는다. 추가 유료 호출로 실패를 덮지 않는다.
