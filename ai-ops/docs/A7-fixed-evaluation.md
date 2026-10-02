# A7 고정 평가·로컬 데모 진행 기록

- 확인일: 2026-10-02 KST
- 상태: **진행 중**. 합성 holdout 16건 고정, 규칙 비교군 1회 실행·자동 채점 완료. 고정 증거 AI 요약 비교군은 모의 응답·사건 재조회 시험을 통과했다. 실제 AI 유료 반복·전체 수동 의미 채점·데모·원격 CI 확인은 미완료.
- 범위: 저장된 합성 증거의 로컬 조사 평가. 라이브 EKS 장애·복구 성능 또는 MTTR 평가가 아니다.

## 입력과 정답의 분리

[`evals/fixtures/holdout/`](../evals/fixtures/holdout/)에 사건 ID가 의미를 드러내지 않는 `case-01`~`case-16`을 만들었다. S1/S3/S4/S10 장애·배포 사건 각 2건, S2/S6/S9 정상 각 2건, S5 정보 부족 2건이다. 원본 날짜·값·문장·해시는 개발 fixture와 다르다. 원본 생성기는 [`build_holdout_fixtures.py`](../scripts/build_holdout_fixtures.py)이고, [`suite.json`](../evals/fixtures/holdout/suite.json)에 정규화 입력 hash와 manifest hash를 고정했다.

정답과 의미 채점 기준은 [`evals/rubrics/holdout/scenarios.json`](../evals/rubrics/holdout/scenarios.json)에 따로 둔다. [`run_holdout.py`](../scripts/run_holdout.py)는 이 파일을 읽지 않고 입력·해시만 읽는다. [`score_holdout.py`](../scripts/score_holdout.py)가 실행 종료 후 정답표를 읽는다. 평가 데이터를 작성한 사람이 개발 데이터를 알고 있었으므로 **독립 평가자에 의한 블라인드 평가라고 주장하지 않는다**. 고정 입력을 보고 엔진이나 규칙을 바꾸면 이 16건은 새 최종 평가로 재사용하지 않는다.

- suite ID: `a7-holdout-v1`
- suite SHA-256: `83c5ff59a4014ceaa98cda3260752544d7d371b2a01d0f8c1d2418e47913de24`
- 실행 시 모델·프롬프트·도구·런북 파일 hash와 각 입력 hash를 결과에 함께 기록한다.

## 현재 확인 결과

규칙 비교군은 16건 모두 `completed`로 저장됐다. 첫 실행의 자동 채점은 다음과 같다. 실행 기록은 저장소 밖의 개인 디렉터리에 두고 공개 문서에는 집계만 남겼다.

| 검사 | 규칙 비교군 1회 | 해석 |
|---|---:|---|
| 정상 사례의 장애 오탐 | 0/6 | 현재 합성 6건에서만 확인 |
| 정보 부족 판단 유보 | 2/2 | 누락을 정상값 0으로 해석하지 않음 |
| S10 복구 상태 구분 | 2/2 | 실패 당시와 롤백 이후를 구분 |
| 인용 ID가 available 증거에 존재 | 16/16 | 의미상 근거 적합도와 별개 |
| 평가 기준의 필수 근거를 인용 | 12/16 | S1 파싱 오류·S4 두 건·S2 TTL 사례에서 빠짐 |
| 기대 진단 상태 일치 | 14/16 | S4 DB 지연 두 건은 규칙이 원인 후보 대신 정보 부족으로 처리 |
| Top-1 의미 정확도·허위 주장 | 미채점 | 보고서 문장과 원본을 수동 검토해야 함 |

규칙의 S4 판단은 서버 원인을 섣불리 확정하지 않는 장점이 있지만, 합성 로그에 있는 ORM mapping·client pool 대기 신호까지 원인 후보로 올리지는 못했다. 이 결과를 보고 규칙을 수정하면 holdout 재평가가 오염되므로, 이번 평가에서는 그대로 유지한다. 단일 로컬 실행의 중앙값은 약 4ms이며 사람의 검토 시간과 데이터 준비 시간은 포함되지 않는다.

정답표를 만든 동일 작업자의 **비독립 수동 확인**에서는 엄격한 Top-1 세부 원인 기준에 맞는 규칙 보고서가 S10 두 건, 즉 2/8이었다. S1은 연결 오류와 payload 파싱 오류를, S3은 태그 부재와 레지스트리 401을 구분하지 못했다. S4 두 건은 가설이 없었다. 이는 블라인드 채점이나 실제 AI 성능 점수가 아니므로 위 자동 채점표의 미채점 상태를 대체하지 않는다.

로컬 재조회 데모는 저장된 규칙 결과에서 `case-07`(배포 실패 뒤 롤백 복구), `case-05`(DB 앱 측 지연은 보지만 세부 원인은 놓친 한계), `case-15`(지표 누락·로그 접근 거부로 판단 유보)를 골라 CLI의 `show --format markdown`으로 확인했다. 세 보고서 모두 저장 후 다시 열렸고 사람에게 보이는 시각은 KST였다. 이는 기존 작업 디렉터리의 규칙 결과 데모이며 clean checkout과 실제 AI 보고서 데모는 아직 남았다.

실제 AI 평가의 첫 시도는 **모델 호출 전 `daily_budget_limit`**로 `partial` 처리됐다. 이 결과는 진단 품질 표본으로 세지 않는다. 평가 실행기는 다음 UTC 날짜에 예산이 확보되면 새 시도 ID로 같은 반복 순서를 다시 실행할 수 있고, 사전 예산 확인 후 새 사건을 만들지 않고 중지하도록 보완했다. 건당 $0.10·UTC 일일 $1 제한을 늘리거나 우회하지 않는다. 16개 입력 × 3회는 최대 48회 평가 실행이며 독립 사건 48건이 아니다.

## 남은 작업과 판정 기준

1. AI 도구 탐색 모드를 고정 입력 16건에서 각각 3회 실행한다. 현재 예산으로는 하루에 전부 끝낼 수 없으므로 결과 파일을 날짜를 넘겨 이어 쓴다. `partial`·`failed` 시도는 성공한 AI 진단으로 채점하지 않는다.
2. 수동 분석 비교군과 검토 표를 준비한다. 고정 증거 AI 요약 코드는 [`fixed_summary.py`](../src/ai_ops/fixed_summary.py)에 구현해 16개 입력의 크기·모의 응답·저장 재조회를 검사했지만 유료 호출은 아직 하지 않았다. 규칙·고정 증거 AI는 전체 검증 묶음, 도구 탐색 AI는 실제 조회한 항목과 런북만 보므로 이 정보 범위 차이를 결과에 명시한다.
3. 별도 수동 검토에서 Top-1 원인·근거 문장의 의미상 지지·금지 주장을 채점한다. 채점기는 완료된 실행마다 검토 항목을 내보내며 `--review`로 받은 사람의 판단만 합산한다. 첫 실행 장애 8건이 모두 검토되기 전에는 Top-1 비율을, 전체 실행이 완료·검토되기 전에는 근거 의미 정확도를 확정하지 않는다. 자동 `evidence_id` 존재 검사 100%만으로 근거 정확도 100%라고 쓰지 않는다.
4. 첫 실행 기준 Top-1 7/8 이상, 정상 오탐 0/6, 판단 유보 2/2, S10 복구 구분 2/2를 확인하고 반복 변동·시간·비용·오답 사례를 함께 공개한다. 미달이면 개선 작업을 등록하고 A 완료로 표시하지 않는다.
5. clean checkout 설치·rules/AI 결과 재조회와 성공·오답/한계·판단 유보 데모, 기존 앱·AI CI의 원격 실행 결과를 확인한다. A5의 HolmesGPT 동등 조건 비교도 별도 미완료다.

## 로컬 재현

고정 자료 검증과 규칙 비교군은 외부 모델 호출이 없다. `AI_OPS_PYTHON`에는 Python 3.11 이상과 잠금 버전의 Pydantic 2.13.5가 설치된 실행 파일을 지정한다. 실행 저장소는 프로젝트 밖의 개인 경로를 사용한다.

```sh
PYTHONPATH=ai-ops/src "$AI_OPS_PYTHON" ai-ops/scripts/build_holdout_fixtures.py --check
PYTHONPATH=ai-ops/src "$AI_OPS_PYTHON" ai-ops/scripts/run_holdout.py --mode rules --store "$AI_OPS_EVAL_STORE"
PYTHONPATH=ai-ops/src "$AI_OPS_PYTHON" ai-ops/scripts/score_holdout.py --runs "$AI_OPS_EVAL_STORE/a7-rules-results.json"
```

실제 AI 도구 탐색 평가는 같은 실행기를 `--mode ai --repeats 3`으로, 고정 증거 AI 요약은 `--mode fixed --repeats 3`으로 사용한다. 두 모드는 개인 저장소의 API 키를 읽고 유료 호출하므로 수동으로 실행한다. 예산 장부와 사건 기록은 기존 개인 저장소를 그대로 사용해야 일일 제한이 유지된다. 채점 JSON의 `manual_review` 항목에는 원인 후보·사실·정답 기준과 비어 있는 검토 필드가 들어 있다. 사람이 해당 필드를 확인하기 전에는 Top-1과 의미상 근거 정확도를 확정하지 않는다.

검토 결과는 저장소 밖의 JSON 파일에 보관한다. `mode`는 해당 실행의 `rules`·`fixed`·`ai` 중 하나여야 한다. 완료된 각 `(case_id, repeat)`에 대해 `top1_correct`는 장애만 참/거짓으로 적고 정상·정보 부족은 `null`로 적는다. 인용 문장이 실제 증거에 의해 지지되는지와 금지 주장이 있는지 역시 원본을 읽고 판단한다. `note`에는 판정 근거를 기록한다. 일부만 검토해도 진행률은 나오지만 점수는 필요한 대상이 모두 검토될 때까지 대기 상태다.

```json
{
  "schema_version": 1,
  "suite_hash": "83c5ff59a4014ceaa98cda3260752544d7d371b2a01d0f8c1d2418e47913de24",
  "mode": "rules",
  "reviews": [
    {
      "case_id": "case-01", "repeat": 1,
      "top1_correct": false,
      "evidence_semantically_supported": true,
      "forbidden_claim_present": false,
      "reviewer": "검토자 이름",
      "note": "보고서의 원인 후보와 ev-h01-1·ev-h01-2 원문을 대조한 이유"
    }
  ]
}
```

```sh
PYTHONPATH=ai-ops/src "$AI_OPS_PYTHON" ai-ops/scripts/score_holdout.py \
  --runs "$AI_OPS_EVAL_STORE/a7-rules-results.json" \
  --review "$AI_OPS_EVAL_STORE/a7-rules-review.json" \
  --output "$AI_OPS_EVAL_STORE/a7-rules-score.json"
```
