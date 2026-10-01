# A3 로컬 사건 조사·저장·표시

- 구현일: 2026-09-29
- 범위: A2의 검증된 저장 증거로 규칙 기반 보고서를 만들고 사건·실행별로 다시 조회한다. AI 모델·라이브 EKS·변경 실행은 없다.
- 구현: [CLI](../src/ai_ops/cli.py), [사건 저장](../src/ai_ops/incidents.py), [규칙 분석](../src/ai_ops/rules.py), [보고서 검증·표시](../src/ai_ops/reports.py), [전체 흐름 시험](../tests/test_local_investigation.py).

## 사용 방법

저장소 루트에서 잠금 의존성을 설치한 Python을 사용한다. `--store`는 **저장소 밖의 개인 디렉터리**여야 하며 생략하면 `~/.local/share/url-shortener-ai-ops`를 사용한다. 새로 만들 때 `0700`, 저장 파일은 `0600`으로 둔다. 아래 경로와 요청 ID는 예시다.

```sh
PYTHONPATH=ai-ops/src python3 -m ai_ops validate-bundle \
  --bundle ai-ops/evals/fixtures/dev/s1-cache-error \
  --registry ai-ops/config/targets.example.json

PYTHONPATH=ai-ops/src python3 -m ai_ops investigate \
  --bundle ai-ops/evals/fixtures/dev/s1-cache-error \
  --registry ai-ops/config/targets.example.json \
  --store /private/tmp/aiops-my-store \
  --mode rules --request-id demo-s1-001 \
  --question '캐시 오류와 사용자 영향의 근거를 정리해줘'

PYTHONPATH=ai-ops/src python3 -m ai_ops show \
  --store /private/tmp/aiops-my-store \
  --incident '<위 명령이 출력한 사건 ID>' \
  --run '<위 명령이 출력한 실행 ID>' --format markdown
```

`--format json`은 원본 보고서 계약의 UTC JSON을 보여준다. 기본 Markdown 및 사람이 읽는 CLI 시각은 `Asia/Seoul`로 변환하고 `KST (UTC+09:00)`를 붙인다. JSON 저장·해시·시간 비교는 UTC를 유지한다. `aiops` 콘솔 명령도 `pyproject.toml`에 등록했다. A5의 `--mode ai`는 합성 실습 증거와 별도 API 키 설정에서만 사용할 수 있다.

## 한 요청의 처리

1. 등록 대상과 manifest/파일을 검증해 A2 `EvidenceBundle`을 만든다. 원본 질문은 마스킹한 뒤 `IncidentRequest`로 만든다.
2. 요청 ID·원본 질문·정규화된 묶음의 조합을 개인 저장소 키로 HMAC 처리해 재전송을 식별한다. 같은 요청 ID와 같은 입력은 기존 사건·실행을 반환하고, 같은 ID의 다른 입력은 거부한다. 원본 질문과 키는 보고서에 저장하지 않는다.
3. `<store>/<incident_id>/<run_id>/`에 정제된 `input.json`, UTC `metadata.json`, UTC `report.json`, KST `report.md`를 원자적으로 기록한다. 별도 `requests/` 색인이 요청 ID를 사건·실행에 연결한다. 프로세스 간 파일 잠금으로 동시 기록을 직렬화한다.
4. 상태 이력은 `accepted → validating → analyzing → completed`를 기록한다. 분석 중 Ctrl-C는 `cancelled`, 오류는 `failed`로 남긴다. 강제 종료로 중간 상태가 남으면 다음 저장소 접근 시 `failed(interrupted)`로 바꾼다. 두 경우 모두 정제된 입력을 보존하며 같은 요청 ID를 자동 재실행하지 않는다.
5. 보고서가 요청 사건·run ID·대상·시간·bundle hash와 일치하고 인용한 증거 ID가 실제 available 항목인지 확인한 뒤 저장한다. Markdown은 검증된 JSON에서 렌더링한다.

규칙 비교군은 캐시 `error`와 조회 로그, 정상 `miss`, 최근 쓰기 `consistency_primary`, image pull 이벤트, DB 지연, 누락, 정상 workload, 롤백 전후를 분리한다. 원인 후보와 관측 사실을 구분하고 S4·S5처럼 근거가 부족하면 판단을 유보한다. 저장된 자료의 정상/복구 표시는 **해당 관측 시점 기준**이며 현재 라이브 상태를 의미하지 않는다. 규칙 결과는 AI 분석으로 표시하지 않는다.

## 검증과 남은 범위

```sh
PYTHONPATH=ai-ops/src python3 -m unittest discover -s ai-ops/tests -p 'test_*.py' -v
python3 ai-ops/scripts/build_dev_fixtures.py --check
```

시험은 8개 합성 사례의 진단 구분, 존재하는 증거 ID 인용, 동일 요청 재전송·충돌, 개인 저장소 권한, UTC JSON/KST Markdown의 동일 시각, 취소·중단·실패 보존, CLI 재조회를 확인한다. 실제 모델 품질·실시간 경보·클러스터 권한·변경 승인·실행은 검증하지 않는다. [A4](A4-replay-tools.md)에서 묶음 내부 조회 도구와 실행 제한을 추가했으며, A5에서 실제 AI를 연결한다. 원시 캡처 자료의 보관 기간·삭제 정책은 실제 자료를 도입하기 전에 정해야 한다.
