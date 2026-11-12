# C-05 실행 통제 평가와 종료 절차

현재 구현 범위는 운영자 Mac에서 시작하는 격리 이미지 복구다. 로컬 시험의 통과와 실환경 복구의 성공은 구분한다. 전체 C 완료에는 새 실행의 정확한 사람 리뷰·CI·병합·동기화와 이후 C-04 관찰 근거가 추가로 필요하다.

## 요구사항과 검증 근거

| 기준 | 확인하는 동작 | 로컬 근거 | 실환경 종료 조건 |
|---|---|---|---|
| R1 | 설명·초안·미승인 요청은 쓰기를 수행하지 않음 | `test_c_recovery`, `test_c_trial_ready` | 승인 전 준비와 장애 실행을 구분 |
| R2 | 승인한 digest 한 필드·정확한 head만 병합·동기화 | `test_c_trial_runtime`, `test_c_merge_guard`, `test_c_sync_guard`, `test_c_verification_collect` | **새 C-03 인계 및 C-04 최소 120초 관찰이 필요함** |
| R3 | 다른 사용자·만료·base/head/대상 변경 거부 | `test_c_approval`, `test_c_execution_preflight` | 해당 실행의 실제 리뷰·CI 원본 대조 |
| R4 | 중복 실행 금지·응답 유실 시 재시도 금지·중단 상태 확인 | `test_c_trial_execution`, `test_c_trial_ready`, `test_c_execution_control` | 실측과 모의 재시작 근거를 구분 |
| R5 | 실패 단계·불확실한 요청을 보존하고 원복 또는 중지 | `test_c_trial_runtime`, `test_c_execution_control`, `test_c_verification` | 정상 원복·잠금·자격 회수 실물 확인 |
| R6 | 기존 CI가 원복한 대상은 재변경하지 않음 | `test_c_proposal_cli`, `test_c_approval` | 현재 정상 상태를 새 복구 성공으로 바꾸지 않음 |
| R7 | 실행 전/중 취소 시 미실행 단계만 중지하고 적용 사실 기록 | `test_c_proposal_lifecycle`, `test_c_execution_control`, `test_c_verification_collect` | 이미 반영한 Git 변경과 앱 원복을 구분 |
| R8 | 다른 환경·파일·조치·identity는 거부 | `test_c_recovery`, `test_c_approval`, `test_c_execution_control` | 운영 환경으로 범위를 확대하지 않음 |

실행 중 취소·재시작·API 응답 유실은 모의 시험에서 외부 함수를 대체해 검사한다. 이 표를 실제 API 요청 도중 프로세스를 강제 종료하거나 모든 실패를 클러스터에 주입했다는 근거로 사용하지 않는다. 새 실환경 실행의 관측 기록 없이 R2나 전체 C를 완료로 표시하지 않는다.

## 실행 취소

`ExecutionControl`은 새 실행의 실제 활성화 계획과 기존 소유자 기록에 결합된다. 동일 실행의 `execution-journal.json`을 배타 생성하고 외부 쓰기 **시도 전**, 확인 응답 **검증 후**에 원자 교체·파일 fsync로 갱신한다. 디스크 기록이 실패하면 새 복구 쓰기는 중지한다. 기존 승인 범위의 안전 원복은 장부 기록 실패 때문에 막지 않는다. 전원 손실이나 파일시스템 자체의 내구성까지 입증한 것은 아니다.

취소 명령은 현재 `gh` 사용자가 고정 운영자인지 확인하고, 개인 소유자 파일의 trial ID/계획 hash에 맞춘 취소 파일만 새로 만든다. 실행기는 이 파일과 기존 제안 저장소의 취소/만료 상태를 병합 및 각 Argo 쓰기 직전에 다시 확인한다. 요청과 외부 API의 처리는 하나의 트랜잭션이 아니므로, 마지막 검사 뒤 이미 시작한 요청의 결과는 확인 또는 불확실 상태로 남길 수 있다.

```sh
PYTHONPATH="$runtime_root/ai-ops/src" python "$runtime_root/ai-ops/scripts/cancel_c4_trial.py" \
  --plan "$trial_root/live/active-plan.json" --run-dir "$trial_root/live"
```

- 병합 전 취소: 병합 자격 발급과 미실행 쓰기를 중지한다.
- 병합 후 취소: 확인한 merge SHA를 보존하고 남은 Argo set/sync를 중지한다. Git commit을 자동 revert하지 않는다.
- Argo set 뒤 취소: 이미 바뀐 revision과 남은 sync를 구분한다. 실습 소유자는 기존 승인 범위의 고정 정상 SHA 원복을 수행할 수 있다.
- C-03 뒤 취소: 이미 적용된 복구를 보존하고 남은 읽기 관찰을 중지한다. 관찰 루프는 읽기 묶음 사이와 대기 중 최대 1초 간격으로 검사한다. 진행 중인 I/O는 기존 제한 시간까지 걸릴 수 있다.
- API 응답 유실: `pending_or_unconfirmed_operation`을 남긴다. 성공이나 실패를 추측해 같은 요청을 다시 보내지 않는다.

결과의 `execution_control`에는 취소 여부, 확인한 변경, 남은/응답 불확실 작업과 `automatic_resume_allowed=false`를 기록한다. 안내 연결기는 종료 신호를 받으면 Python의 정리 경로로 들어가 자신이 발급한 App 설치 토큰의 회수를 시도한다. 회수 API가 실패하거나 강제 종료되면 성공으로 표시하지 말고 남은 자격의 유효기간과 후속 확인을 기록한다.

## 중단한 실행의 원복

기존 프로세스가 살아 있거나 Lease UID/holder가 달라졌으면 재개하지 않는다. 새 병합·재주입을 자동으로 재개하는 명령은 제공하지 않는다. 기존 소유자가 승인받은 고정 정상 SHA로 돌아가는 경로만 지원한다.

병합한 revision을 앱에 설정한 뒤 중단됐다면 로컬 장부의 SHA만 믿지 않는다. 장부의 계획/소유자·제안·정확한 PR head·고정 병합 App·실제 현재 main·단일 부모와 values 한 필드 변경을 GitHub 원본에 다시 대조한다. main이 전진했거나 실제 원본이 다르면 원복도 중지하고 수동 조사한다. 확인한 merge SHA는 기존 실습 소유권 검사에서만 허용한다.

```sh
PYTHONPATH="$runtime_root/ai-ops/src" python "$runtime_root/ai-ops/scripts/restore_c4_trial.py" \
  --plan "$trial_root/live/active-plan.json" \
  --authorization "$trial_root/live/derived-authorization.json" \
  --owner "$trial_root/live/result-owner.json" \
  --handoff "$trial_root/live/result-handoff.json" \
  --journal "$trial_root/live/execution-journal.json" \
  --kubeconfig "$operator_kubeconfig" --output "$trial_root/owned-rollback.json"
```

응답을 잃어 병합을 확인하지 못한 상태에는 확인된 merge SHA를 만들어 넣지 않는다. 원본과 잠금 소유권을 조사하고 필요한 별도 승인을 받아 처리한다. 다른 실행의 잠금을 빼앗지 않는다.

## PR 안내까지의 조회

사건 원본 수집의 시작·끝 Application/Deployment/Lease 조회는 각각 독립된 읽기 묶음으로 병렬화한다. 정상 CI 메타데이터·클러스터·현재 계정·TLS 연결 정보도 같은 단계 안에서 병렬로 읽고, 계정/연결을 확인한 뒤 ECR 두 digest와 Pod를 별도 묶음으로 읽는다. 읽기 사이에는 대조 단계를 유지한다. 실패·불완전 응답을 과거 성공으로 대체하지 않으며 리뷰·원복 기한을 늘리지 않는다.

안내 연결기의 `post-state.json.stage_history`에 각 실제 단계 진입 시각을 기록한다. 이 변경으로 실환경 시간이 줄었다는 주장은 새 실행에서 측정한 뒤에만 한다.

## 복구 조회 예산과 거절 진단

실습의 GitHub 조회기는 현재 시각부터 **기존 소유 원복 시각까지 남은 정수 초**를 사용하며 최대 180초로 제한한다. Kubernetes/ECR 수집 중에도 소진되는 일반 조회기의 기본 45초를 이 실행 구간에 재사용하지 않는다. 8초 미만이 남으면 시작하지 않는다. 개별 GET 8초, 실시간 자료 15초, 리뷰·소유 원복·독립 감시의 고정 기한과 쓰기 직전 검사는 유지한다.

실행이 거절되면 `approval_rejection_codes`에 허용 목록의 고정 코드만 보존한다. 알려지지 않은 승인 거절은 `approval_rejected`로 줄이고 일반 예외의 원문·API 응답·자격은 복사하지 않는다. 이 보완은 009의 정확한 거절 사유를 소급 확정하지 않는다. 009는 기한 내 사람 리뷰/CI 뒤 `ApprovalRejected`로 멈춰 정상 원복됐으며, 상세 코드를 저장하지 않았으므로 45초 예산 문제를 그 실행의 확정 원인으로 쓰지 않는다.

`rejection_source_frames`에는 신뢰한 `ai-ops/src/ai_ops`·`ai-ops/scripts`의 상대 소스 경로·함수명·줄 번호만 최대 8개 남긴다. 예외 원문·locals·절대 경로·외부 프레임은 남기지 않는다. 입력 오류를 숨긴 채 다음 장애 실습으로 원인을 추측하지 않기 위한 진단이다.

## Application 관측 갱신의 비교 경계

전후 원본 비교는 Application의 `metadata.resourceVersion`이 다른 비어 있지 않은 문자열로 바뀌고 `status.reconciledAt`이 현재 시각을 넘지 않으며 엄격히 전진할 때에만 이 두 필드를 제외해 비교한다. 버전은 불투명 문자열로 취급한다. 하나만 바뀌거나 시간이 역행·미래·누락·잘못된 형식이면 거절한다. 현재 실제 Application CRD의 `subresources`는 `{}`여서 상태 갱신도 generation을 올린다. 위 두 관측 필드가 함께 바뀌고 나머지 원본이 모두 같을 때에만 양의 정수 generation의 단일 증가(+1)를 허용한다. 감소·타입 변경·2 이상 증가·generation 단독 변경은 거절한다. UID·managedFields·삭제 시각·annotations·전체 spec·나머지 status는 동일해야 한다. Deployment·Lease의 resourceVersion을 포함한 전체 원본과 values 바이트 비교도 유지한다. 조회 전후 단계, 신선도·증거·승인·CI·원복 기한 검사는 그대로다.

010은 정확한 head의 기한 내 사람 리뷰·필수 CI 뒤 `execution_preflight_input_unavailable_or_invalid`로 중지하고 정상 원복됐다. 로컬에서 resourceVersion/reconciledAt만 함께 갱신해 같은 거절을 재현했지만, 당시 내부 예외 위치를 저장하지 않아 **010의 확정 원인으로 단정하지 않는다.** 이후 정상 앱의 읽기 전용 watch에서는 generation·managedFields도 바뀌는 갱신을 관찰했다. 별도 정상 watch에서 조회 시각 갱신·generation 단일 증가와 나머지 원본 동일성을 확인했다. generation은 실제 CRD에서 확인한 단일 상태 갱신만 위 조건으로 구분하며 managedFields 변경은 허용하지 않는다. 보완 후 실제 C-03/C-04 성공은 새 준비와 실행별 승인·새 PR 리뷰로 별도 검증한다.

## 관측 시각과 해결 요청의 순서

012는 시작 예산 안에 장애를 주입하고 사건을 접수했지만, 저장된 관측 시각이 요청 생성 시각보다 2.009996초 뒤여서 사건 결합을 거절했다. 사건·대상·bundle/report/failure 결합과 Pod/Deployment/image 해시는 일치했다. 새 PR 없이 소유 정상 원복·HTTP 200·빈 Lease·자격 회수를 확인했다. 어느 호스트의 시계가 원인인지는 이 자료만으로 단정하지 않는다.

인증된 운영자 서비스는 실제 현재 UTC보다 미래인 이미지 장애 관측으로 해결 요청을 저장하지 않는다. 자동 연결기는 기존 안내 마감(리뷰 마감에서 최소 리뷰 시간을 뺀 시각) 안에서 최대 5초만 실제로 기다린다. 대기 전 투영을 보존하고, 대기 후 동일 사건 투영을 다시 읽어 비교한 뒤 요청을 한 번 생성한다. 5초 밖의 미래·마감 초과·시계 점프·조회 실패·투영 변경은 중지한다. 요청 시각 소급·시계 조정·시간 검증 완화·요청 쓰기 재시도는 하지 않는다.

새 제안의 첫/갱신 입력은 개인 `proposal-inputs.json`에 보존해 첫 차단 사유가 상위 판정에 덮여 사라지지 않게 한다. 후속 연결기의 예외도 고정 거절 코드와 신뢰한 소스 위치만 진단에 추가한다. 원문 사건 자료는 공개 문서에 복사하지 않는다.

## 최종 데모와 완료 판정

011은 새 준비의 기한 내 실제 승인을 받은 뒤 `_inject`의 시작 직전 15초 예산 검사에서 중지했다. `fault_injected=false`이고 실행 장부의 events/confirmed는 비어 있으며 pending_operation도 null이다. 정상 앱·HTTP 200·빈 Lease·자격 회수를 별도로 확인했다. 이 실행의 새 복구 PR이나 C-04 성공은 없다.

기동 직전 조회에서 고정 원격 workflow run과 jobs의 독립 GET만 함께 읽는다. 로컬 소스/PID/marker 검증은 원격 조회 전에 수행하며, 현재 Lease는 원격 조회 뒤 새로 읽어 그 사이의 소유권 변경을 놓치지 않는다. run/step·정확한 guard 신원·activation·소유권·양쪽 ACK를 기존 조건대로 대조한다. 조회를 성공으로 대체하거나 쓰기를 병렬화하지 않는다. **시작 예산 15초, 리뷰 210초, 소유 원복 240초, 독립 감시 300초, 관찰 120초는 유지한다.** 실환경 시간 단축과 성공 여부는 새 실행에서 측정한다.

1. 현재 정상 CI·worker/PVC·정상/실패 이미지·Git 보호·원복 자격 부재를 대조한다.
2. 양쪽 감시 준비 후 현재 준비 영수증의 새 실행 승인을 받는다.
3. 실제 실패의 조사/원인 설명·해결 요청·제안·새 PR을 연결하고 즉시 안내한다.
4. 정확한 head에 대한 실제 사람 리뷰·필수 CI 후 조건부 병합·정확한 merge SHA 동기화를 검증한다.
5. 새 C-03 인계 이후 HTTP·지표·경보를 최소 120초 수집하고 해결/미해결/검증불가를 보고한다.
6. 감시·임시 자격·Secret·소켓·자기 완료 Job 정리를 실제 확인한다. 기존 로그인/App 키/PVC와 사건 근거는 보존한다.
7. 실제/모의 범위와 미측정 항목을 구분해 작업 리스트·인계·완료 판단을 갱신한다. AI 포트폴리오 재개는 사용자의 구현 완료 통보를 따른다.

이 단계는 Terraform이나 새로운 유료 모델 캠페인을 요구하지 않는다. 기존 모델 보고서의 정확도·독립 평가·장기 운영 성과를 새로 측정한 것으로 표시하지 않는다.
