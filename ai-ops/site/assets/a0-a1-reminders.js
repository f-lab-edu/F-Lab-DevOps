// A0·A1 작업 카드의 짧은 설명과 근거 파일을 오른쪽 설명 영역에 연결한다.
Object.assign(details, {
  "reminder-a0-baseline": d(
    "A0-1 · 코드 기준선 고정", "a0",
    "AI가 참고할 운영 지식을 작성하기 전에, 어떤 코드 버전을 검토했는지 확정한 작업입니다.",
    [
      "구현: 착수 시 Git SHA와 작업 트리 상태를 기록하고, 캐시·DB·경보·배포 코드의 확인 결과를 A0 기준선에 남겼습니다.",
      "이유: 실습 문서와 현재 코드가 다르면 오래된 동작을 장애 판단 기준으로 삼을 수 있기 때문입니다.",
      "기억할 점: 기준선은 당시 코드의 검토 결과입니다. 코드가 바뀌면 다시 대조해야 합니다."
    ],
    "코드 확인은 실클러스터에서 실제 사건이 일어났다는 증거가 아닙니다.",
    [file("A0 기준선", root + "docs/A0-baseline.md")]
  ),
  "reminder-a0-cache-db": d(
    "A0-2 · 캐시·DB 의미 대조", "a0",
    "캐시 지표와 DB 접근 로그를 실제 코드 동작에 맞게 해석하도록 정리했습니다.",
    [
      "구현: cache.py에서 unavailable·조회 오류·정상 miss·최근 쓰기 경로를, database.py에서 read/write 엔진 공유 조건을 확인했습니다.",
      "이유: 캐시 hit와 오류는 한 요청에서 함께 증가할 수 있고, replica라는 로그가 물리 Replica 연결을 보증하지 않기 때문입니다.",
      "기억할 점: 런타임 설정과 실제 DB 상태를 확인해야 운영 원인을 확정할 수 있습니다."
    ],
    "이 단계는 코드 의미를 정리한 것이며 Redis·DB에 직접 접속해 확인한 작업은 아닙니다.",
    [file("cache.py", repo + "url-shortener/app/core/cache.py"), file("database.py", repo + "url-shortener/app/core/database.py"), file("캐시 런북", root + "runbooks/cache.md"), file("DB 런북", root + "runbooks/database.md")]
  ),
  "reminder-a0-alert-deploy": d(
    "A0-3 · 경보·배포·롤백 대조", "a0",
    "경보가 가리키는 대상과 배포 성공·실패·복구를 구분하기 위한 기준을 정리했습니다.",
    [
      "구현: PrometheusRule의 집계·라벨과 CI의 정확한 GitOps revision, Synced/Healthy, smoke 검사 및 이전 태그 복원 절차를 대조했습니다.",
      "이유: 경보 집계 과정에서 대상 라벨이 사라질 수 있고, 태그를 되돌린 사실만으로 정상 복구를 입증할 수 없기 때문입니다.",
      "기억할 점: 실패 시점과 복구 시점의 증거를 따로 모아야 합니다."
    ],
    "실제 Alertmanager payload, 자동 롤백 실행 결과와 클러스터 상태는 A0에서 검증하지 않았습니다.",
    [file("PrometheusRule", repo + "url-shortener/url-shortener-chart/templates/prometheusrule.yaml"), file("CI·배포", repo + ".github/workflows/ci-cd.yml"), file("배포 런북", root + "runbooks/deployment.md")]
  ),
  "reminder-a0-runbooks": d(
    "A0-4 · 런북·자료 출처 정리", "a0",
    "사람이 검토한 운영 절차만 후속 AI 조사에 활용할 후보로 선별했습니다.",
    [
      "구현: 캐시·DB·배포 런북을 현행 코드에 맞춰 작성하고 index.yaml에 근거 경로와 SHA-256을 기록했습니다.",
      "이유: 오래된 실습 설명이나 출처가 불분명한 자료가 원인 판단에 섞이지 않게 하기 위해서입니다.",
      "기억할 점: 실제 캡처와 합성 자료를 구분하는 정책도 정했지만, 런북 자동 로딩은 아직 구현되지 않았습니다."
    ],
    "런북은 현재 AI가 읽는 입력이 아니라 사람이 검토한 입력 후보입니다.",
    [file("런북 색인", root + "runbooks/index.yaml"), file("자료 정책", root + "docs/data-policy.md"), file("A0 기준선", root + "docs/A0-baseline.md")]
  ),
  "reminder-a1-package": d(
    "A1-1 · 독립 패키지와 의존성", "a1",
    "기존 URL 단축 앱과 분리된 AI 운영 조사용 Python 패키지를 만들었습니다.",
    [
      "구현: ai-ops/pyproject.toml에 Python 지원 범위와 Pydantic 버전을 정의하고 requirements.lock에 의존성 해시를 고정했습니다.",
      "이유: 입력 계약을 앱 실행·DB 초기화와 분리하고 로컬과 CI에서 같은 조건으로 검증하기 위해서입니다.",
      "기억할 점: 패키지는 계약 코드의 기반이며, 설치만으로 AI 조사나 EKS 연결이 생기지는 않습니다."
    ],
    "현재 패키지는 운영 에이전트 배포 설정이 아닙니다.",
    [file("pyproject.toml", root + "pyproject.toml"), file("requirements.lock", root + "requirements.lock"), file("A1 계약 문서", root + "docs/A1-contracts.md")]
  ),
  "reminder-a1-models": d(
    "A1-2 · 조사 입력 계약 정의", "a1",
    "후속 조사 단계가 주고받을 자료의 구조와 뜻을 코드로 고정했습니다.",
    [
      "구현: TargetRegistry, IncidentRequest, EvidenceItem/Bundle, AnalysisReport를 Pydantic 모델로 정의했습니다.",
      "이유: 허용 대상, 질문, 증거의 출처·상태와 보고서 항목을 단계마다 같은 의미로 다루기 위해서입니다.",
      "기억할 점: AnalysisReport는 형식만 정의됐고 AI가 보고서를 생성하는 기능은 아직 없습니다."
    ],
    "A1의 EvidenceBundle은 계약과 생성 함수를 제공하며 실제 저장 증거 파일 처리는 A2 범위입니다.",
    [file("contracts.py", root + "src/ai_ops/contracts.py"), file("대상 설정 예제", root + "config/targets.example.json"), file("A1 계약 문서", root + "docs/A1-contracts.md")]
  ),
  "reminder-a1-validation": d(
    "A1-3 · 입력 거부와 교차 검증", "a1",
    "형식이 맞지 않거나 다른 사건의 자료가 섞인 입력을 조사 전에 거부합니다.",
    [
      "구현: JSON 크기를 먼저 제한한 뒤 버전·ID·필드·UTC 시각·해시를 검증하고, 요청과 증거의 대상·시간·bundle hash를 다시 대조합니다.",
      "이유: 비슷한 서비스나 다른 시간대의 증거를 같은 사건의 근거로 잘못 사용할 수 있기 때문입니다.",
      "기억할 점: 현재 요청 계약은 UTC Z/+00:00만 받습니다. 사용자용 KST 표시는 후속 A3 범위입니다."
    ],
    "계약 검증은 자료의 출처 진위나 장애 원인을 입증하지 않습니다.",
    [file("contracts.py", root + "src/ai_ops/contracts.py"), file("A1 계약 문서", root + "docs/A1-contracts.md")]
  ),
  "reminder-a1-tests": d(
    "A1-4 · 스키마·테스트·CI", "a1",
    "계약 모델을 변경할 때 공개 형식과 거부 조건이 함께 유지되는지 검사합니다.",
    [
      "구현: 모델에서 JSON Schema를 생성하고 정상·잘못된 입력 시험과 AI 전용 CI에 연결했습니다.",
      "이유: 코드와 스키마가 어긋나거나 검증 조건이 빠진 변경을 조기에 찾기 위해서입니다.",
      "기억할 점: JSON Schema는 기본 구조를 보여주며 대상 등록과 요청·증거 교차 검사는 Python 코드가 담당합니다."
    ],
    "로컬 계약 시험은 통과했지만 원격 GitHub Actions 실행 결과는 확인되지 않았습니다.",
    [file("스키마 생성기", root + "scripts/export_schemas.py"), file("계약 시험", root + "tests/test_input_contract.py"), file("AI 전용 CI", repo + ".github/workflows/ai-ops-ci.yml")]
  )
});
