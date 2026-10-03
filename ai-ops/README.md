# AI 운영 조사 기능

현재 **A0~A4와 A6 로컬 검증을 완료**했다. A5의 합성 증거 전용 OpenAI Responses 어댑터는 실제 AI 보고서 생성·근거 인용·감사 기록을 확인했고 현재 엔진으로 사용한다. 실패 실행의 알려진 부분 사용량과 S2 정상 캐시 miss 재검증도 마쳤다. 다만 원래 계획한 HolmesGPT와 동등 조건의 4~6건 품질 비교는 아직 수행하지 않아 **A5 전체 상태는 검증 중**이다. 그 판단과 한계는 [A5 문서](docs/A5-engine-decision.md)에 있다. A6는 현재 어댑터의 보고서 계약·실패 시 규칙 부분 결과·보안 경계를 모의 응답으로 검증했다. **A7은 새 v3 합성 입력에서 수정 고정 증거 AI와 도구 탐색 AI를 각각 48/48회 완료**했다. 기대 진단 자동 채점은 각각 45/48, 36/48이다. 첫 반복의 비독립 예비 검토와 [AI 전용 원격 CI 통과](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/37116993482), [앱 CI 원격 검증](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/37118365182)을 확인했다. 익명 A/B 검토 화면을 준비했지만 독립 수동 의미 채점과 앱 CI의 PR 이벤트 실행은 남았다. 실클러스터 수집과 변경 실행은 후속 구현 범위다.

- [A0 코드 기준선과 확인 결과](docs/A0-baseline.md)
- [데이터 출처·보관 기준](docs/data-policy.md)
- [런북 목록과 검증 메타데이터](runbooks/index.yaml)
- [A1 계약·검증 범위](docs/A1-contracts.md)
- [A2 저장 증거 처리·합성 재현 자료](docs/A2-evidence.md)
- [A3 로컬 사건 조사·CLI·보고서](docs/A3-local-investigation.md)
- [A4 저장 증거 조회 도구·제한·감사 기록](docs/A4-replay-tools.md)
- [A5 엔진 결정·AI 연결·남은 실제 검증](docs/A5-engine-decision.md)
- [A5 ChatGPT Plus 비채택 검토 기록](docs/A5-plus-feasibility.md)
- [A6 보고서·실패·보안 검증](docs/A6-report-failure-validation.md)
- [A7 고정 평가 진행 기록](docs/A7-fixed-evaluation.md)
- [A7 독립 의미 검토 인계 안내](docs/A7-independent-review.md)
- [A0·A1 파일별 처리 흐름 이미지](docs/assets/ai-ops-a0-a1-flow.png)
- [캐시 런북](runbooks/cache.md)
- [DB 런북](runbooks/database.md)
- [경보·배포·롤백 런북](runbooks/deployment.md)

런북은 코드에서 확인한 동작과 사건별 판단 절차를 제공한다. 실환경 정상 동작이나 특정 사건의 원인을 입증하는 자료는 별도로 수집해야 한다. `index.yaml`에 등록된 문서만 향후 AI 입력 후보로 사용한다. 기준선 기록·데이터 정책·평가 정답은 자동으로 모델에 제공하지 않는다.

`src/ai_ops/contracts.py`가 요청·증거·보고서의 Pydantic 계약을 정의한다. `schemas/`는 같은 모델에서 생성한 JSON Schema다. 등록 대상은 `config/targets.example.json` 형식으로 관리한다. 예제 cluster ID는 가상 값이다.

Python 3.11 이상과 `requirements.lock`의 고정 의존성을 사용한다. 저장소 루트에서 로컬 검증:

```sh
python3 -m venv /tmp/ai-ops-venv
/tmp/ai-ops-venv/bin/python -m pip install --require-hashes --only-binary=:all: -r ai-ops/requirements.lock
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python -m unittest discover -s ai-ops/tests -p 'test_*.py' -v
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/build_holdout_fixtures.py --check
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/build_holdout_v2_fixtures.py --check
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/build_holdout_v3_fixtures.py --check
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/export_schemas.py
git diff --exit-code -- ai-ops/schemas
```

`requirements.lock`은 현재 애플리케이션 잠금 파일의 동일한 Pydantic 2.13.5 계열 해시를 독립적으로 복사했다. 개발 중 계약을 바꾸면 schema를 다시 생성하고 함께 검토한다. JSON Schema는 필드 구조를 공유하지만 대상 등록·hash 비교·시간 관계 같은 교차 검증은 Python 코드에서 수행한다.

저장 증거는 `python -m ai_ops validate-bundle`로 검사하고, `investigate --mode rules`로 사건 보고서를 만든 뒤 `show`로 다시 볼 수 있다. 명령별 `--bundle`, `--registry`, `--store` 예시와 저장 위치 기준은 [A3 문서](docs/A3-local-investigation.md)에 있다.

`investigate --mode ai`는 현재 `practice` 합성 증거와 **별도 과금되는 API 키 경로**만 지원한다. 키를 처음 등록하거나 교체할 때는 저장소 루트에서 아래 명령을 실행하고 `OpenAI API 키:` 입력창에 붙여넣으면 된다. 입력 문자는 화면에 표시되지 않는다. 이 설정 명령은 API를 호출하거나 비용을 발생시키지 않는다.

```sh
./ai-ops/scripts/aiops-local configure-api-key
./ai-ops/scripts/aiops-local api-key-status
```

키는 Git 저장소 밖 `~/.local/share/url-shortener-ai-ops/.openai-api-key`에 사용자 전용 권한(0600)으로 보관된다. 기본 저장소를 바꾸려면 설정·조사 명령에 같은 `--store` 경로를 지정한다. 기존 `OPENAI_API_KEY` 환경변수가 설정되어 있으면 저장된 키보다 우선한다. 키 값은 채팅·명령행 인자·프로젝트 파일에 입력하지 않는다. 이 컴퓨터의 Python 실행 환경은 `aiops-local`이 자동으로 찾는다. 다른 환경에서는 Python 3.11 이상에 `requirements.lock` 의존성을 설치하고 `AI_OPS_PYTHON`으로 경로를 지정하면 된다. 비용·출처 제한, 실행 예시 및 실제 점검 결과는 [A5 문서](docs/A5-engine-decision.md)에 있다. Plus 플랜 사용 경로는 선택하지 않았으며 [검토 기록](docs/A5-plus-feasibility.md)만 보관한다.

AI 호출이 인증·네트워크·시간·예산 문제로 실패하면 검증된 저장 증거의 규칙 분석을 `partial`로 저장한다. CLI는 실패 이유를 보여주고 종료 코드 1을 반환한다. 보고서 형식·근거·보안 검증을 통과하지 못한 모델 초안은 부분 결과로 위장하지 않고 실패 상태로 남긴다. 자세한 조건은 [A6 검증 기록](docs/A6-report-failure-validation.md)을 참고한다.
