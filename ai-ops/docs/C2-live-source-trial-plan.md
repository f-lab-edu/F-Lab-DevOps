# C-02 활성 GitOps 소스 전환 실습 준비

상태: **격리 실패 주입·사건 수집·정상 원복 완료, v2 변경안 초안 미완료.** 대상은 `ai-ops-lab/lab-api` 한 개다. C-02의 v2 변경안 초안을 실제 장애에서 검증하기 위한 실습이며, PR 생성·승인·병합·AI의 복구 실행은 C-03 범위로 남긴다.

## 왜 별도 실습이 필요한가

기존 S10은 실패 커밋을 한 번 동기화하지만 Argo Application의 고정 `targetRevision`은 정상 커밋을 유지한다. 따라서 현재 Deployment의 실패 이미지와 활성 `values.yaml`이 달라 `values_stale`로 차단된다. S10의 공유 Lease가 잡혀 있는 동안에는 `lab_locked`도 차단한다. 실패를 관측했다는 이유만으로 이 검사를 완화하지 않는다.

## 실행 전 준비

첫 읽기 검사에는 `prepare_c2_source_trial.py`를 사용한다. 관리자 kubeconfig, 고정 정상·실패 커밋 SHA, 개인 전용 출력 경로를 입력하면 현재 Argo·Deployment·Lease, 두 커밋의 단일 values 변경, AWS 계정과 ECR의 정상 이미지 존재·실패 이미지 부재를 확인한다. 출력의 `ready_to_inject=false`는 의도된 결과다. 이 명령은 독립 원복 주체나 활성 revision 전용 조사 접수를 설치하지 않는다.

1. 현재 ECR에 존재하는 정상 digest, 고정 정상 revision, 실습 앱 UID·Deployment UID·Ready 1/1, Argo `Synced/Healthy`, 빈 공유 Lease를 기록한다. 모델 gateway·egress proxy는 복제수 0으로 둔다.
2. 정상 관측 CI의 현재 Pod 결합값·Role·GitHub 환경 변수·`ai-ci/ci-normal-binding`이 일치하는지 확인하고, **새 정상 CI 한 건**의 성공 영수증을 확보한다. 이전 Pod에 결합된 영수증을 새 실습의 정상 근거로 재사용하지 않는다.
3. 정상 커밋과 `ai-ops/lab/values.yaml`의 digest 한 값만 다른 실패 커밋을 준비한다. ECR에 실패 digest가 없고 정상 digest가 있음을 재확인한다. 실패 커밋은 `main`에 병합하지 않는다.
4. 실행별 검토안에 두 커밋 SHA, 정상·실패 digest, 대상 UID, 최대 실패 관측 시간, 별도 원복 주체와 중단 조건을 적어 승인받는다. 준비·읽기 검사만으로 실패를 주입하지 않는다.

### 2026-10-30 정상 상태 점검 기록

- 현재 Pod에 다시 결합한 [정상 관측 CI #15](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/41005933607)는 성공했다. 영수증은 기준선 유지·잠금 해제·생성 자료 정리·외부 모델 호출 0건을 기록했고, 전달기가 한 번 접수했다.
- [기한 원복 #1](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/41006501331)은 실습 CI 역할에 없는 Kubernetes Application 조회 권한 때문에 실패했다. [PR #127](https://github.com/f-lab-edu/F-Lab-DevOps/pull/127)에서 기존 Argo 실습 토큰으로 조회하도록 수정했고, [기한 원복 #2](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/41007653059)는 **정상 상태에서** 성공했다.
- 별도 로컬 감시 실행은 기존 관리자 kubeconfig의 소유자·권한·`argocd` namespace를 검사한 뒤 정상 대상에서 기한 후 확인까지 성공했다. 로컬 Argo CLI 3.5.3의 공식 SHA-256 `76efc71c00bc3ffeda5daa277d990e3d89b3628b2440fc7dd38aca79c63b15e0`을 대조하고, 해당 버전으로 전체 정상 상태 점검을 다시 통과했다. 실제 실패 상태의 `set`·`sync` 경로는 아직 실행하지 않았다.
- 이 점검은 실패 주입 승인이나 실제 복구 성공의 근거로 사용하지 않는다. 실패 이미지는 적용하지 않았고 앱은 정상 revision의 `Synced/Healthy`, Ready 1, 빈 잠금 상태다.

## 승인 후 제한된 실행 순서

1. 별도 [C-02 기한 원복 workflow](../../.github/workflows/ai-ops-c2-rollback.yml)와 로컬 `run_c2_rollback_guard.py --core` 실행이 동일한 정상 revision·현재 Application UID·Deployment UID·두 이미지와 UTC 기한을 고정한다. GitHub 환경 승인을 거쳐 실제 Job의 **감시 준비 완료** 로그를 확인하고, 로컬 실행도 같은 로그를 출력한 뒤 살아 있는지 확인한다. 두 실행 중 하나라도 대기·실패·취소 상태면 시작하지 않는다. 로컬 실행은 소유자 전용 kubeconfig의 현재 namespace를 `argocd`로 설정하고 검증된 Argo CLI 3.5.3을 사용한다. 두 실행기는 대상 UID·namespace·수동 Argo 소스와 이미지 두 개만 허용하며 기한에 정상 revision으로 되돌려 `Synced/Healthy`·Ready 1/1을 확인한다.
2. Argo Application의 `targetRevision`을 승인된 실패 커밋으로 전환하고 **그 커밋만** 수동 동기화한다. 운영 앱이나 다른 실습 Deployment는 변경하지 않는다. 이미지 취득 실패·Ready 0, Argo sync revision·operation 성공, 공유 Lease가 비어 있음을 확인한다.
3. 기존 `prepare_c2_live_job.py`는 S10 실행 ID와 Lease에 결합돼 있어 이 실습에 그대로 재사용하지 않는다. 별도 `prepare_c2_active_capture.py`가 정상·실패 커밋의 단일 digest 변경, 현재 활성 revision·Application/Deployment UID·Pod/ReplicaSet 소유 관계·이미지 취득 실패·빈 Lease를 확인한 뒤 기존 worker 이미지·설정·PVC를 사용하는 일회성 Job 정의만 개인 파일에 저장한다. 실제 적용은 실행별 승인 후 별도 단계에서 한다.
4. `ai-ci/ci-worker`의 v3 사건을 TLS로 읽고, 새 정상 CI 영수증·ECR·현재 실패 Pod·인증된 해결 요청을 `audit_c2_live_preflight.py`로 결합한다. 초안 직전 30초 이내 재조회가 성공할 때만 v2 `draft`를 개인 저장소에 기록한다. 어느 근거든 불일치하면 제안을 만들지 않는다.
5. 성공 여부와 관계없이 제한 시간 안에 정상 revision으로 Application을 되돌려 수동 동기화한다. Argo `Synced/Healthy`, 정상 digest, Ready 1/1, 빈 Lease, 변경안이 `draft` 이상으로 진행되지 않았음을 확인한다. 앱 Pod 교체 후 정상 CI와 C-02의 Pod 한정 권한·자격을 새 Pod에 맞춰 회전한다.

## 2026-10-30 실행 결과

- 실행별 검토안의 SHA-256 `733aea730af242ca2b3bf4303ffeead8f663581bb038b1efb0d7750748eae2a8`에 대해 사용자가 격리 실패 주입을 승인했다. 정상 revision은 `0fd3e2e4b2cd00d8684b94e83c9b7b0abdf75a09`, 실패 revision은 `a71eba53368f5ecb8ad1fe730f04537f8e50925d`다. 두 커밋의 `values.yaml` 이미지 digest 한 값만 다르며 실패 커밋은 `main`에 병합하지 않았다.
- [GitHub 독립 원복 #41016598000](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/41016598000)과 로컬 원복 감시가 모두 준비 완료를 출력한 뒤 15:53:52 KST에 실패 revision을 활성 소스로 동기화했다. 없는 ECR digest를 참조한 새 Pod에서 `ErrImagePull`을 확인했다. 기한은 15:58:56 KST였고 운영 앱·유료 모델은 변경하지 않았다.
- 15:54:54 KST에 일회성 `c2-source-capture-41016598000` Job이 요청을 접수했다. worker의 작업 `job-74c354b18fcc451f9bec0c034493369e`는 사건 `inc-90aaf5e09287487eab42f3d264bcbc9c` / 실행 `run-0116102819b54191aeea06bb2fc90279`을 `partial`로 저장했다. 제한된 TLS 조회 결과는 `suspected_cause`, 이미지 실패 근거 3개(`ev-deployment-object`, `ev-event-0`, `ev-pod-0-status`)였다. 로그 1개와 메트릭 5개가 누락됐고 일부 배포 자료가 오래되어 전체 보고서는 부분 결과다. 이 결론은 조회 가능한 근거 범위에 한정한다.
- 조사 접수 뒤 15:55:09 KST에 정상 revision을 수동 동기화했고, 15:55:15 KST에 Argo `Synced/Healthy`와 앱 Ready 1을 확인했다. 정상 digest, 빈 공유 Lease, 모델 gateway·proxy 복제수 0도 재확인했다. 두 독립 원복 감시는 기한에 정상 상태를 확인하고 성공했다. **실패가 기한까지 남아 있을 때 두 감시가 직접 원복하는 경로는 이번에 실행되지 않았다.**
- 복구 뒤 읽기 전용 감사는 `already_recovered`, `proposal_allowed=false`를 반환했다. 장애 중 인증된 해결 요청과 30초 이내 두 번째 원본 재조회까지 마치지 못했으므로 v2 `draft`는 생성하지 않았다. 장애가 사라진 뒤 오래된 사건만으로 변경안을 만들지 않는 차단은 확인했다.
- 원복으로 실습 앱 Pod가 `lab-api-6c9cbdc496-b96vc`로 교체됐다. 정상 관측 CI의 Pod 한정 Role, GitHub `NORMAL_CI_BINDING`, 클러스터 `ci-normal-binding`을 새 Pod에 맞췄고 결합값 세 곳의 동일성을 확인했다. relay·worker를 순서대로 재시작해 Ready를 확인했다. C-02 조회 Role도 새 worker Pod `ci-worker-8485d47645-blt52` 한 개로 회전했고, 1시간 자격으로 재조회한 사건 요약의 `partial`·`suspected_cause`·이미지 근거 3개가 보존됐다. 회전 이후 정상 관측 CI를 새로 실행하지는 않았다.

## 남은 작업과 차단 조건

- 두 원복 감시가 실제 장애 중 살아 있고 기한에 정상 상태를 재확인한 것까지 검증했다. 이번에는 기한 전에 수동 원복했으므로 감시자의 **실패 상태 직접 원복**은 미검증이다. 다음 실패 주입에는 새 실행별 승인이 필요하며, 두 감시와 수동 원복 경로를 다시 준비해야 한다.
- 활성 revision 전용 조사 Job과 v3 사건 저장은 실측했다. 다음에는 장애 창 안에서 인증된 사건 조회, 요청자 확인, 해결 요청, 정상 CI/ECR 근거 결합, 30초 이내 재조회, v2 초안 저장을 순서대로 끝내야 한다. 자동 원복 이후에는 `already_recovered`를 유지하고 초안을 만들지 않는다.
- 원복 뒤 회전한 정상 CI 결합값으로 새 정상 관측 CI 실행은 아직 검증하지 않았다.
- C-02의 성공 기준은 **v2 초안의 근거·diff 해시를 저장하는 것까지**다. 사람의 특정 변경 승인, PR 병합, 클러스터 복구 실행은 포함하지 않는다.
- 자동 원복 전에 초안을 만들지 못하면 실패 원인을 기록하고 정상 상태만 복구한다. 시간 제한을 늘리거나 검사 조건을 느슨하게 하지 않는다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
