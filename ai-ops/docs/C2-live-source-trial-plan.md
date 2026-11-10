# C-02 활성 GitOps 소스 전환 실습 준비

상태: **격리 실패 주입·v2 초안 저장·정상 원복·실습 후 자격 회전 완료.** 대상은 `ai-ops-lab/lab-api` 한 개다. PR 생성·승인·병합·AI의 복구 실행은 C-03 범위로 남긴다.

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

## 2026-10-30 실제 실패 실습 결과

- 실행별 검토안 `c2-v2-trial-review.md`의 SHA-256 `e0789ff99321b05d78d47fe0a40b257015aaab6bf31e9ba8545825fbcf09c396`에 대한 사용자 승인을 받은 뒤, GitHub와 로컬의 독립 원복 감시가 모두 준비된 것을 확인했다. 실패 커밋 `a71eba53368f5ecb8ad1fe730f04537f8e50925d`를 실습 앱에만 적용했고, 없는 이미지 digest 때문에 Pod가 `ErrImagePull`이 됐다.
- 일회성 조사 Job이 사건 `inc-1370fb8b96f2432590fb86c45a55fec2`와 실행 `run-07d09905752c401aa1330a56ae14cd2d`를 만들었다. TLS 조회, 정상 CI 영수증, ECR과 활성 GitOps 소스, 현재 실패 Pod의 대조와 초안 직전 재조회가 모두 통과했다. 인증된 해결 요청 `creq-888f8b8789704d2ca4efa7a7a678a08a`에 연결된 v2 초안 `cprop-c9195485990648cfa48dea420a8d4d04`가 `draft`로 저장됐다. 제안 해시는 `afd5fb3440b0a7787d3b8c4a0ded581445e37444bd66db27f0b68e047fc83c74`, diff 해시는 `9882bb6db0b6d9dc88b13a8799883d10a5017740c9baaac0384350c11d10d2f8`이다.
- 목표 확인 직후 정상 커밋 `0fd3e2e4b2cd00d8684b94e83c9b7b0abdf75a09`로 원복했다. Argo `Synced/Healthy`, 정상 이미지 digest `sha256:db0022dbc03199cddf5d7bc802977a9380e3dc5859511f7821014e869307082e`, Ready 1/1, 빈 Lease를 확인했다. [GitHub 기한 원복 감시](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/41018858403)는 `success`로 끝났고 로컬 감시도 정상 기준선 복구를 확인했다.
- 초안 작성 감사 결과는 `proposal_allowed=false`, `draft_only_c3_approval_required`, `cluster_or_github_write_performed=false`다. 이 값은 **초안 작성 코드의 변경 없음**을 뜻하며, 실습 주입과 원복은 별도 승인된 작업으로 실행됐다. 실제 복구 변경·PR 생성·유료 모델 호출은 수행하지 않았다. 교체된 앱 Pod `lab-api-6c9cbdc496-vxkvj`의 정상 CI Role 이름 권한, GitHub `NORMAL_CI_BINDING`, 클러스터 `ci-normal-binding`을 같은 결합값으로 갱신하고 relay·worker를 순서대로 재시작해 Ready를 확인했다. Prometheus의 이전 Pod 시계열이 정리된 뒤 정상 관측 CI 준비 검사가 통과했다.
- 새 worker Pod `ci-worker-5d874746df-dvpht` 한 개의 `pods get`·`pods/portforward create`만 남기도록 C-02 Role·Binding을 회전하고, 1시간 단기 자격을 재발급했다. 이 자격으로 새 Pod 조회와 사건 요약 TLS 조회는 성공했고 이전 Pod·Secret 조회와 무인증 HTTP 요청은 거부됐다. 로컬 포트 전달과 운영자 조회 서버는 검증 뒤 종료했다.

## 남은 조건

- GitHub·로컬 원복 경로는 실제 실패 실습에서도 정상 상태를 확인했다. 이번에는 목표 확인 직후 수동 원복했고 두 기한 감시는 정상 복구를 검증했다. 두 감시가 동시에 중단되는 경우의 자동 원복은 검증하지 않았다.
- 활성 revision 전용 조사 Job의 실제 적용과 장애 중 사건·요청 연결, v2 초안 저장까지 확인했다. 실습 후 Pod 결합값·단기 조회 자격 회전도 완료했다.
- C-02의 성공 기준은 **v2 초안의 근거·diff 해시를 저장하는 것까지**다. 사람의 특정 변경 승인, PR 병합, 클러스터 복구 실행은 포함하지 않는다.
- 자동 원복 전에 초안을 만들지 못하면 실패 원인을 기록하고 정상 상태만 복구한다. 시간 제한을 늘리거나 검사 조건을 느슨하게 하지 않는다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
