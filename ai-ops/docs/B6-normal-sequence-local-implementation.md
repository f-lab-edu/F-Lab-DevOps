# B-06 정상 조회 순서의 로컬 계약·모의 검증

상태: **2026-10-26 KST 로컬 계약과 모의 검증 완료.** [순서 증거 설계](B6-normal-sequence-evidence-design.md)의 v3 계약·제한 로그 대조·정제·미전송 요청 본문·같은 입력 모의 보고서를 구현했다. 실제 실습 수집, 상시 worker 연결, 외부 모델 전송은 수행하지 않았다. B-06·B단계 완료와 C단계 진입은 보류한다.

## 구현한 처리 흐름

실습 조회의 비공개 기록과 원본 snapshot을 입력받아 **HTTP 200 → 같은 아이템의 앱 로그 → 해당 outcome counter만 1 증가 → 다음 조회와 순서·counter 연속성**을 확인한다. snapshot에서 v1 projection을 다시 만들어 입력과 대조하고, Pod의 Deployment 소유 관계·이미지 해시와 source bundle/audit/config 해시도 확인한다. 검증된 miss→hit 한 쌍 또는 두 쌍만 v3 view의 `cache_probe_sequence`에 추가한다.

모델용 view에는 고정 `probe-1` 별칭, 같은 아이템이라는 검증 결과, 순번·hit/miss·HTTP 상태만 포함한다. 원본 item ID·Pod UID·로그·원본 대응표는 비공개 결합 객체에 남고 요청 본문에 포함되지 않는다. 원문 로그는 64KiB·256줄 이내로 제한하고 Kubernetes 시각과 앱의 고정 로그 형식을 대조한다. 누락·중복·순서 뒤섞임·잘림·다른 Pod·counter 혼입·해시 불일치는 `ValueError`로 준비를 중지한다.

| 파일 (`ai-ops/` 기준) | 역할 |
|---|---|
| `src/ai_ops/model_sequence_projection.py` | `ProbeRead/ProbeRecord` 비공개 계약, 제한 원문 로그 대조, 같은 snapshot·Pod·이미지 검증, `SequenceModelView/SequenceProjection` v3 정제와 해시 결합, 재파싱 |
| `src/ai_ops/projected_sequence_model_engine.py` | v3의 토큰 계산·생성 **본문만 구성**, 같은 view의 규칙 모의 보고서, 보고서 해시·available 근거 인용 검사. 네트워크 호출 함수 없음 |
| `tests/test_model_sequence_projection.py` | 정상 정제, 원본 노출 방지, 변조·다른 대상·counter 혼입·원문 로그 누락/중복/잘림·순서/해시 위조 거부, 네트워크 없는 모의 생성·보고서 인용 검사 7개 |

`ProbeRecord`의 해시·타입 자체는 외부 출처 인증이 아니다. 현재 함수는 **호출자가 제공한** snapshot·HTTP/counter 기록·원문 로그·Pod UID·잘림 여부를 검증한다. 실제 Kubernetes 조회가 같은 Pod에서 수행됐고 제한 때문에 로그가 잘리지 않았는지를 보장하는 수집 연결은 다음 단계다. 임의 JSON 파일을 실제 관측으로 신뢰하는 CLI나 자동 접수 경로는 추가하지 않았다.

## 검증 결과와 기존 자료 보존

- 전체 Python **464개 시험**이 통과했다. 이후 추가한 “내부 해시는 맞지만 snapshot과 다른 v1 view” 차단 시험 1개를 포함해 신규 모듈 **7개 시험**을 다시 통과했다. 최초 전체 실행의 8개 오류는 로컬 서버 bind에 대한 sandbox 제한이었고, 필요한 권한으로 전체를 다시 실행해 통과했다.
- 기존 v1·v2와 신규 v3 관련 시험, Python 문법 검사, fixture 일치 검사, schema 재생성 차이 없음, Helm·배포 정적 검사가 통과했다. Docker 이미지 빌드·원격 CI·배포 검증 결과로 표현하지 않는다.
- 기존 v1/v2 정제·일회 비교 코드의 결합 해시 대상 파일을 수정하지 않았다. 이전 `cmp-a221f415e489f08dcae0941d7586be9c`가 `needs_review` 상태와 기존 input hash를 유지하며 다시 조회되는 것을 확인했다.
- 외부 요청 **0회**, 추가 API 예약·비용 **0**이다. 모의 보고서는 고정된 규칙 결과이며 실제 모델의 정상 설명 품질을 입증하지 않는다.

## 다음 연결 작업

2026-10-26 후속으로 [GET·같은 Pod 로그 수집 연결](B6-normal-sequence-capture.md)을 구현하고 실습 한 건의 네 조회·원복을 확인했다. 아래 문단의 실행기 미연결 상태는 로컬 계약 구현 당시 기록이다. 현재 남은 연결은 저장 snapshot과 이번 조회 기록을 결합해 v3 정제 입력만 반출하는 exporter다.

실습 실행기에 각 GET의 시작/종료·응답 item ID·counter 기록과 **같은 Pod UID의 bounded 로그 조회**를 연결해야 한다. 기존 실행기는 요청 뒤 시각과 counter만 기록하므로 현재 결과 파일을 새 계약에 자동 변환하지 않는다. 수집기가 제공할 로그 prefix·시각 형식·잘림 판정도 실제 소스로 확인한다. 출처 검증과 원복을 마친 새 실습 입력으로 v3 view를 만든 뒤 비공개 미리보기를 준비한다.

v3는 아직 `model_policy.py`·gateway·상시 CI 전달기에 등록하지 않았다. 실제 전송에는 v3용 비공개 저장·입력별 승인·예산 장부가 필요하고, 상시 연결은 별도 캠페인·배포 검증이 필요하다. 이전 정상 S2 일회 승인과 중지된 캠페인을 재사용하지 않는다. 다음 작업 권장 추론 깊이는 **GPT-6 SOL high**다.
