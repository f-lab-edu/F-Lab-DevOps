# C-02 활성 GitOps 소스 전환 실습 준비

상태: **설계·선행 조건 정리, 실패 주입 미실행.** 대상은 `ai-ops-lab/lab-api` 한 개다. C-02의 v2 변경안 초안을 실제 장애에서 검증하기 위한 실습이며, PR 생성·승인·병합·AI의 복구 실행은 C-03 범위로 남긴다.

## 왜 별도 실습이 필요한가

기존 S10은 실패 커밋을 한 번 동기화하지만 Argo Application의 고정 `targetRevision`은 정상 커밋을 유지한다. 따라서 현재 Deployment의 실패 이미지와 활성 `values.yaml`이 달라 `values_stale`로 차단된다. S10의 공유 Lease가 잡혀 있는 동안에는 `lab_locked`도 차단한다. 실패를 관측했다는 이유만으로 이 검사를 완화하지 않는다.

## 실행 전 준비

첫 읽기 검사에는 `prepare_c2_source_trial.py`를 사용한다. 관리자 kubeconfig, 고정 정상·실패 커밋 SHA, 개인 전용 출력 경로를 입력하면 현재 Argo·Deployment·Lease, 두 커밋의 단일 values 변경, AWS 계정과 ECR의 정상 이미지 존재·실패 이미지 부재를 확인한다. 출력의 `ready_to_inject=false`는 의도된 결과다. 이 명령은 독립 원복 주체나 활성 revision 전용 조사 접수를 설치하지 않는다.

1. 현재 ECR에 존재하는 정상 digest, 고정 정상 revision, 실습 앱 UID·Deployment UID·Ready 1/1, Argo `Synced/Healthy`, 빈 공유 Lease를 기록한다. 모델 gateway·egress proxy는 복제수 0으로 둔다.
2. 정상 관측 CI의 현재 Pod 결합값·Role·GitHub 환경 변수·`ai-ci/ci-normal-binding`이 일치하는지 확인하고, **새 정상 CI 한 건**의 성공 영수증을 확보한다. 이전 Pod에 결합된 영수증을 새 실습의 정상 근거로 재사용하지 않는다.
3. 정상 커밋과 `ai-ops/lab/values.yaml`의 digest 한 값만 다른 실패 커밋을 준비한다. ECR에 실패 digest가 없고 정상 digest가 있음을 재확인한다. 실패 커밋은 `main`에 병합하지 않는다.
4. 실행별 검토안에 두 커밋 SHA, 정상·실패 digest, 대상 UID, 최대 실패 관측 시간, 별도 원복 주체와 중단 조건을 적어 승인받는다. 준비·읽기 검사만으로 실패를 주입하지 않는다.

## 승인 후 제한된 실행 순서

1. 별도 [C-02 기한 원복 workflow](../../.github/workflows/ai-ops-c2-rollback.yml)가 정상 revision·현재 Application UID·Deployment UID·두 이미지와 UTC 기한을 고정한다. GitHub 환경 승인을 거쳐 실제 Job이 실행 중임을 확인한 뒤에만 장애 주입을 검토한다. 원복 Job이 대기·실패·취소 상태면 시작하지 않는다. 원복 실행기는 대상 UID·namespace·수동 Argo 소스와 이미지 두 개만 허용하며 기한이 되면 정상 revision으로 되돌려 `Synced/Healthy`·Ready 1/1을 확인한다.
2. Argo Application의 `targetRevision`을 승인된 실패 커밋으로 전환하고 **그 커밋만** 수동 동기화한다. 운영 앱이나 다른 실습 Deployment는 변경하지 않는다. 이미지 취득 실패·Ready 0, Argo sync revision·operation 성공, 공유 Lease가 비어 있음을 확인한다.
3. 기존 `prepare_c2_live_job.py`는 S10 실행 ID와 Lease에 결합돼 있어 이 실습에 그대로 재사용하지 않는다. 별도 접수 경로가 현재 활성 revision·Pod/ReplicaSet 소유 관계·실패 이미지를 확인한 뒤 한 건만 접수하도록 구현·검증한다.
4. `ai-ci/ci-worker`의 v3 사건을 TLS로 읽고, 새 정상 CI 영수증·ECR·현재 실패 Pod·인증된 해결 요청을 `audit_c2_live_preflight.py`로 결합한다. 초안 직전 30초 이내 재조회가 성공할 때만 v2 `draft`를 개인 저장소에 기록한다. 어느 근거든 불일치하면 제안을 만들지 않는다.
5. 성공 여부와 관계없이 제한 시간 안에 정상 revision으로 Application을 되돌려 수동 동기화한다. Argo `Synced/Healthy`, 정상 digest, Ready 1/1, 빈 Lease, 변경안이 `draft` 이상으로 진행되지 않았음을 확인한다. 앱 Pod 교체 후 정상 CI와 C-02의 Pod 한정 권한·자격을 새 Pod에 맞춰 회전한다.

## 구현 전 차단 조건

- 별도 기한 원복 workflow와 대상 검증 코드는 구현했지만 **실제 실행·권한·만료 토큰 복원 검증은 아직 없다.** GitHub Actions 자체가 중지되거나 자격이 만료되면 원복이 실패할 수 있으므로 이 workflow 하나만으로 실패 주입을 시작하지 않는다. 활성 revision 전용 조사 접수와 장애 중 사건·요청 연결도 아직 준비되지 않았다.
- C-02의 성공 기준은 **v2 초안의 근거·diff 해시를 저장하는 것까지**다. 사람의 특정 변경 승인, PR 병합, 클러스터 복구 실행은 포함하지 않는다.
- 자동 원복 전에 초안을 만들지 못하면 실패 원인을 기록하고 정상 상태만 복구한다. 시간 제한을 늘리거나 검사 조건을 느슨하게 하지 않는다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
