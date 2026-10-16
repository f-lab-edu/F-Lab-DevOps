# 운영자 CLI 파일 경로 정책

2026-10-17 사용자 선택에 따라 CLI의 임의 파일 경로 접근을 제한했다. [path_policy.py](../src/ai_ops/path_policy.py)가 명시적 인자와 기본값을 모두 검사해 검증한 새 인자 객체만 실행부에 전달한다. 모델의 조회 도구 허용 목록과 별도로 운영자 CLI에도 경계를 적용한다.

## 허용하는 위치

| 용도 | 허용 폴더 |
|---|---|
| 설정·평가 입력 | `ai-ops/config`, `ai-ops/evals`, `ai-ops/runbooks`, 개인 저장 폴더, 추가 등록 폴더 |
| 컨테이너 설정·저장 | `/etc/ai-ops`, `/data/incidents` |
| 개인 저장·결과 | `~/.local/share/url-shortener-ai-ops`, 추가 등록 폴더 |
| 로컬 검토 화면 | `ai-ops/site`의 HTML. JSON 검토 결과도 해당 폴더 안에 저장 가능 |

일반 파일 입력은 JSON·CSV·HTML만 허용하며 실제 존재하는 일반 파일이어야 한다. bundle/config 폴더 인자는 실제 자료 폴더여야 한다. 컨테이너 설정 경로는 입력용이고 `/etc/ai-ops`나 프로젝트 설정/평가 자료에 결과·키 저장소를 만들 수 없다. 내부 함수에 이미 신뢰한 자료를 전달하는 Python API와 CLI 인자 경계는 구분한다.

허용 폴더 밖과 `..` 부모 탐색은 파일을 읽거나 저장소·예산 장부를 열기 전에 거부한다. 허용 폴더 자체의 symlink와 경계 밖을 가리키는 입력 링크를 거부한다. Kubernetes projected volume처럼 같은 설정 폴더 안에서 버전을 가리키는 입력 링크는 정규화한 내부 경로로 읽는다. 출력은 내부 링크라도 따라가지 않는다.

## 기존 개인 자료를 사용하는 방법

기존 자료를 기본 개인 저장 폴더의 `inputs/`·`evaluations/` 아래로 옮기거나, 별도의 전용 폴더 하나를 운영자 환경변수로 등록한다.

```sh
mkdir -m 700 "$HOME/ai-ops-files"
export AI_OPS_FILES_ROOT="$HOME/ai-ops-files"
```

등록 폴더는 절대 경로여야 하며 현재 사용자 소유이고 다른 사용자에게 쓰기가 허용되지 않아야 한다. 파일시스템 루트·홈 전체·공용 임시 폴더·symlink 루트는 허용하지 않는다. CLI에는 허용 범위를 넓히는 인자를 제공하지 않는다. 환경 설정은 운영자가 관리하며 모델 응답·증거·질문에서 받아 설정하지 않는다. 이 값은 허용 폴더를 추가할 뿐 기본 키/사건 저장소 위치를 자동으로 옮기지 않는다.

스크립트를 저장소에서 직접 실행할 때는 패키지를 설치하거나 `PYTHONPATH=ai-ops/src`를 지정한다. 예를 들어 추가 폴더에 준비한 CSV를 채점하려면 다음과 같이 실행한다.

```sh
PYTHONPATH=ai-ops/src python3 ai-ops/scripts/score_manual_baseline.py \
  --csv "$AI_OPS_FILES_ROOT/manual.csv" \
  --output "$AI_OPS_FILES_ROOT/manual-score.json"
```

## 출력과 평가 재개

일반 JSON/HTML 결과는 새 이름에만 저장한다. 기존 파일과 출력 symlink를 덮어쓰지 않는다. 두 채점 스크립트의 JSON은 0600 임시 파일에 완성·fsync한 뒤 대상이 없을 때만 원자적으로 게시한다. 기록 도중 실패해 불완전한 결과를 정상 결과 파일명으로 남기지 않는다.

`run_holdout.py --result`는 재개를 위해 `a7-이름-results.json` 형식만 예외적으로 받는다. 기존 결과가 있으면 원래의 suite/엔진/반복/프롬프트 등 실행 정보 검사를 통과해야 계속 기록한다. 기본 결과 파일 이름은 유지했다. `ai-budget.json`·`metadata.json`을 평가 재개 경로로 지정할 수 없다. 기존 결과가 필요한 일반 채점은 새 출력 이름을 선택한다.

## 검증과 미완료

실제 파일·CLI 시험으로 경계 밖·접두사 유사 폴더·부모 탐색·입력/출력 symlink·기본 인자 우회·덮어쓰기·허용 폴더 확장과 전체 운영자 스크립트의 초기 차단을 확인한다. Kubernetes 내부 입력 링크 허용과 개인 JSON 권한도 확인한다. 기존 합성 고정 입력과 해시는 변경하지 않았다.

SonarCloud의 최종 재분석 결과와 main 반영 상태는 [이미지 main 반영 검토](B3-image-main-review.md)와 [PR #32](https://github.com/f-lab-edu/F-Lab-DevOps/pull/32)를 따른다. 합성 마스킹 표식 지적은 실제 인증값이 아니지만 분석에서 임의 제외하지 않았다.
