# A2 저장 증거 처리와 개발용 재현 자료

- 구현일: 2026-09-29
- 범위: 로컬에 저장된 manifest/증거 파일을 읽어 A1 `EvidenceBundle`로 정규화. Kubernetes·Prometheus·외부 모델 호출은 없다.
- 구현: [evidence.py](../src/ai_ops/evidence.py), [합성 fixture 생성기](../scripts/build_dev_fixtures.py), [A2 시험](../tests/test_evidence_processing.py), [개발 입력](../evals/fixtures/dev/), [별도 검토 기준](../evals/rubrics/dev/scenarios.md).

## 입력과 처리 순서

`load_evidence_bundle(bundle_root, registry)`는 루트의 `manifest.json`(최대 256 KiB)을 읽고 schema version·등록 대상·24시간 이내 UTC window·증거 ID 중복을 검사한다. manifest 항목에는 `evidence_id`, `kind`, 상대 `source_ref`, 원본 파일 `raw_sha256`, 원본 `observed_at`·`collected_at`, `status`, 누락 사유, 항목별 provenance가 있다. `available` 항목은 파일과 해시·관측 시각을 요구하고, 그 외 상태는 원본 파일을 읽지 않으며 사유를 요구한다.

각 파일은 bundle 루트 FD에 상대적으로 열고 모든 경로 구성요소에서 symlink를 따르지 않는다. 상위 경로·절대 경로·비정규 경로, 일반 파일이 아닌 입력, 64 KiB 초과 파일, 전체 원본 4 MiB 초과, 원본 SHA-256 불일치를 거부한다. JSON 중복 키와 비유한 숫자도 거부한다. 로그는 `.log` UTF-8 파일, 나머지는 `.json` 파일이다. 원본 전체를 복사하지 않고 종류별 허용 필드만 추출해 정규화 payload를 만든 다음 내용 해시와 bundle 해시를 계산한다.

원본 시각은 `Z` 또는 `±HH:MM` 오프셋이 명시된 ISO 8601만 허용한다. `+09:00`과 `Z`가 같은 순간이면 동일한 UTC `observed_at` 및 metric sample 시각으로 정규화한다. payload의 `source_time`과 sample의 `source_at`·`source_offset`에 원본 문자열·오프셋을 남긴다. 관측 시각과 수집 시각을 구분하며, 누락된 시간대를 현재 시각으로 채우지 않는다. A1 `IncidentRequest`의 UTC 입력 제한은 그대로다. 사용자에게 KST로 표시하는 기능은 A3 범위다.

문자열의 알려진 credential URL, Bearer/JWT, 키·토큰·비밀번호 할당, AWS 키, private key 블록을 마스킹하고 범주·건수를 `redaction_summary`에 기록한다. `sanitize_question`과 `create_sanitized_incident_request`는 질문을 요청 계약에 넣기 전에 같은 규칙을 적용한다. 향후 도구 출력에도 `redact_text`를 적용한다. **패턴 마스킹은 완전한 비밀정보 탐지가 아니다.** 실제 캡처 자료를 저장하거나 외부 모델에 보내기 전에는 사람이 공개 적합성을 별도로 검토해야 한다. 원시 자료의 저장 위치·보존 기간은 A3에서 결정한다.

## 합성 사례와 검증

| 사례 | 입력에서 보존할 차이 |
|---|---|
| S1 캐시 오류 | `error=3`, 조회 오류 로그, event 권한 거부를 서로 다른 증거로 표현 |
| S2 정상 miss | `miss=5`, `error=0`, 캐시 사용 의도와 URL 설정 여부를 보존 |
| S3 잘못된 image tag | 배포 변경과 `ImagePullBackOff` event를 분리해 보존 |
| S4 DB 지연 | 앱 측 P99·표본 수와 실습 지연 문맥을 보존 |
| S5 정보 부족 | metric 누락·log 권한 거부를 값 0이나 정상으로 바꾸지 않음 |
| S6 정상 상태 | Ready·재시작 0·HTTP 오류 비율 0·요청량을 보존 |
| S9 정상 Primary 경로 | `consistency_primary=2`, 최근 쓰기 marker, `error=0`을 보존 |
| S10 롤백 후 복구 | 실패 당시 배포와 이후 정상 revision을 별개 증거로 보존 |

개발 입력은 `python3 ai-ops/scripts/build_dev_fixtures.py`로 재생성하고 `--check`로 저장 파일과 비교한다. 사람이 확인할 해석 기준은 fixture 루트 밖의 `evals/rubrics/dev/`에 둔다. 최종 평가 holdout은 아직 없다.

저장소 루트에서 Pydantic 2.13.5가 설치된 Python으로 다음을 실행한다.

```sh
PYTHONPATH=ai-ops/src python3 -m unittest discover -s ai-ops/tests -p 'test_*.py' -v
python3 ai-ops/scripts/build_dev_fixtures.py --check
```

시험은 여덟 개발 사례의 `EvidenceBundle` 재생, UTC 정렬, 값 0과 누락 구분, 마스킹 sentinel 비노출, 경로 탈출·symlink·해시·크기·등록 대상·잘못된 시각 거부를 확인한다. S7/S8/S11의 기능 검증과 최종 holdout은 후속 작업이다. 이는 로컬 합성 입력의 검증이며 실제 EKS 장애 탐지나 AI 원인 분석 결과가 아니다.
