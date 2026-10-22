# B-06 상시 CI 전달기·실습 worker 배포 준비

상태: 구현·시험 중. 클러스터 상시 배포와 실제 최신 CI 자동 접수는 아직 미검증이다.

## 역할과 처리 흐름

전달기가 고정된 실습 CI 완료 목록을 읽는다 → artifact 해시·출처·실패/복구 관측을 검증한다 → 전용 영속 큐에 접수한다 → 별도 실습 worker가 현재 클러스터를 읽어 조사한다 → 규칙 보고서를 저장한다. 이 구성은 운영 worker를 실습 대상으로 바꾸지 않는다. 외부 모델 호출·운영 변경 권한은 추가하지 않는다.

`ci_token_reader.py`는 전용 Secret 파일로 GitHub API를 읽는다. GitHub CLI나 개인 로그인 복사는 필요 없다. artifact의 HTTPS 서명 주소로 이동할 때 인증 헤더를 제거하며 허용된 artifact 호스트만 따른다. 고정 경로·응답 크기·요청 timeout을 검사한다. 토큰/원문 API 오류는 로그에 출력하지 않는다. 최소 권한과 만료는 GitHub에서 관리하며 읽기 성공만으로 다른 권한의 부재를 보장하지 않는다.

## 격리와 저장

`ci_service.py`와 `prepare_ci_service.py`는 `ai-ci` namespace에 두 Deployment를 만들며 기본 replicas는 0이다. worker에는 실습·지정 Argo Application 읽기 RoleBinding과 짧은 수명 Kubernetes token만, relay에는 GitHub Secret만 마운트한다. relay ServiceAccount에는 클러스터 읽기 권한이 없다. 두 Pod는 같은 UID와 1Gi 암호화 RWO PVC를 사용하며 relay를 worker와 같은 노드에 배치한다. worker를 먼저 준비한 다음 relay를 활성화한다. 노드 장애 때 두 프로세스의 일시 중단이 있을 수 있다.

worker는 내부 API /32·Prometheus·DNS만 접근한다. relay는 DNS와 사설/링크 로컬을 제외한 외부 TCP 443을 허용한다. 기본 NetworkPolicy는 GitHub FQDN만 제한하는 기능이 없으므로 이 정책을 GitHub 전용 도메인 필터라고 주장하지 않는다. 네트워크 정책은 Pod 단위이고 별도 Pod로 분리했다. 같은 UID가 공유하는 큐는 상호 신뢰 경계이며 OS 관리자/동일 자격 실행을 방어하는 별도 보안 경계가 아니다.

전용 토큰은 `ci-relay-github` Secret의 `token` 키로 운영자가 준비한다. 원본은 Git 밖 0600 파일이며 manifest·이미지·artifact·보고서에 포함하지 않는다. K8s Secret 마운트에서만 relay가 읽고 worker는 마운트하지 않는다. Kubernetes Secret은 암호 저장소와 같다고 가정하지 않고 기존 EKS 저장 암호화와 RBAC 범위를 확인한다.

## 설치·검증 순서

1. 운영 설치와 별도 실습 Pod 4개·Prometheus를 검증한다. 재설치한 현재 앱 digest로 `ai-ops/lab/deployment.json`을 검토·main 반영한다.
2. 현재 실습 manifest로 GitOps 계획을 만들고 고정 main SHA·Role·서버 정책·수동 sync를 검증한다. CI baseline 변수와 Argo CI 조회/sync 토큰은 새 클러스터에 맞춰 갱신한다. 운영 Project 권한은 늘리지 않는다.
3. 토큰 transport·배포 builder가 포함된 새 이미지 CI 승인·등록·검사 0건을 확인한다. 이전 이미지에 없는 코드를 ConfigMap으로 주입하지 않는다.
4. 현재 manifest와 승인 digest로 `prepare_ci_service.py`를 실행한다. 출력은 Secret 값 없는 비활성 정의다. 계정·컨텍스트·동명 리소스 소유권을 확인하고 server dry-run 후 적용한다.
5. 토큰 파일을 출력하지 않고 전용 Secret에 입력한다. worker 1개를 먼저 활성화하고 probe 성공 뒤 relay 1개를 활성화한다. worker에서 Secret·exec·운영 읽기 거부, relay에서 API 읽기 거부, GitHub 목록 조회·토큰 미마운트 상태를 검증한다.
6. 검토된 실습 실패 소스 CI 한 건을 실행한다. GitHub 환경 승인은 사용자가 수행한다. CI의 원복·artifact 보존 완료 뒤 relay 자동 접수→큐 완료→같은 출처 보고서를 대조한다. 최초 실패를 보존하므로 CI 자체의 failure를 AI 조사 실패로 단정하지 않는다. 오래된 기록을 새 자동 발견 실측으로 계산하지 않는다.

## 정리·재설치

전체 정리에서는 **실습 namespace와 EBS CSI 삭제 전에** relay를 0개로 중지하고 worker도 중지한다. `ai-ci/ci-queue`의 PV·실제 Volume ID를 기록한 뒤 PVC를 삭제하고 EBS 부재를 확인한다. `ai-ci` namespace와 두 `ci-worker-read` RoleBinding(ai-ops-lab/argocd)을 제거한다. GitHub 자격은 로컬 원본을 보존하되 클러스터 Secret은 namespace 삭제로 제거된다. 운영 PVC나 다른 프로젝트 볼륨을 삭제하지 않는다. 노드·클러스터가 먼저 삭제된 재개에서는 기록한 available/unattached 볼륨의 소유권을 확인한 뒤 별도로 정리한다.

재설치는 새 API 주소·이미지 digest·실습 UID·새 Argo 자격을 검증하고 위 절차를 다시 수행한다. 전달기만 먼저 실행하지 않는다. 현재 15분 이내 실패 완료 목록 20개 조회 제한이 있으며 전달기 중단 기간의 전체 이력 처리를 보장하지 않는다.

### 최초 배포에서 발견한 시작 조건

전달기에는 `--config /etc/ai-ops --store /data/incidents --watch`를 모두 전달해야 한다. 공유 PVC를 사용하는 두 Pod에는 `fsGroupChangePolicy: OnRootMismatch`를 지정해 후발 Pod가 0700/0600 큐 권한을 재귀 변경하지 않도록 한다. 기존 잘못된 정책으로 이미 권한이 바뀌었다면 두 프로세스를 먼저 중지하고 전용 PVC의 소유권·접수 상태를 확인한 뒤 같은 UID로 비공개 권한을 복구한다. 강제 볼륨 분리나 운영 PVC 수정으로 우회하지 않는다. worker 교체·노드 이동 시에는 relay를 먼저 0개로 중지하고 worker가 새 노드에서 Ready가 된 뒤 relay를 활성화해 RWO 연결 충돌을 피한다.
