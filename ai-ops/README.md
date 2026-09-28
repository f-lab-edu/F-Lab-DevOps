# AI 운영 조사 기능

현재 완료 범위는 **A0: 코드 기준선·런북 준비**와 **A1: 조사 데이터 계약·입력 검증**이다. 조사 CLI, 모델 연결, 실클러스터 수집, 변경 실행은 후속 구현 범위다.

- [A0 코드 기준선과 확인 결과](docs/A0-baseline.md)
- [데이터 출처·보관 기준](docs/data-policy.md)
- [런북 목록과 검증 메타데이터](runbooks/index.yaml)
- [A1 계약·검증 범위](docs/A1-contracts.md)
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

다음 작업은 **A2: 파일 증거의 정규화·마스킹·재현 데이터**다.
