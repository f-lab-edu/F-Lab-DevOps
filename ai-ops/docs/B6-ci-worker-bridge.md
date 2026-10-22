# B-06 CI 전달기와 worker 큐 연결

상태: **전달 계약·완료 CI 조회 실행기·영속 큐·worker 연결 구현, 격리 호스트 worker 실측 완료**. 현재 클러스터에 relay/실습 worker를 상시 배포한 상태는 아니다. 최근 실제 CI가 없으므로 완료 이벤트의 자동 발견→접수 성공은 모의 입력으로, 기존 실제 이력의 검증→접수→현재 읽기 수집→보고서는 명시 재생으로 확인했다.

## 무엇을 구현했나

[S10-04](B6-ci-history-import.md)의 GitHub 검증과 [S10-05](B6-ci-report.md)의 보고서 해석을 기존 worker 큐에 연결했다. 운영자가 수집과 보고서 생성을 각각 실행하는 대신, 별도 전달기(relay)가 CI를 확인해 큐에 넣으면 worker가 처리한다.

흐름은 **전달기가 고정 실습 workflow의 완료 기록 조회 → run/attempt·main 소스·ZIP 해시·실패/원복 관측 검증 → 비공개 큐에 HMAC으로 결합해 접수 → worker가 설정·HMAC·구조 검사 → 현재 Kubernetes/Argo/Prometheus 읽기 수집과 CI 이력 연결 → snapshot 고정 → 규칙 보고서 저장 → 큐 완료 참조 저장** 순서다.

`전달기`는 GitHub 자료를 검증하는 별도 프로세스다. `큐`는 접수한 조사 요청과 시도 상태를 보존하는 SQLite 저장소다. `HMAC`은 기존 개인 저장소 키로 큐 입력과 중복 키를 함께 결합한 무결성 값이다. `worker`는 큐 한 건을 처리하는 실행기다. CI 단계가 과거에 수행한 자동 원복과 worker가 지금 실행하는 읽기 조사는 다르다.

## 검증 위치와 신뢰 경계

| 구성 | 할 일 | 인증/권한 |
|---|---|---|
| `ai_ops.ci_relay` | 고정 GitHub API에서 완료 목록과 원본 artifact 검증, 접수 | 운영자 `gh` 읽기 인증, 해당 비공개 큐 쓰기 |
| `JobQueue.submit_ci` | 실습 대상 제한, 중복 키 생성, 입력 HMAC 결합, commit | 기존 같은 사용자 소유 저장소·전용 HMAC 키 |
| worker | 접수 HMAC/계약 검사, 현재 읽기 수집, 같은 출처 snapshot 확인, 보고서 | 기존 실습 읽기 연결; GitHub·모델 인증 불필요 |

공개 HTTP endpoint나 `verified=true` JSON 입력은 추가하지 않았다. 외부 파일을 검증된 CI로 받아주는 CLI도 없다. 검증 함수가 반환한 메모리 객체만 접수한다. worker의 내부 JSON 복원은 큐 HMAC 확인 뒤 수행한다. GitHub ZIP 원본 확인은 전달기에서 수행하며 worker는 ZIP을 재다운로드하지 않는다.

비공개 큐는 기존과 같은 OS 사용자·0700 폴더·0600 파일이 신뢰 경계다. 같은 사용자로 실행하는 임의 Python 코드나 저장소 키에 접근할 수 있는 관리자까지 차단하는 별도의 보안 경계라고 주장하지 않는다. 전용 전달기는 고정 API만 사용하며 인증값·원시 API 응답·ZIP 본문은 큐/콘솔에 넣지 않는다.

현재 운영 worker의 운영 앱 설정을 실습 CI 대상으로 바꾸지 않았다. 실제 검증은 기존 `lab-investigator`의 읽기 전용 자격을 사용하는 **별도 호스트 worker와 별도 개인 큐**에서 했다. 운영 worker/수신기/PVC·RBAC·NetworkPolicy·GitHub 환경은 변경하지 않았다.

## 큐 입력 계약

기존 수동/경보 입력의 6개 필드는 유지한다. CI 입력에만 아래 두 필드를 추가한다.

```json
{
  "target": "url-shortener-b6-lab",
  "question": "고정된 CI 조사 질문",
  "config_hash": "동일한 실습 설정의 SHA-256",
  "route": null,
  "operation": null,
  "mode": "rules",
  "source": "verified_lab_ci_v1",
  "ci_history": {
    "schema_version": 1,
    "repository": "f-lab-edu/F-Lab-DevOps",
    "workflow": ".github/workflows/ai-ops-lab-ci.yml",
    "run_id": "실행 번호",
    "run_attempt": "재시도 번호",
    "artifact_id": "artifact 번호",
    "archive_digest": "sha256:원본 ZIP 해시",
    "source_revision": "검토된 main 소스 SHA",
    "records": ["검증한 deployment 관측", "rollback 관측", "result 관측"]
  }
}
```

위 JSON은 구조 설명이며 실제 계약의 해시/ID 형식과 records의 엄격한 객체 검사를 생략한 예다. CI 이력은 최대 32KiB, 전체 worker 큐 입력은 최대 64KiB로 제한한다. 토큰·비밀번호·모델 키·임의 파일 경로/URL·임의 질문·쓰기 명령을 추가하지 않는다.

중복 키는 `source + repository + workflow + run_id + run_attempt`의 해시다. 같은 실행/재시도를 반복 전달하면 같은 작업을 반환한다. 같은 키인데 artifact·소스·설정·내용이 바뀌면 충돌로 거부한다. 새로운 attempt는 별도 작업이다. 보관 만료로 삭제한 키의 재접수 거부도 기존 규칙을 따른다.

## worker 처리와 재시작

worker는 수집 전에 설정 해시와 CI 입력 HMAC을 검사한다. 수집 뒤에도 같은 두 단계·run/attempt·artifact·원본 해시·소스·UID·관측 시각이 snapshot 감사에 남았는지 대조한다. 현재 앱이 재생성돼 UID가 달라지는 등 연결이 실패하면 `needs_review / integrity_error`로 두고 잘못된 보고서를 만들지 않는다.

수집 snapshot이 고정된 뒤에는 재시도/재시작에서 그 입력을 재사용한다. 보고서 저장 뒤 큐 commit 전에 중단돼도 기존 완료 보고서와 접수 입력을 대조해 상태만 복구한다. GitHub나 클러스터를 다시 조회해 원래 입력을 바꾸지 않는다. 기존 lease·generation·취소·최대 3회 시도·7일 보관 정리는 유지한다.

오래된 CI 관측은 기존 900초 창에서 `stale`로 남는다. 큐 대기가 길어졌다는 이유로 관측 시각을 갱신하지 않는다. CI 기록만으로 사용자 경로의 복구나 AI 분석 성공을 주장하지 않으며 외부 모델은 계속 사용하지 않는다.

## 운영자 실행

같은 설정과 같은 개인 큐를 사용하는 **새 버전의 worker를 먼저** 준비한다. 이전 worker는 새 CI 입력을 해석하지 못해 검토 대기로 보낼 수 있으므로 전달기만 먼저 켜지 않는다. SQLite 테이블 구조와 기존 수동 입력은 바꾸지 않았다.

전달기 환경에는 잠금 의존성과 `gh` 인증이 필요하다. worker 환경에는 같은 Python 코드/잠금 의존성과 실습 읽기 연결만 필요하며 `gh`나 GitHub 토큰을 주지 않는다. 호스트에서 사용할 EKS endpoint·CA·짧은 수명 토큰·Prometheus port-forward는 S10-04 절차로 준비하며 만료/종료 시 다시 준비해야 한다. 아래 예의 `$AI_OPS_FILES_ROOT`는 저장소 밖 현재 사용자 소유의 전용 폴더다.

```sh
# 첫 프로세스: 큐를 처리하는 읽기 전용 worker
PYTHONPATH=ai-ops/src python -m ai_ops.worker_runtime \
  --config "$AI_OPS_FILES_ROOT" --store "$AI_OPS_FILES_ROOT/ci-store"

# 별도 프로세스: 실행 중인 동안 완료된 실습 CI를 주기적으로 확인
PYTHONPATH=ai-ops/src python -m ai_ops.ci_relay \
  --config "$AI_OPS_FILES_ROOT" --store "$AI_OPS_FILES_ROOT/ci-store" --watch
```

`--watch`가 없으면 목록을 한 번 조회한다. 고정 실습 workflow의 main·workflow_dispatch·completed 목록에서 최근 20개 중 **완료 갱신 시각이 15분 이내인 failure**만 검증 대상으로 삼는다. 취소·진행 중·과거 기록은 자동 접수하지 않는다. 목록 20개 제한은 `listing_limited=true`로 표시하며 모든 과거 실행/재시도를 빠짐없이 처리한다고 보장하지 않는다. 60초는 조회 사이의 대기이며 API 처리 시간은 별도다. 연결 실패는 고정 오류만 출력하고 다음 주기에 재시도한다.

명시적으로 과거 한 건을 재생할 수도 있다. 이 경우 시각을 바꾸거나 새 장애를 일으키지 않는다.

```sh
PYTHONPATH=ai-ops/src python -m ai_ops.ci_relay \
  --config "$AI_OPS_FILES_ROOT" --store "$AI_OPS_FILES_ROOT/ci-store" \
  --run-id 39719907612 \
  --expected-head b0a667fd77484eb126985b3dd6ea811e7831fd7c
```

## 파일과 실제 결과

| 파일 | 역할 |
|---|---|
| `src/ai_ops/ci_relay.py` | 고정 완료 CI 조회·원본 검증·명시 접수/주기 실행 |
| `src/ai_ops/ci_history.py` | 고정 실습 완료 목록 API 한 개만 추가 허용 |
| `src/ai_ops/ci_handoff.py` | 엄격한 큐 계약·복원·수집 결과와의 결합 검사 |
| `src/ai_ops/job_queue.py` | 실습 CI 접수·중복 키·입력 HMAC 확인 |
| `src/ai_ops/worker.py` | CI 입력 수집 전달, snapshot/보고서·접수 내용 대조 |
| `tests/test_ci_queue.py` | 신규 10개 통합 시험: 발견·접수·중복·충돌·변조·UID·stale·재시작 |

[실측 JSON](assets/B6-ci-queue-validation.json): 기존 실제 run `39719907612`·attempt 1을 GitHub에서 다시 검증해 별도 큐에 접수했다. worker는 현재 실습 읽기 수집과 CI 이력을 묶어 `partial / unknown`, `rules-captured-ci-v1` 보고서를 저장했다. 시도는 1회이고 두 번째 전달은 같은 job이었다. CI 두 관측은 원래 시각을 유지한 stale였다. 자동 목록 조회는 기존 실행 1개가 오래돼 skipped였으며 최근 완료 이벤트의 자동 접수 성공 실측으로 표시하지 않았다.

worker 구간에서 GitHub 호출과 모델 키 조회를 금지한 채 실제 읽기 수집을 수행했다. 실습 Pod UID는 전후 동일했고 임시 토큰 파일과 port-forward는 정리했다. 새 장애·모델 호출·IAM/RBAC 변경은 0회다. 신규 10개 포함 전체 Python **364개**를 통과했다.

완료한 범위는 전달 실행기·큐 계약·worker 연결과 위 검증이다. **클러스터 상시 relay/실습 worker 배포, relay 전용 GitHub 읽기 자격의 배치, 최신 실제 CI 완료의 자동 처리 실측은 아직 하지 않았다.** 상시 배치는 실습 worker와 같은 비공개 큐에 접근하는 별도 전달 프로세스에만 GitHub 인증을 주는 구조로 이어가야 한다. 현재 운영 worker의 범위/권한을 확대하거나 개인 gh 인증을 클러스터에 복사해 대신하지 않는다. B 전체 완료와 C 진입은 계속 보류한다.

## 상시 배포 후속

클러스터용 전용 파일 토큰 transport와 비활성 기본 배포 정의는 [CI 서비스 문서](B6-ci-service.md)로 이어진다. 이 구현 추가만으로 상시 배포·최근 실제 CI 자동 처리 완료를 의미하지 않는다. 기존 gh 인증 경로는 로컬 운영자용으로 유지한다.
