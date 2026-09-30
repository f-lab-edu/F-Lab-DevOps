# AI 운영 조사 기능

현재 완료 범위는 **A0~A4**이다. **A5는 진행 중**으로, 합성 증거 전용 OpenAI Responses 어댑터와 모의 응답 검증까지 연결했다. 실제 유료 모델 호출·HolmesGPT 개발 입력 비교는 API 계정 준비 후 남아 있다. 실클러스터 수집과 변경 실행은 후속 구현 범위다.

- [A0 코드 기준선과 확인 결과](docs/A0-baseline.md)
- [데이터 출처·보관 기준](docs/data-policy.md)
- [런북 목록과 검증 메타데이터](runbooks/index.yaml)
- [A1 계약·검증 범위](docs/A1-contracts.md)
- [A2 저장 증거 처리·합성 재현 자료](docs/A2-evidence.md)
- [A3 로컬 사건 조사·CLI·보고서](docs/A3-local-investigation.md)
- [A4 저장 증거 조회 도구·제한·감사 기록](docs/A4-replay-tools.md)
- [A5 엔진 결정·AI 연결·남은 실제 검증](docs/A5-engine-decision.md)
- [A5 ChatGPT Plus 연동 가능성·필요 변경](docs/A5-plus-feasibility.md)
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
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/export_schemas.py
git diff --exit-code -- ai-ops/schemas
```

`requirements.lock`은 현재 애플리케이션 잠금 파일의 동일한 Pydantic 2.13.5 계열 해시를 독립적으로 복사했다. 개발 중 계약을 바꾸면 schema를 다시 생성하고 함께 검토한다. JSON Schema는 필드 구조를 공유하지만 대상 등록·hash 비교·시간 관계 같은 교차 검증은 Python 코드에서 수행한다.

저장 증거는 `python -m ai_ops validate-bundle`로 검사하고, `investigate --mode rules`로 사건 보고서를 만든 뒤 `show`로 다시 볼 수 있다. 명령별 `--bundle`, `--registry`, `--store` 예시와 저장 위치 기준은 [A3 문서](docs/A3-local-investigation.md)에 있다.

`investigate --mode ai`는 현재 `practice` 합성 증거와 **별도 과금되는 API 키 경로**만 지원한다. 키와 계정 별칭, 비용·출처 제한 및 실행 예시는 [A5 문서](docs/A5-engine-decision.md)에 있다. API 키가 없는 현재 환경에서는 외부 호출 없이 모의 응답 시험만 수행했다. Plus 플랜 사용 경로는 [별도 검토](docs/A5-plus-feasibility.md) 단계이며 아직 실행되지 않는다.
