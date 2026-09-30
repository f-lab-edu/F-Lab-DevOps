# A4 저장 증거 조회 도구와 제한 실행기

- 구현일: 2026-09-30
- 범위: 완료된 A3 사건의 정제된 `EvidenceBundle`과 A0 해시 검증 런북을 읽는 로컬 재생. 실제 모델, 실시간 EKS 조회, 변경 실행은 연결하지 않았다.
- 구현: [조회 도구](../src/ai_ops/replay_tools.py), [실행기·시험용 모델](../src/ai_ops/replay_runner.py), [사건 감사 저장](../src/ai_ops/incidents.py), [시험](../tests/test_replay_tools.py).

## 처리 흐름

1. A3의 완료된 사건·실행에서 `input.json`을 재검증해 요청과 증거 묶음을 읽는다. 실행기는 질문·대상·시간 범위와 **증거 ID·종류·상태·관측 시각만 담은 목록**을 시험용 모델에 제공한다. 증거 본문은 아직 제공하지 않는다.
2. 모델이 `ToolCall(name, arguments)`를 선택하면 실행기가 허용 목록, 정확한 인자 키, 대상, 시간 범위, 고정 메트릭 query ID를 검사한다. 유효한 호출만 저장된 묶음 안에서 조회한다. `get_runbook`은 `index.yaml`에 등록된 문서의 고정 경로와 SHA-256을 확인한 뒤 읽는다.
3. 결과는 다음 모델 호출의 `provided_evidence` 또는 `provided_runbooks`에 추가된다. 거부·누락도 `last_result`로 명시된다. 금지된 도구나 임의 파일 경로·PromQL·대상 변경은 실행하지 않는다.
4. 모델이 `Stop`을 반환하거나 한도·오류로 끝나면 실행 ID, 실제 제공한 증거 ID, 런북 ID, 호출별 도구·정제된 인자·결과 ID·소요 시간·오류를 `<store>/<incident_id>/<run_id>/tool-audit.json`에 `0600`으로 기록한다. 같은 실행에 감사 파일을 덮어쓰지 않는다. A3 규칙 보고서는 바꾸지 않는다.

A3에서 받은 사건·실행 ID로 시험용 선택 흐름을 재생할 수 있다. 다음 예시의 ID와 저장소 경로는 자신의 값으로 바꾼다.

```python
from pathlib import Path
from ai_ops.incidents import IncidentStore
from ai_ops.replay_runner import ScriptedModel, Stop, ToolCall

store = IncidentStore(Path("/private/tmp/aiops-my-store"))
record = store.show("inc-<실제 ID>", "run-<실제 ID>")
target = record.bundle.target.model_dump(mode="json")
window = record.bundle.window.model_dump(mode="json")
model = ScriptedModel([
    ToolCall("query_service_metrics", {"target": target, "window": window, "query_id": "cache_outcomes"}),
    ToolCall("get_pod_logs", {"target": target, "window": window, "container": "all"}),
    Stop(),
])
result = store.replay(record.incident_id, record.run_id, model)
print(result.as_dict())
```

| 도구 | 읽는 자료 | 필수 인자 |
|---|---|---|
| `get_workload_status` | workload 상태 | `target` |
| `get_recent_events` | 이벤트와 명시된 누락 | `target`, `window` |
| `get_pod_logs` | 마스킹된 로그 | `target`, `container`, `window` |
| `query_service_metrics` | 고정 query ID의 저장 메트릭 | `query_id`, `target`, `window` |
| `get_deployment_context` | 배포·구성 요약 | `target`, `window` |
| `get_runbook` | 목록·해시가 일치하는 A0 문서 | `document_id` |

`target`은 묶음의 전체 대상과 정확히 일치해야 하고 `window`는 묶음 시간 안이어야 한다. A2의 텍스트 로그에는 컨테이너 식별자가 없으므로 `container="all"`은 **묶음에 들어 있는 로그 전체**를 뜻한다. 이름이 기록된 로그만 특정 이름으로 조회할 수 있다. 저장 메트릭의 조회 구간에 맞는 샘플만 반환하며 결과에 원본 증거 ID와 원본 해시, 부분 조회 표시를 남긴다. 런북은 사건 관측 사실이 아니므로 증거 ID를 만들지 않는다.

소스 체크아웃에서는 `ai-ops/runbooks/`를 기본으로 읽는다. 패키지 wheel에는 A0 런북 원문을 포함하지 않으므로 별도 설치 위치에서 사용할 때는 운영자가 `AI_OPS_RUNBOOK_ROOT`를 해당 런북 디렉터리로 지정해야 한다. 목록에 등록된 고정 문서 이름·해시 검사는 그대로 적용된다.

## 실행 한도와 현재 경계

기본값은 조사 전체 120초, 도구 최대 10회, 도구 호출당 10초, 일시적인 모델 오류 재시도 1회다. 거부된 호출도 10회에 포함한다. 로컬 메인 스레드에서 벽시계 타이머로 느린 모델·도구 호출을 중단하고, 기한이 지난 도구 결과는 모델에 전달하지 않는다. 타이머가 이미 다른 용도로 사용 중이거나 메인 스레드가 아니면 안전하게 실패한다. 실제 모델 어댑터는 A5에서 자신의 네트워크 타임아웃·토큰·비용 한도도 적용해야 한다.

`ScriptedModel`은 조회 순서를 재현하는 **시험용** 구현이다. A4 결과는 AI 진단 보고서가 아니다. A5에서 실제 모델 연결과 보고서의 **실제로 제공된 available 증거 ID만 인용**하는 검증을 연결한다. 런북의 문장은 참고 자료이며 실행 지시로 취급하지 않는다.

## 검증

```sh
PYTHONPATH=ai-ops/src python3 -m unittest discover -s ai-ops/tests -p 'test_*.py' -v
```

합성 8개 사례 중 필요한 종류를 조회하고, 가짜 모델의 반복 선택, 금지 도구·범위 밖 대상·임의 인자·비등록 런북 거부, 호출·시간·재시도 한도, 전달된 ID, 개인 저장소의 감사 파일 권한을 확인한다. 실제 모델 품질, 원격 CI, 운영 클러스터 권한·변경은 이 시험에 포함되지 않는다.
