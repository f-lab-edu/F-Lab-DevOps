# B-06 S10-02 실습 GitOps·권한·공유 잠금 준비

2026-10-21 **정의 작성과 서버 dry-run·공유 Lease 실측을 완료했다. 실제 Argo 등록·배포 인계·CI 자동 롤백은 아직 실행하지 않았다.** [검증 요약](assets/B6-s10-gitops-preparation.json)을 함께 관리한다.

## 무엇을 구현했고 어떻게 동작하는가

기존 S3는 운영자가 실습 앱 이미지를 직접 바꾼 뒤 원래 이미지로 복구했다. S10은 CI가 Git에 기록한 실패 버전을 Argo로 적용하고 자동으로 이전 버전에 되돌리는 과정을 확인한다. 같은 Deployment를 두 방식으로 동시에 변경하지 않도록 준비하는 단계다. AI가 해결 권한을 얻는 C단계와는 별개다.

검증된 기존 실습 정의를 읽는다 → Secret 값 없이 앱 Deployment 하나를 GitOps 경로에 둔다 → 전용 Argo Project·수동 Application·권한·대상 제한 정책을 만든다 → 기존 실행기와 공유하는 잠금을 확인한다 → 서버 dry-run으로 현재 앱 spec 불변을 비교한다 → 운영/실습 식별자를 비교하고 잠금을 해제한다. 실제 동기화는 새 파일이 포함된 검토 커밋이 준비된 뒤에만 진행한다.

| 파일 | 역할 |
|---|---|
| [lab/deployment.json](../lab/deployment.json) | GitOps가 관리할 `ai-ops-lab/lab-api` 한 개. 승인된 이미지 digest와 기존 Pod 정의를 유지 |
| [b6_gitops.py](../src/ai_ops/b6_gitops.py) | 기존 실습 정의 검사, Argo·RBAC·서버 검증 정책 생성, 허용 범위 대조 |
| [b6_coordination.py](../src/ai_ops/b6_coordination.py) | 수동 배포 소유권 확인과 공유 Lease 취득·해제·지표 창 검사 |
| [prepare_b6_gitops.py](../scripts/prepare_b6_gitops.py) | 개인 폴더에 계획 생성. 선택적으로 서버 dry-run과 잠금 점검 수행. 실제 적용 기능 없음 |
| [run_b6_scenario.py](../scripts/run_b6_scenario.py) | 기존 로컬 잠금에 공유 Lease 추가. 복구 실패·비정상 종료에는 잠금 보존 |
| [deploy_b6_lab.py](../scripts/deploy_b6_lab.py) | 전체 실습 설치도 공유 Lease 사용. GitOps 등록 이후 재적용 거부 |
| [b6-gitops-bindings.example.json](../config/b6-gitops-bindings.example.json) | 후속 수집기용 `argocd/lab-target` 읽기 설정 예시. 현재 설정에는 미적용 |
| [test_b6_gitops.py](../tests/test_b6_gitops.py) | 대상·권한 확대, 다른 소유권, 동시 실행, 복구 실패와 인계 준비 검사 |

## GitOps 대상과 배포 소유권

**GitOps**는 Git에 저장한 리소스 정의를 기준으로 배포를 맞추는 방식이다. **Application**은 Argo가 어느 Git 경로를 어느 namespace에 적용할지 정하는 객체이고, **AppProject**는 그 Application이 사용할 저장소·배포 범위·실행 권한을 제한한다.

Application은 기존 Argo가 관리하는 `argocd` namespace에 `lab-target`, Project는 `b6-lab`로 준비한다. 배포 목적지는 기존 `ai-ops-lab`다. App을 실습 namespace에 만들기 위해 Argo 전체의 감시 namespace를 넓히거나 재시작하지 않는다.

Git 경로는 `ai-ops/lab`, 입력은 `deployment.json` 한 파일로 제한하며 하위 폴더를 재귀 탐색하지 않는다. 대상은 `lab-api` Deployment뿐이다. DB·Redis·수집기·Secret·Service·namespace·PVC는 이 Application이 관리하지 않는다.

소스 revision에는 검토한 전체 Git SHA를 사용한다. 자동 동기화·self-heal을 켜지 않고 초기 동기화도 직접 실행하기 전에는 일어나지 않는다. Application에 삭제 finalizer를 넣지 않으며 Deployment에는 `Prune=false,Delete=false`를 둔다.

후속 CI는 실패 실습용 별도 커밋 SHA로 수동 sync하고, 복구할 때 원래 승인 커밋 SHA로 다시 sync하도록 구현할 계획이다. Application이 바라보는 정상 기준 SHA는 유지한다. 따라서 실패 이미지를 main의 정상 배포 정의로 바꿔놓을 필요가 없다. 이 CI 경로 자체는 아직 미구현이다.

기존 수동 실행기는 배포의 `ai-ops/b6-deployment-owner=gitops`, Argo 추적 표식 또는 `argocd/lab-target` 등록을 발견하면 시작을 거부한다. 이미지·Redis 설정 patch도 배포 소유권을 다시 검사한다. 전체 실습 설치기로 GitOps 소유권을 덮어쓰는 것도 거부한다.

## 준비한 권한과 서버의 대상 제한

| 주체 | 허용할 범위 |
|---|---|
| Argo Project `b6-lab:ci-sync` | `b6-lab/lab-target` Application의 get·sync만 허용. Application 설정 update·삭제·override 권한 없음 |
| Kubernetes group `b6-s10-coordinator` | 기존 `b6-scenario-lock` Lease 한 개 get·update, `lab-api` Deployment 한 개 get. Lease 생성·앱 patch·Secret·exec 권한 없음 |
| 기존 `lab-investigator` | 후속 binding 변경에 필요한 `argocd/lab-target` 한 개 get만 추가 예정. 쓰기 권한 없음 |

AppProject는 배포 종류와 namespace를 제한하지만 Deployment 이름까지 제한하지는 않는다. 별도의 `ValidatingAdmissionPolicy`·Binding을 준비해 실제 Argo 컨트롤러 identity가 `ai-ops-lab`에서 `lab-api`만 생성·갱신할 수 있게 하고, 다른 Deployment와 삭제 요청은 거부하도록 했다. 이 정책은 Application이 아니라 운영자가 설치할 구성이다. 운영 namespace나 일반 운영자의 정리 요청에는 적용하지 않는다.

실제 컨트롤러 ServiceAccount가 `argocd-application-controller`임을 읽어 대조했다. Project·권한·정책 정의의 서버 dry-run은 통과했다. **정책과 Role을 실제 적용하지 않았으므로 인가 허용/거부와 CEL 정책의 실제 차단을 검증한 것은 아니다.** 토큰·GitHub secret·IAM/EKS identity mapping도 만들지 않았다. CI가 공유 Lease를 사용할 신뢰 identity 연결은 후속 구현이 필요하며 기존 ECR 이미지 등록 역할을 넓히지 않는다.

## 공유 Lease 잠금

**Lease**는 Kubernetes에 저장하는 작은 조정용 객체다. 로컬 `.scenario-lock` 파일은 한 컴퓨터·한 제어 폴더 안에서만 동시 실행을 막는다. 이제 `ai-ops-lab/b6-scenario-lock`도 함께 사용해 다른 제어 폴더나 후속 CI와 같은 실행 경계를 공유할 수 있게 했다.

실행 ID와 Lease UID를 보관하고 `resourceVersion`을 사용해 원자적으로 취득한다. 이미 다른 실행이 보유하면 중단한다. 잠금이 오래됐다는 이유로 빼앗거나 자동 만료시키지 않는다. 복구 항목이 비어 있지 않고 전부 실제 boolean `true`일 때만 자기 잠금을 해제한다. 해제한 후에도 305초의 지표 창을 Lease에 남겨 다른 제어 폴더에서 바로 재실행하는 것을 막는다.

실행 도중 예외가 발생하거나 복구·해제 확인이 실패하면 공유 잠금과 로컬 active/미복구 상태를 보존한다. 임의의 새 제어 폴더를 만들어 우회하지 않는다. 이 잠금은 수정한 실행기들이 따르는 조정 규약이며 관리자의 직접 kubectl 변경을 막는 접근제어 정책은 아니다.

잠금이 남았다면 먼저 기존 프로세스·CI가 끝났는지 확인하고, 해당 실행의 기준 이미지/설정·자기 데이터·HTTP/Redis·운영 식별자 복구를 확인한다. 저장한 holder/UID와 서버 값이 같은 때에만 운영자가 해제를 진행한다. Lease 삭제나 만료 시간 조작으로 복구 검사를 대신하지 않는다.

## 실제 확인한 결과

- 새 경계 시험 19개를 포함한 관련 B-06 시험 **79개**가 통과했다. 기존 전체 시험을 반복하지 않았다.
- 기존 클러스터에서 앱 Deployment와 bootstrap 8개 정의의 서버 dry-run을 통과했다. projected 앱 spec이 현재 spec과 같았다.
- 운영 앱·AI StatefulSet·PVC 식별자와 실습 Deployment 4개의 UID/generation·Pod template 해시가 검사 전후 같았다. Pod 교체·보고서 파일 조작은 실행하지 않았다.
- 공유 Lease를 실제 생성·취득하고 다른 holder의 취득이 거부되는 것을 확인했다. 장애 주입 없이 정상 해제했으며 실제 시나리오용 305초 제한은 자동 시험으로 확인했다.
- 선택한 기준 커밋 `38b5d85db58507f02b84b158f612e385748b5f10`에는 새 배포 파일이 없어 `source_commit_matches_deployment=false`, `ready_to_adopt=false`로 남겼다.
- 실제 정책/권한 적용·Application 등록·collector binding 변경·GitOps sync·CI 자동 롤백·외부 모델 호출은 하지 않았다. 기존 조사 입력의 CI `missing_history`도 유지한다.

이번에 실제 추가한 클러스터 객체는 해제된 Lease 한 개다. 기존 실습 namespace와 함께 정리되며 별도 AWS 리소스·ECR·Terraform state는 만들지 않았다. 준비한 서버 검증 정책은 아직 클러스터에 설치하지 않았다.

## 실행 방법

기존 비공개 manifest에는 Secret 값이 있으므로 등록된 개인 자료 폴더에서만 읽는다. 계획 출력에는 Secret 값을 포함하지 않는다.

```sh
AI_OPS_FILES_ROOT=/전용/개인/자료폴더 PYTHONPATH=ai-ops/src python \
  ai-ops/scripts/prepare_b6_gitops.py \
  --manifest /전용/개인/자료폴더/manifest.json \
  --revision <검토한_전체_커밋_SHA> \
  --output /전용/개인/자료폴더/gitops-새계획.json
```

`--live-check --state-dir <기존 scenarios 폴더>`를 추가하면 서버 dry-run과 식별자 비교를 수행한다. `--exercise-lock`을 추가한 경우에만 공유 Lease 취득·경쟁·해제를 실측한다. 어느 방식에도 실제 bootstrap 적용·sync 기능은 없다.

## 다음 적용 순서

1. 새 배포 파일과 실행기 변경을 검토해 원격에 반영한다. 실제 소스 커밋에 저장된 `deployment.json`이 계획과 일치하는지 다시 확인한다.
2. 공유 Lease를 보유한 상태에서 정책/Binding·Project·최소 Role을 적용하고, 정책의 typeChecking 및 실제 허용/거부를 확인한다. CI identity도 이 권한에만 연결한다.
3. 수동 Application을 등록하고 Argo가 렌더링한 대상이 동일 Deployment 하나·기존 앱 spec인지 비교한 뒤 첫 정상 sync로 인계한다. 실습 DB·Redis·수집기와 운영 대상은 유지한다.
4. collector의 `argocd/lab-target` 읽기 설정을 반영한다. 먼저 ConfigMap 갱신으로 연결 가능한지 확인하고 Pod를 교체해야 한다면 기존 emptyDir 보고서 보존 절차를 사용한다.
5. S10-03의 main 수동 workflow와 같은 공유 Lease를 사용하는 CI 인증, 실패 커밋·복구 커밋·전후 관측 보존을 구현한다. 실제 실패 이후 복구가 성공해도 최초 CI 실패는 남긴다.

S10-02는 **적용 전 준비와 검증 묶음 완료**다. 실제 인계·S10 실환경 성공·B-06 전체 완료·C 진입은 후속이다. 다음 작업의 권장 추론 깊이는 GPT-6 SOL high다.

## 2026-10-22 원격 반영 전 코드 검토

이 절은 위 10월 21일 서버 실측과 구분한다. 이후 전체 AWS 정리·재설치를 수행했으므로 예전 UID·공유 Lease·dry-run 결과는 현재 클러스터 검증이 아니다. 이번에는 S10 코드·정의의 분리, 검토와 오프라인 검사를 수행하며 실제 실습 배포·권한 적용·Argo 등록·장애 주입은 하지 않는다.

- 최신 main `d7a57295951aa24b5d43a976cdc0e68200798200` 기준으로 S10-01 출처 점검과 S10-02 준비·공유 잠금 변경만 분리했다. 로컬에 남아 있는 S9/S11 확장과 실측 문서, TLS·설치 기록은 이번 PR에 포함하지 않는다. 원격의 수동 실행기는 S1~S4 범위를 유지한다.
- 삭제된 기존 앱 digest 대신 재설치 image-only 실행 `39712114396`의 `sha-38b5d85`·ECR digest `sha256:e5b0ac5d3963b63afe913584204b7d1a33f9c07a9ee8df3956a6a0af154bb142`를 정적 Deployment에 반영했다. 실제 ECR 존재도 읽기 전용 조회로 확인했다. 다음 실습 설치는 같은 앱 정의를 사용하고 실제 Pod spec 일치를 다시 검사해야 한다.
- CI 롤백 기록에 다른 이미지가 섞이거나 생략된 이미지가 있으면 복구 성공으로 해석하지 않도록 보완했다. 빈 복구 기록에는 공유 잠금을 해제하지 않으며, 저장소의 Deployment 파일이 생성기의 실습 앱 정의와 정확히 같은지도 검사한다.
- S10 관련 시험 35개와 전체 Python 327개가 통과했다. 전체 검사는 Python 3.13에서 기존 SQLite ResourceWarning이 stderr JSON 캡처를 방해하는 현상을 분리하기 위해 `-W ignore::ResourceWarning`을 사용했다. 최초 로그 캡처 실패와 단독 7개 통과를 보존하며 SQLite 연결 정리 결함을 이번에 해결했다고 주장하지 않는다. 원격 CI 결과는 PR에서 별도 확인한다.
- 고정 fixture 4종·compileall·기존 Helm/읽기 권한/NetworkPolicy 정적 검사·스키마 재생성 불변 검사를 통과했다. 기존 경계 검사를 통과한 것이 새 S10 AdmissionPolicy의 실제 차단 검증을 의미하지 않는다.

[코드 검토 요약](assets/B6-s10-source-review.json)에 이번 확인과 미실행 범위를 기록한다. 다음은 원격 검토 SHA를 기준으로 독립 실습을 재설치하고 실제 권한·정책 검사와 Argo 소유권 인계를 수행하는 것이다. 그 이후 S10-03 CI 실행·신뢰 identity 연결을 구현한다. 원격 반영만으로 `ready_to_adopt`를 true로 바꾸거나 B-06 완료로 표시하지 않는다.
