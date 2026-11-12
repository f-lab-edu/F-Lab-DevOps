# C-04 준비 완료 후 승인으로 시작하기

2026-11-12 KST. 사용자 요청에 따라 고정 시각 대기를 대체하는 실행 경로를 구현하고 **로컬에서만 검증**했다. 실제 AWS/GitHub/Kubernetes 조회·자격 발급·workflow 실행·장애 주입·Terraform은 수행하지 않았다. 이 문서의 코드 변경이나 게시 자체를 실습 실행 승인으로 사용하지 않는다.

## 새 실행 흐름

1. `prepare_c4_trial.py --when-ready`가 대상 UID·정상/실패 이미지·코드·Git 보호·정상 CI 기준을 대조해 **시각 없는 intent**와 사전 검증을 만든다. 새 원격 감시 코드가 게시되지 않았거나 고정 소스 hash가 다르면 이 단계에서 거절한다.
2. 준비 담당자가 기존 한정 자격 범위에서 실습 토큰·C-02 조회·로컬/원격 감시를 준비한다. `run_c4_ready_guard.py`와 새 `ai-ops-c4-ready-rollback.yml`은 정상 기준선을 확인하고 **승인 바인딩을 기다린다**. 승인 전에는 장애·원복을 실행하지 않는다. 원격 environment 승인은 기존대로 GitHub UI에서 처리한다.
3. 실제 감시 로그·PID·원격 run/step/main SHA·소스 hash를 확인한 뒤 `StandbyGuardReader`용 증거를 만든다. `run_c4_ready_trial.py`가 정상 CI 콘텐츠·C-02 TLS 조회·불변 기준선·이미지·양쪽 감시를 준비하고 `ready_awaiting_actual_approval` 영수증을 게시한다. **준비 유효기간은 300초**다.
4. 이 영수증의 **intent hash와 readiness hash**에 대한 실제 사용자 실행 승인을 받는다. 승인을 기다리는 동안 이미지와 감시 조회를 갱신한다. 준비보다 앞선 승인·다른 영수증·만료·프로세스 재시작·시계 변경·대상/코드/감시 변경은 거절한다. 승인 파일은 현재 준비가 끝난 뒤에만 생성할 수 있다.
5. 승인 후 현재 조건을 재검증하고 **T를 한 번만 고정**한다. 승인 소비 표식을 `O_EXCL`로 저장하고 자기 Lease를 취득한다. 양쪽 감시는 같은 activation hash·자기 guard ID로 ACK한다. 그 뒤 같은 프로세스가 즉시 주입한다. 달력의 특정 시각까지 기다리는 경로는 없다.
6. 실제 실패 → 새 C-02 조사 → 새 제안/PR → 실제 안내 → 정확한 head의 실제 사용자 리뷰·CI → 병합·동기화 → C-03 인계 → 최소 120초 C-04 관찰은 기존 코드를 공유한다. PR 안내 성공 응답과 사람 리뷰를 합성하지 않는다.
7. 준비 담당자는 실제 C-03 인계가 확인되면 양쪽 감시를 종료하고, 최종 결과 뒤 이번 토큰·원복 Secret·파생 설정을 회수한다. 새 실행기는 자체 조회 스레드·포워드·운영자 소켓과 자기 완료 조사 Job을 정리한다. 원본 로그인·App 키·PVC·사건 근거는 보존한다.

## 유지한 시간 조건

T는 사용자 답변의 제출 시각 자체가 아니라 **실제 승인과 마지막 현재 조건을 확인한 뒤 기한을 연결한 시각**이다. 승인 처리에 필요한 조회·ACK만 수행하며 고정 시각 대기는 하지 않는다.

| 조건 | 기한 |
|---|---|
| 준비 영수증 유효기간 | 준비 완료 후 300초 |
| 새 PR 실제 안내 영수증 게시 | T+90초 이내 |
| 정확한 head의 실제 사람 리뷰 | T+210초까지 |
| 실제 안내 이후 리뷰 시간 | **최소 120초** |
| 소유 실행기 원복 시작 목표 | T+240초 |
| 독립 감시 원복 시작 기한 | **T+300초** |
| C-04 관찰 | 실제 C-03 인계 후 최소 120초 |

감시 ACK·마지막 조회·첫 쓰기는 **T부터 최대 15초 안**에 완료해야 한다. 느리면 주입하지 않고 자기 정상 잠금만 해제한다. 주입이 T보다 늦어져도 T+300초를 연장하지 않으므로 실제 주입 이후 원복 기한은 300초를 넘지 않는다. 기존 달력 방식의 5초 주입 창은 그 방식에 그대로 남기고 새 방식의 시작 핸드셰이크에만 15초 상한을 적용했다.

조사·PR·안내가 늦어 120초 리뷰 시간을 확보하지 못하거나 실제 리뷰/CI가 없으면 정상 원복한다. 300초는 원복 **시작 기한**이며 완료 보장이 아니다. 양쪽 감시는 같은 클러스터·Argo·Lease에 의존하고, 기존 소유 잠금을 빼앗지 않는다. 원복 실패는 잠금과 근거를 보존해 수동 조사한다.

## 실행 계약과 자료

- `ReadyTrialPlan`: 대상·runtime manifest·사전 검증 hash·고정 상대 기한. 시작 날짜/시각 필드가 없다.
- `ReadyReceipt`: 실제 현재 프로세스의 준비 완료/만료·기준선 hash·양쪽 guard ID.
- `ReadyApproval`: 실제 사용자의 intent/readiness hash별 승인. 채팅 답변이나 GitHub 과거 리뷰를 임의로 승격하지 않는다.
- `ReadyActivation`: 위 세 자료와 파생 절대 실행 계획을 묶는다. 상대 기한 승인을 파생 계획에 연결하는 것이며 새 사람 승인을 만들었다고 기록하지 않는다.
- `approval-consumed.json`: 실패·프로세스 재시작에도 재사용할 수 없는 소비 표식.
- `active-plan.json`, `derived-authorization.json`, `result-owner.json`: **감시 활성화와 첫 Argo 쓰기 전** 게시한다. 중단 뒤 기존 `restore_c4_trial.py`로 현재 소유자·PID·고정 정상 SHA를 대조할 수 있다.

새 JSON Schema는 `schemas/c4-ready-{intent,receipt,approval,activation}.schema.json`이다. 모델 검증은 대상의 정확한 필드 집합·기존 TrialPlan 타입·서로 다른 정상/실패 기준과 해시/시간 연결을 추가 검사한다.

실제 감시 증거는 `intent_hash`, 실제 최초 관찰 시각, `standby_expires_at`, `guard_source_root`, 사전 검증과 동일한 8개 `source_sha256`, 로컬 PID/intent 경로/개인 marker/guard ID, 원격 run ID/guard ID를 포함한다. 단순 실행 중 표시만으로 실제 준비 로그를 대신하지 않는다. 감시 코드 파일과 증거의 hash를 함께 바꿔도 사전 검증에 묶인 승인 hash와 다르면 거절한다.

새 runtime은 개인 보존 소스 없이 검토한 commit만으로 만든다. `requirements.lock`과 로컬 도구 전용 `requirements-trial.lock`을 설치한 환경에서 다음 명령을 사용한다. `runtime_root`는 아직 존재하지 않는 등록된 개인 경로다. Git에 커밋되지 않은 파일과 로컬 자격은 묶지 않고, 심볼릭 링크는 거절한다.

```sh
PYTHONPATH=ai-ops/src python ai-ops/scripts/build_c4_runtime.py \
  --revision "$reviewed_commit" --destination "$runtime_root"
```

새 runtime 안에서 사용할 명령 형식은 다음과 같다. 아래는 **실환경 준비가 끝난 뒤의 사용 절차**이며 이번 로컬 작업에서 실행하지 않았다. 경로 변수는 개인 실행 폴더와 검증된 묶음의 절대 경로로 설정한다.

```sh
python "$runtime_root/ai-ops/scripts/bridge_c4_review_notice.py" \
  --when-ready --plan "$trial_root/intent.json" \
  --activation "$trial_root/live/activation.json" \
  --socket "$operator_root/review.sock"

python "$runtime_root/ai-ops/scripts/run_c4_ready_trial.py" \
  --intent "$trial_root/intent.json" --preflight "$trial_root/preflight.json" \
  --guard-evidence "$trial_root/standby-guards.json" \
  --normal-binding "$trial_root/normal-binding.json" --normal-run-id "$normal_run_id" \
  --projection-probe "$trial_root/projection-probe.json" \
  --c2-kubeconfig "$trial_root/c2-kubeconfig.json" --kubeconfig "$operator_kubeconfig" \
  --operator-state-root "$operator_root" --review-notifier-socket "$operator_root/review.sock" \
  --run-dir "$trial_root/live" --approval "$trial_root/live/approval.json" \
  --readiness "$trial_root/live/readiness.json" --activation "$trial_root/live/activation.json" \
  --output "$trial_root/live/result.json" --execute-approved-trial
```

`operator_root`는 `/private/tmp/c04-<새 trial_id>`에 고정한다. `--execute-approved-trial` 옵션만으로 주입하지 않는다. 현재 준비 영수증에 대한 실제 승인 파일이 별도로 필요하다. 실제 호스트 도구의 accepted 응답을 같은 bridge stdin에 전달하는 안내 절차도 유지한다.

기존 `supervise_c4_trial.py`와 `run_c4_trial.py`는 과거 고정 계획의 재현/호환 경로다. 새 실행에서 그 감독기의 12분 전·5분 전 게이트를 사용하지 않는다. 새 준비 담당 경로는 위 intent/standby/ready CLI를 사용한다.

## 게시 전 검증과 남은 작업

[게시 소스 검증 결과](assets/C4-ready-publication-validation-20261112.json)는 실제 저장소 체크아웃 기준이다. 이전 개인 결합 runtime에서 검증한 C-03 코드와 시험을 저장소에 포함해, 필수 C-03 통합 시험이 모듈 부재로 건너뛰어지지 않게 했다. 기존 저장소의 더 최신인 격리 원복 안전 검사는 유지했다. C-03 초안/리뷰/잠금/병합/정확한 동기화와 C-04 관찰 경계는 원격 코드에서도 함께 검증한다.

감시가 Lease 응답을 받은 뒤의 실제 시각으로 activation을 검증해 조회 도중 게시 경합을 처리하며, 기한 원복 함수에는 kubeconfig를 명시적으로 전달한다. 상대 기한을 이동하거나 원복 소유권을 빼앗는 방식으로 해결하지 않는다. 새 스키마 4개를 공통 exporter에 등록했고 원복 workflow만 변경해도 CI가 실행된다. 실제 Helm 렌더를 쓰는 시험보다 먼저 고정 Helm을 설치한다.

이 PR의 workflow는 수동 호출 전용이며 main 보호/환경 승인/OIDC 범위를 바꾸지 않는다. 실제 원격 standby/ACK·환경 승인 지연·실제 장애/복구·C-04 관찰은 원격 반영 후 별도로 검증한다. 새 intent의 실제 준비가 끝나면 그 영수증에 대한 사용자 실행 승인을 받고, 새 복구 PR의 정확한 head에 대해 기한 내 실제 GitHub 리뷰를 확인해야 한다. 과거 승인이나 PR 리뷰를 재사용하지 않는다. Terraform은 필요하지 않다.
