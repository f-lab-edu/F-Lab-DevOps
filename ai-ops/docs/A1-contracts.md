# A1 조사 데이터 계약

- 확인일: 2026-09-28
- 코드 기준: `e4c51585bbc449f444527ed3c0614aac4b4b5700`
- 의존성: 별도 Python 패키지, Pydantic `2.13.5`와 필요한 4개 transitive dependency를 [잠금 파일](../requirements.lock)에 고정. 이 버전은 기존 앱의 잠금 파일과 일치한다.
- 구현: [contracts.py](../src/ai_ops/contracts.py), [대상 설정 예제](../config/targets.example.json), [JSON Schema](../schemas/), [계약 시험](../tests/test_input_contract.py), [CI](../../.github/workflows/ai-ops-ci.yml).

## 계약 경계

| 계약 | 주요 필드·검증 | 생성 주체 |
|---|---|---|
| TargetRegistry | schema version 1, 정확한 cluster/namespace/workload/service/environment 튜플, 중복 거부 | 신뢰된 로컬 설정 파일 |
| IncidentRequest | 내부 incident ID, 재전송 request ID, manual_replay, 등록 대상, 질문, UTC 24시간 이내 window, bundle hash, 요청 시각 | `create_incident_request`가 incident ID 생성. 재전송 처리 자체는 A3 |
| EvidenceItem/Bundle | 종류·출처·시각·상태·provenance, 누락 사유, payload/hash, 중복 ID·대상·bundle hash 검사 | A1은 계약과 생성 함수 제공. A2가 [로컬 파일 정규화](A2-evidence.md)를 구현 |
| AnalysisReport | incident/run/input 연계, 진단·현재 상태 구분, 사실·가설·부족 증거·추가 확인·미실행 대응·실행 메타데이터 | A3/A5의 조사 엔진이 생성 예정 |

모든 최상위 자료와 evidence 항목은 `schema_version=1`이다. 허용되지 않은 추가 필드와 알 수 없는 version을 거부한다. 시간은 `Z` 또는 `+00:00`을 명시한 UTC ISO 8601로 표현한다. 최대 window는 24시간, 질문은 최대 1000자다. 설정 파일은 32KiB, 단일 available payload는 64KiB, bundle은 4MiB 상한이다. 상태가 available이면 내용/hash/관측 시각이 필요하고, 그 밖의 상태는 누락 사유를 적으며 내용/hash를 넣지 않는다. 숫자 0은 `{"value": 0}`처럼 실제 값으로 표현한다.

현재 A1 계약은 `+09:00` 입력을 UTC로 변환하지 않고 거부하며, KST로 보여주는 출력도 없다. A2 로더가 원본 출처 시각의 명시적 오프셋을 UTC로 정규화한다. 사용자용 시각의 KST 표시는 A3에서 구현하도록 통합 구현 계획의 11·12·14절과 AI 작업 리스트에 기록했다.

`source_ref`의 상대 POSIX 형식·상위 경로·절대 경로를 검사한다. 실제 파일이 bundle root 안에 있는지, symlink가 없는지, 파일 크기를 읽기 전에 제한하는 일은 A2 로더가 담당한다. A1 구조 검사만으로는 입력 내용의 비밀값 제거를 보증하지 않는다. A2는 질문·허용 payload 필드의 알려진 비밀값 패턴을 마스킹하며, 실제 캡처 자료의 공개 적합성은 사람이 별도 검토해야 한다.

외부 요청과 bundle JSON은 `parse_incident_request_json`/`parse_evidence_bundle_json`으로 먼저 byte 크기를 제한한 뒤 Pydantic으로 파싱한다. Pydantic의 `model_copy(update=...)`는 update 값을 검증하지 않으므로, 외부 값으로 계약을 바꿀 때 사용하지 않는다. 구조를 통과한 `IncidentRequest`도 `validate_request_bundle`에서 대상 등록·bundle의 대상·window·hash를 다시 대조한다. ID 형식 검사는 발급 주체·재전송 멱등성·보고서의 증거 의미를 증명하지 않는다. 그 상태 관리와 보고서 근거 검증은 A3/A6에서 구현한다.

## 검증 범위

- 로컬 계약 단위 시험: 정상 왕복, 잘못된 ID/version/대상/시간/질문, 누락과 0, hash·중복·크기·출처, 보고서 형식.
- JSON Schema는 Pydantic 모델에서 생성하고 시험에서 현재 파일과 동일한지 확인한다. Python 교차 필드 검사는 JSON Schema 하나만으로 표현되지 않는다.
- A1 CI는 AI 패키지·workflow 변경에서 잠금 의존성을 설치하고 단위 시험과 schema 재생성을 확인한다. 외부 모델·Kubernetes·배포 credential은 필요 없다.
- 로컬 검증은 Codex 번들 Python 3.12.14/Pydantic 2.13.5로 수행했다. CI 실행과 다른 Python 3.11 설치 검증은 별도다.

A2는 로컬 저장 파일을 읽어 이 계약에 맞는 증거와 개발용 합성 fixture 8건을 준비했다. 다음 A3에서 사건 저장·CLI·규칙 기반 보고서를 연결한다. 현재 예제 대상은 실제 EKS 자격증명이나 실환경 연결을 의미하지 않는다.
