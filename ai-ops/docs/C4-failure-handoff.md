# 장애 주입 소유권의 C-02/C-03 인계

격리 장애 주입 소유권을 잠금을 비우지 않고 C-02 조사와 C-03 실행기로 넘긴다. 실제 실행은 [준비 완료 후 승인 시작](C4-ready-start-local-20261112.md)에서 현재 준비 영수증별 승인을 받은 뒤 진행한다. 새 복구 PR head의 실제 사람 리뷰·CI는 별도로 필요하다.

## 연결한 경로

`주입 소유자 manual-* → 실패 인계 제공 → 같은 잠금 아래 C-02 조사·제안 → PR head 승인 → 동일 Lease를 c3-*로 CAS 인계 → 기존 C-03 재검증 → 정확한 head 병합 계획`

`FailureHandoff`는 실습 ID, 원래 소유자와 지정 실행자, Lease UID·취득 시각, Application·Deployment·실패 Pod UID, Deployment generation, 정상·실패 revision/이미지, 실제 주입 시각, 제공 시각과 원복 시작 기한을 묶는다. 기한은 주입 시각으로부터 최대 300초다. `normal_revision`과 `normal_image`는 실행별 검토안의 고정 정상 기준에서 가져온다. `normal_revision`과 실패 관측 SHA, 복구 PR의 base SHA는 각자 역할대로 대조한다.

제공·인계는 소유 프로세스가 취득 결과를 보유한 `LabLease` 객체와 새 원본 조회 함수를 사용한다. 제공 단계는 `ai-ops/c03-failure-handoff` annotation에 인계 hash를 CAS로 기록하고 holder는 유지한다. 인계 단계는 같은 UID/resourceVersion의 Lease를 지정 `c3-*` holder로 한 번에 교체한다. 기한을 이유로 다른 holder를 빼앗는 경로는 없다.

인계 hash와 JSON은 로컬 자료의 동일성 대조에 쓰인다. 신뢰 경계는 기존 주입·실행 프로세스와 사용자 전용 보관 위치다. 실행별 승인, GitHub 리뷰, 정상 CI 및 ECR 원본 확인은 각각 기존 경로에서 확인한다.

## 변경 파일과 호출 순서

| 파일 | 역할 |
|---|---|
| `src/ai_ops/c_failure_handoff.py` | 엄격한 인계 계약, 실패 원본 검사, 제공·인계 CAS, C-03 원본 reader 결합, 기존 승인/CI 게이트와 병합 계획 연결 |
| `src/ai_ops/c2_active_capture.py` | 기본 빈 잠금 검사와, 명시적인 실패 인계 아래의 조사 경로 |
| `scripts/prepare_c2_active_capture.py` | `--failure-handoff` 파일과 실습 ID·격리 저장소·두 revision·대상 UID 대조 후 기존 읽기 조회와 Job 준비 |
| `src/ai_ops/b6_coordination.py` | 보존 C-03와 동일한 c3 holder 형식, 정상 해제/다음 취득 시 이전 인계 annotation 정리 |
| `schemas/injection-failure-handoff.schema.json` | 인계 JSON Schema |
| `tests/test_c_failure_handoff*.py` | 실패 인계와 보존 C-03 실제 게이트 연결 시험 |

1. 기존 실행별 승인과 원복 준비를 마친 주입 프로세스가 자기 `LabLease`를 보유한다. 실제 주입 뒤 `offer_failure_handoff()`를 호출한다. 이 함수는 15초 이내에 조회한 원본에서 고정 장애 SHA·이미지, Argo 동기화 성공, Ready 0, ImagePullBackOff/ErrImagePull, Pod 소유 관계와 주입 이후 생성 시각을 대조한다. 반환된 인계는 `write_private_json()`으로 개인 위치에 기록한다.
2. C-02 조사 준비에 `--source c03-isolated --failure-handoff <개인 JSON>`를 전달한다. 일반 조사 경로는 기존처럼 사용 중 잠금을 거부한다. 인계를 전달한 경로는 현재 Lease annotation과 동일한 기록, 고정 대상과 실패를 대조한다. CLI의 baseline/failure revision과 실습 번호도 기록과 일치해야 한다. Job 적용은 기존 승인된 실습 실행기의 절차를 따른다.
3. `assess_failure_handoff_proof()`에 같은 실패 snapshot과 검증한 정상 CI/ECR 입력을 전달해 기존 `assess_recovery_proof()`로 연결한다. 이어 사건·사용자 요청·제안·PR 연결과 실제 head 리뷰를 기존 절차로 검증한다.
4. `FailureHandoffReader(record, c3_live_reader, read_failure, clock=clock)`를 만들고 이를 `acquire_failure_preflight()`의 `live_reader`로 전달한다. 인계의 대상과 승인 제안의 현재·목표 digest를 먼저 대조한 뒤 CAS로 holder를 넘기고, 기존 `verify_execution_preflight()`를 잠금 아래에서 호출한다. 반환값은 `(locked, recipient_lease)`이며 `execution_allowed=false`를 유지한다.
5. `plan_failure_merge()`로 현재 실패·지정 holder·기한을 다시 대조해 정확한 head의 병합 계획을 만든다. 이어 기존 `verify_merged_commit()`과 `plan_exact_sync()`에 같은 `locked`와 인계에 묶인 reader를 사용한다. 계획은 `write_enabled=false`다. 실제 쓰기 adapter는 기존 승인 범위와 실행 시각을 대조해 각 동작 직전에 연결해야 한다.
6. 동기화 후에는 기존 `verify_c03_handoff()`로 정상 상태와 현재 잠금을 검증하고 지정 수신 실행자가 기존 `release(recovery_verified=True)` 경로를 사용한다. 이후 C-04 준비·120초 이상 관찰·판정으로 연결한다.

`read_failure()`는 매 호출마다 `application`, `deployment`, `replica_set`, `pods`, `lease`, `checked_at`을 새 원본으로 반환해야 한다. C-03 proof 연결에는 고정 Git blob의 `values_bytes`도 필요하다. C-03 reader가 반환한 Application·Deployment·Lease는 별도의 전후 조회와 같아야 한다. 파일을 재사용하며 `checked_at`만 갱신하는 구현은 허용되지 않는다.

## 실패와 재시도

- 잘못된 실습·대상·revision·이미지·generation·Pod UID, 이전 실패·오래된 snapshot, 다른 owner, Lease 재생성, 만료된 기한은 중지 사유다.
- CAS 충돌은 원래 holder를 유지한다. 교체 응답이 유실되면 실제 holder가 이미 c3일 수 있으므로 현재 Lease를 읽고 확인해야 한다. 자동 재인계와 자동 해제는 하지 않는다.
- 인계 이후 승인/CI 검증 실패나 대상 변경·기한 초과는 지정 c3 holder를 유지한다. 복구를 확인하기 전 정상 해제 API를 호출하지 않는다.
- 기존 로컬·원격 감시는 같은 클러스터/Argo/Lease에 의존한다. 잠금 보유 중 감시가 원복을 거부하는 경우를 새 실행 검토안의 수동 원복 절차에 포함해야 한다. 취소된 일정과 승인은 재사용하지 않는다.

## 로컬 검증

저장소 체크아웃에 C-03 승인·CI·병합·동기화 코드와 통합 시험을 함께 포함한다. 개인 소스 아카이브는 필요하지 않다.

```sh
PYTHONPATH=ai-ops/src python -m unittest discover -s ai-ops/tests -p 'test_c_failure_handoff*.py'
```

GitHub/Kubernetes 원본과 Lease 서버는 합성 대역이다. 실제 실행은 현재 대상·권한·원복 감시·실행별 승인·정확한 head의 리뷰를 별도로 확인한다. 과거 승인을 재사용하지 않는다.
