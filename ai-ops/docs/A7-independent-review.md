# A7 v3 독립 의미 검토 인계

## 목적과 현재 상태

새 v3 합성 사건 16건의 첫 반복 보고서 두 방식, 총 32건을 별도 검토자가 원본 증거와 대조한다. 확인 항목은 장애 8건의 첫 원인 후보, 모든 보고서의 근거·유보 진술, 정답표에 적힌 금지 주장이다. **독립 판정은 아직 받지 않았다.** 평가 자료·프롬프트·기존 자체 검토를 작성한 Codex의 판정은 독립 결과로 합산하지 않는다.

검토용 HTML은 로컬 전용 `ai-ops/site/a7-review.html`이다. Git에 포함하지 않는다. 두 방식의 이름을 사건마다 무작위 A/B로 바꾸고, 대응표 `a7-v3-blind-review-map.json`은 저장소 밖 개인 디렉터리에 권한 `0600`으로 둔다. 화면에는 합성 증거·두 보고서·고정된 세부 판정 기준이 들어 있다. **방식 이름만 숨긴 검토**이며 정답 기준과 사건 유형은 제공하므로 완전한 정답 블라인드 검증은 아니다. 보고서 문체로 방식을 추측할 수도 있다. v3 자료 작성자가 기존 오답을 알고 있었다는 한계도 그대로 유지한다.

## 검토자에게 전달할 것

별도 검토자에게는 생성된 `a7-review.html` 하나만 전달한다. 기존 A7 결과 화면, 방식 대응표, `self-review`·`self-score` 파일과 API 키는 전달하지 않는다. HTML은 외부 네트워크나 모델을 호출하지 않고 브라우저에서 직접 열 수 있다. HTML 자체에는 평가용 합성 로그가 들어 있으므로 공개 웹사이트에 게시하지 않는다.

검토자는 화면에서 이름 또는 식별자를 입력하고, 자료 작성에 관여하지 않았다는 항목을 확인한다. 각 사건의 A·B를 각각 읽고 다음을 기록한다.

1. 장애 사건만 첫 원인 후보가 제시된 세부 기준과 일치하는지 판정한다. 정상·증거 부족 사건의 Top-1은 적용하지 않는다.
2. 모든 보고서에서 사실·가설의 인용 문장이 증거 원문과 의미상 맞는지 판정한다. 증거 부족 사건에서는 없는 값을 만들어내지 않고 판단을 유보했는지도 본다. 인용 ID의 존재만으로 `맞음`을 고르지 않는다.
3. 금지 주장이 **있으면** `있음`, 없으면 `없음`을 고른다. `있음`은 결함 판정이다.
4. `판정 이유`에 대조한 증거 ID와 보고서 문장을 적는다. 화면의 완료 카운터가 32/32가 되면 `완료 판정 JSON 저장`으로 파일을 내보낸다. 중간에는 초안 JSON을 저장하고 다시 열 수 있다.

검토자가 작성한 완료 JSON을 받기 전에는 점수를 계산하지 않는다. A/B 방식 공개와 기존 자체 검토와의 차이 비교는 **독립 판정을 저장한 뒤**에만 한다. 첫 반복의 Top-1 판정은 장애 8건의 각 방식별 점수로 합산할 수 있지만, 전체 48회 근거 의미 정확도는 2·3회차까지 검토하기 전에는 확정하지 않는다.

## 생성과 판정 반영

`AI_OPS_EVAL_STORE`는 기존 개인 평가 저장소를 가리키고, `AI_OPS_PYTHON`은 잠금 의존성을 설치한 Python을 가리킨다. 아래 생성 명령은 기존 보고서를 읽을 뿐 모델 호출이나 유료 API 요청을 하지 않는다.

```sh
PYTHONPATH=ai-ops/src "$AI_OPS_PYTHON" ai-ops/scripts/prepare_blind_review.py prepare \
  --fixed-packet "$AI_OPS_EVAL_STORE/a7-v3-fixed-review-packet.json" \
  --ai-packet "$AI_OPS_EVAL_STORE/a7-v3-ai-review-packet.json" \
  --html ai-ops/site/a7-review.html \
  --mapping "$AI_OPS_EVAL_STORE/a7-v3-blind-review-map.json"
```

재생성할 때마다 A/B 배정과 패킷 ID가 바뀐다. 생성기는 기존 HTML·대응표를 덮어쓰지 않으므로 새로 만들 때는 두 파일 모두 다른 이름을 지정해야 한다. 검토자가 이미 파일을 작성했다면 같은 패킷의 대응표를 보존한다. 완성된 검토 파일은 저장소 밖 개인 저장소의 `a7-v3-independent-review.json`으로 옮긴 뒤 아래처럼 변환한다. 변환기는 패킷 ID·32개 판정·독립 검토 확인·필수 이유를 검사하고, 각 방식의 기존 채점기 입력 형식으로 분리한다.

```sh
PYTHONPATH=ai-ops/src "$AI_OPS_PYTHON" ai-ops/scripts/prepare_blind_review.py import \
  --review "$AI_OPS_EVAL_STORE/a7-v3-independent-review.json" \
  --mapping "$AI_OPS_EVAL_STORE/a7-v3-blind-review-map.json" \
  --fixed-template "$AI_OPS_EVAL_STORE/a7-v3-fixed-review-template.json" \
  --ai-template "$AI_OPS_EVAL_STORE/a7-v3-ai-review-template.json" \
  --fixed-output "$AI_OPS_EVAL_STORE/a7-v3-fixed-independent-review.json" \
  --ai-output "$AI_OPS_EVAL_STORE/a7-v3-ai-independent-review.json"
```

변환 후 [`score_holdout.py`](../scripts/score_holdout.py)에 각 방식의 결과 파일과 독립 검토표를 각각 입력한다. 두 방식의 첫 반복만 검토했으므로 채점기의 근거 의미 **전체 48회 비율은 계속 대기**로 둔다. 독립 판정과 기존 자체 검토의 차이를 사건별로 확인하고, 이 결과를 보고 프롬프트나 평가 자료를 바꾸면 v3는 개발 자료로 재분류한다.
