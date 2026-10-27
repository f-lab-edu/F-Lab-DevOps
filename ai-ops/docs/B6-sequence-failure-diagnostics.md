# B-06 순서 비교의 실패 진단 보존

상태: **2026-10-26 진단 기능을 로컬 구현·모의 검증한 뒤, 별도 승인된 새 ID 한 건을 실행했다.** [기존 v3 한 건](B6-normal-sequence-live-failure.md)은 `projection_report_contract_rejected`로 끝났고 초안·검증 예외를 저장하지 않았다. 그 건의 정확한 실패 필드는 지금도 알 수 없다. 이 문서는 **이후 새로 준비하는 비교**에서 같은 진단 공백을 줄이는 방법이다.

## 처리 흐름

1. 새 `DiagnosticSequenceStore`가 기존 v3 정제 입력·계획·두 요청 본문에 진단 코드 버전을 묶는다. 저장 위치는 개인 전용 `sequence-diagnostic-comparisons/`이며 기존 `sequence-comparisons/`와 구분된다. 예전 실행 코드·기록을 변경하지 않아 과거 비교의 해시 검증과 조회를 유지한다.
2. 사용자는 새 ID와 전체 요청 미리보기를 확인하고 **새 입력별 승인**을 한다. 모델 전송 시에는 기존 로컬 UTC 일일 장부에서 건당 $0.10을 예약한 다음 토큰 계산과 생성을 각각 최대 한 번 실행한다. 전송 상태가 불명확하면 자동 재시도하지 않는다.
3. 생성된 JSON이 보고서 계약에 어긋나면 고정 코드와 허용된 필드 경로만 `failure_diagnostic`에 저장한다. 구조 오류의 원문·입력값·예외 메시지는 상태 출력에 넣지 않는다. `input_hash_mismatch`, `unavailable_evidence_reference`, `hypothesis_without_support`, `diagnosis_hypothesis_conflict`, `report_schema_invalid`을 구분한다. 분류할 수 없는 내부 예외는 `report_validation_internal_error`로 표시한다.
4. JSON 초안이 있고 직렬화 크기가 64 KiB 이하이면 SHA-256과 함께 로컬 0600 HMAC 서명 파일에 보존한다. 일반 CLI `show`와 `run` 출력은 초안을 제외한다. 원문 보존은 암호화가 아니며 같은 OS 사용자 권한과 개인 폴더 접근 제어에 의존한다. 본문과 전체 요청 미리보기는 **7일 뒤 `purge --apply`를 실행해야** 삭제된다. 자동 정리 스케줄러가 있다고 가정하지 않는다. 예약 장부는 기존 폴더에 남는다.

모델 응답이 완료되지 않았거나 JSON 파싱 전 거절된 경우에는 구조화 초안이 없으므로 원문을 새 저장소에 추가 보존하지 않는다. 이때는 기존의 고정 전송·응답 실패 사유와 확인 가능한 사용량만 남긴다. 이 진단은 형식·근거 참조의 실패를 구분하며 자연어 설명의 의미 정확성을 판정하지 않는다.

| 파일 (`ai-ops/` 기준) | 역할 |
|---|---|
| `src/ai_ops/sequence_diagnostics.py` | 새 버전 결합·고정 실패 분류·제한 초안 보존·안전한 요약 |
| `scripts/compare_sequence_diagnostics.py` | 새 비교의 준비·미리보기·승인·모의/실제 일회 실행·요약 조회·기한 정리 |
| `tests/test_sequence_diagnostics.py` | 잘못된 해시·허위 근거·근거 없는 가설·판정 충돌·스키마 오류, 비공개 출력, 일회 사용, 기존 기록과 공유 장부, 7일 정리 검증 |

`prepare --projection … --plan …`으로 새 비교를 만들고 `preview-html --id …`로 두 요청 전체를 확인한다. `approve`에는 새 결합 해시와 승인자를 지정한다. **실제** 실행은 기존 정책상 공급자 보관 인지와 계정의 선택적 데이터 공유 꺼짐 확인도 필요하며, 입력별 별도 승인 없이 `run-live`를 하지 않는다. `run-mock`은 검증용이고 실제 모델 결과를 대체하지 않는다. `show --id …`는 원문을 출력하지 않는다. `purge`는 기한 지난 비교를 조회하고, `purge --apply`가 해당 본문·미리보기를 정리한다.

이 작업만으로 정상 S2의 적극적 설명이나 B-06 완료를 주장하지 않는다. 실제 의미 평가는 새 비교의 입력·비용·코드 미리보기와 별도 승인을 거쳐 얻은 **새 보고서**로 수행해야 한다. 권장 추론 깊이: **GPT-6 SOL high**.

진단 기능 구현 단계 검증: 계약 위반 5종과 비밀값 비노출·일회 실행·예산 공유·7일 정리·CLI 안전 조회를 포함해 전체 Python **491개 시험**이 통과했다. 기존 비공개 비교 두 건도 당시 저장소에서 `failed`와 `needs_review` 상태로 각각 다시 조회됐다. **구현·모의 검증 단계**의 OpenAI API 호출·새 비용 예약은 0이었다. 이후 새 ID의 실제 전송은 아래 후속 기록과 [결과 문서](B6-normal-sequence-diagnostic-live-result.md)에 별도로 남긴다.

## 실제 v3 입력의 새 비교 준비 — 2026-10-26 KST

[이전 실패 비교](B6-normal-sequence-live-failure.md)의 **검증된 정제 입력과 같은 비용 계획**을 새 진단 버전으로 다시 결합했다. 이전 실행의 승인·초안·실패 상태를 복사하거나 수정하지 않았다. 입력의 `view_hash`는 동일하지만 새 실행 코드가 결합됐으므로 비교 ID와 결합 해시는 다르다.

| 항목 | 준비 결과 |
|---|---|
| 새 비교 ID | `cmp-319207e7d943ea0ba29b1c2e32d728a2` |
| 결합 SHA-256 | `319207e7d943ea0ba29b1c2e32d728a2796edc57d951b84733c48014abb5f203` |
| view SHA-256 | `082b29757099cdf763201b11bf0e27249b5ae79732093c74b60d90641bc0fe32` |
| 상태 | `prepared`, 승인·사용량 없음 |
| 요청 | 토큰 계산과 보고서 생성의 전체 본문을 미리보기에 고정. `gpt-6-sol` low, `store=false`, 최대 출력 1,024토큰 |
| 비용 계획 | 건당 최대 $0.10 예약, 기존 로컬 UTC 일일 $1 장부 공유. 현재 장부의 2026-10-26 예약은 기존 $0.20 그대로 |
| 보존 | 비공개 HTML 0600. 준비·미리보기 과정의 모델 호출과 새 예약 0 |

[비공개 전체 요청 미리보기](/Users/jounghyeon/.local/share/url-shortener-ai-ops/sequence-diagnostic-comparisons/preview-cmp-319207e7d943ea0ba29b1c2e32d728a2.html)를 보고 새 입력별 승인 여부를 결정한다. 준비 상태는 자동 전송을 켜지 않는다. 이전 v3 승인과 클러스터 캠페인 승인은 이 ID에 재사용할 수 없다. 미리보기 이후 실행 코드나 계획을 바꾸면 코드 결합 검사가 막으므로 다시 준비해야 한다.

위 문단은 준비 당시의 상태다. 이후 사용자가 이 한 건을 승인했고 [실제 결과](B6-normal-sequence-diagnostic-live-result.md)는 보고서 계약을 통과해 `needs_review`로 보존됐다. 따라서 현재 이 ID는 `prepared`가 아니며 재실행할 수 없다.
