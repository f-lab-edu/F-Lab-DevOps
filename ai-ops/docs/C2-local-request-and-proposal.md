# C-02 로컬 사건 조회·이미지 변경안 첫 구현 — 2026-10-28 KST

판정: **인증된 실습 사건 조회를 EKS에서 확인, C-02 전체는 진행 중.** [C-01 설계](C1-approval-recovery-design.md)의 요청과 특정 변경 승인을 분리하는 작업이다. 실습 `ai-ci/ci-worker` PVC의 제한된 TLS sidecar를 배포하고 실제 사건 한 건을 운영자 CLI에서 읽었다. Helm 입력은 준비 상태이며 현재 Argo는 계속 `deployment.json`을 사용한다. 변경안의 실시간 검증과 승인·실행은 연결되지 않았으므로 이 기록을 실제 복구 기능으로 해석하지 않는다.

## 구현한 처리 흐름

1. 운영자 컴퓨터의 Unix socket 서비스가 **커널 peer UID**와 로컬 `gh api user`의 불변 GitHub ID를 허용 목록과 대조한다. 클라이언트가 적은 사용자 이름은 받지 않는다. 서비스는 `show`와 `request`만 지원하고 `approve`·`execute` 명령은 없다.
2. `show`는 `incident_id`·`run_id`를 모두 요구한다. 기존 `IncidentStore`의 저장 계약을 **읽기 전용**으로 다시 검사하고 `urlshortener/ai-ops-lab/lab-api/practice` 대상만 통과시킨다. 반환 값은 대상·묶음/보고서/감사 해시, 상태·증거 종류별 수·인용 ID뿐이다. 질문·로그·보고서 문장·UID·토큰은 반환하지 않는다.
3. `request`는 위 조회를 통과한 사건과 인증 사용자 ID를 묶어 개인 SQLite에 기록한다. 이 단계의 결과는 **해결 요청**이며 변경안 승인이나 GitHub 쓰기가 아니다. 개인 디렉터리는 `0700`, DB는 `0600`; 동일 요청 ID의 다른 내용과 동일 요청의 다른 변경안을 거부한다.
4. 변경안 초안 함수는 요청의 증거 묶음·보고서 해시, 고정 저장소·실습 대상·base SHA·현재 이미지와 `values.yaml`의 이미지가 일치하는지 확인한다. `api.image.digest` 한 필드만 바꾼 정확한 diff·해시·24시간 만료를 생성해 `draft`로만 저장한다. 현재 이미지·과거 정상 digest·ECR 존재성·Argo 상태의 **실시간 검증 입력은 아직 없다.** 따라서 이 함수는 오프라인 초안 계약이며 실제 제안 승인 경로에는 연결하지 않았다.

| 파일 | 역할 |
|---|---|
| [c_recovery.py](../src/ai_ops/c_recovery.py) | 실습 사건의 원문 없는 투영, 요청·초안 계약, 이미지 한 필드 diff와 개인 DB |
| [c_local_auth.py](../src/ai_ops/c_local_auth.py) | macOS `getpeereid`·Linux `SO_PEERCRED`와 GitHub 사용자 ID 대조 |
| [c_operator.py](../src/ai_ops/c_operator.py) | 사용자 전용 Unix socket의 `show`·`request` 인터페이스 |
| [c_projection_server.py](../src/ai_ops/c_projection_server.py)·[c_projection_client.py](../src/ai_ops/c_projection_client.py) | 실습 worker PVC의 제한된 TLS 요약 응답과 운영자 로컬 TLS 검증 클라이언트 |
| [ci_service.py](../src/ai_ops/ci_service.py)·[prepare_ci_service.py](../scripts/prepare_ci_service.py) | 별도 Secret이 지정됐을 때만 실습 worker Pod에 읽기 전용 sidecar를 추가하는 비활성 배포 정의 |
| [Chart.yaml](../lab/Chart.yaml)·[values.yaml](../lab/values.yaml)·[Deployment template](../lab/templates/deployment.yaml) | 기존 단일 실습 Deployment와 동일하게 렌더되는 **비활성** Helm 입력 |
| [검증 시험](../tests/test_c_recovery.py) | 대상/권한 거부, 원문 제외, 정확한 digest diff, 중복 저장, 로컬 소켓 경계 |

`values.yaml`은 JSON 형식으로 쓴 유효한 YAML이며, 추가 키와 다른 repository를 [values.schema.json](../lab/values.schema.json)이 거부한다. 제안 생성 함수도 같은 고정 구조를 별도로 검사한다. Helm chart는 현재의 [deployment.json](../lab/deployment.json)을 대신해 **아직 Argo에 연결되지 않았다.** 기존 GitOps 입력과 중복 관리하지 않는다.

## 실습 worker 사건 조회 경로의 현재 상태

S10 조사 사건은 운영 `ai-ops` StatefulSet이 아니라 **`ai-ci/ci-worker`의 `ci-queue` PVC**에 있다. 선택적 `projection-read` sidecar는 같은 Pod에서 이 PVC를 **읽기 전용**으로 마운트하고, 등록된 `urlshortener/ai-ops-lab/lab-api/practice` 사건의 ID·대상·상태·증거 종류별 수·인용 ID·묶음/보고서/감사 해시만 TLS로 반환한다. 원문 질문·로그·보고서 문장·토큰은 응답에 없다. 인증용 `ci-projection-identity` Secret의 `token`은 CI 전달기 GitHub 토큰이나 운영 경보 토큰과 공유하지 않는다. 이 sidecar에는 Kubernetes 서비스 계정 토큰·GitHub 자격을 마운트하지 않고, Service와 인바운드 NetworkPolicy 허용도 추가하지 않았다.

운영자 로컬 서비스는 같은 OS 사용자와 `gh api user`의 허용 ID를 먼저 확인한 뒤, 별도 로컬 토큰 파일과 CA 인증서로 sidecar TLS 연결을 검증한다. 로컬 포트 전달은 **전송 경로일 뿐 인증 수단이 아니다.** 현재 `ai-ci/ci-projection-identity` Secret은 `token/tls.crt/tls.key`를 담고, `ci-worker.ai-ci.svc` SAN 인증서·토큰의 로컬 원본은 사용자 전용 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/c2-projection-identity-20261028/`에 `0700/0600`으로 보존한다. 인증서 만료는 **2026-11-27 18:22 KST**다. 만료 전에 새 자격을 발급하고 Secret·로컬 클라이언트의 CA를 함께 교체해야 한다. 재설치에서는 Secret 복사본을 다시 만들되 원본 값을 Git·문서·실행 출력에 남기지 않는다. 운영자용 범위 제한 Pod port-forward RBAC는 아직 없다. 이번 실측은 관리자의 임시 kubeconfig로 전송 경로만 열었고, 로컬 CLI에는 `kubectl exec`나 Secret 조회 권한을 부여하지 않았다. `prepare_ci_service.py --projection-read`는 복제수 0의 배포 정의만 생성하므로 활성 worker에 그대로 적용하면 worker가 내려간다. 이번에는 현재 Deployment의 UID·resourceVersion·복제수 1·PVC를 검사하는 조건부 JSON patch로 sidecar와 Secret 볼륨만 추가했다.

## 확인 결과

- `helm lint ai-ops/lab`: 오류 0개. 기본 `helm template`의 단일 Deployment가 기존 `deployment.json`과 구조상 **완전히 동일**했다.
- 대체 digest로 렌더하면 `spec.template.spec.containers[0].image` **한 필드만** 달라졌다.
- `test_c_recovery` 집중 시험 **7개 통과**. 로컬 Unix socket 시험은 기본 샌드박스의 소켓 생성 제한으로 처음 실행에서 막혀, 로컬 소켓 권한으로 같은 시험을 재실행해 통과했다. GitHub 조회는 모의 응답을 사용했고 실제 토큰을 읽거나 클러스터/모델을 호출하지 않았다. 변경안 ID 충돌도 저장 전에 거부한다.
- `test_c_projection`·`test_ci_service`·기존 `test_alert_receiver`와 위 7개를 합쳐 **22개 통과**. 전체 AI 시험 **539개 통과**와 PR #102의 `contracts`·`image` 검사 통과를 확인했다. 합성 S10 이력으로 실제 `ci-worker` 형식의 사건을 개인 큐에 저장하고 TLS 요약 경로로 조회했으며, 보고서 문장은 응답에 없었다.
- 2026-10-28 18:05 KST에 현재 EKS endpoint로 임시 kubeconfig를 만들고 **읽기 전용**으로 재조회했다. `ai-ci/ci-worker`는 1/1이고 컨테이너는 `ci-worker` 하나, 볼륨은 `config/data/tmp/identity`였다. `ci-queue` PVC는 `Bound/ReadWriteOnce`이고 `ci-projection-identity` Secret은 없었다. 기본 kubeconfig를 바꾸지 않았으며 임시 파일을 삭제했다. 따라서 현재 클러스터에는 사건 조회 sidecar·자격이 없고 원격 조회 성공은 아직 입증하지 않았다.
- PR [#102](https://github.com/f-lab-edu/F-Lab-DevOps/pull/102)를 main 병합 커밋 `2e87cf69bf50916722743000a2545020e7201ec7`로 반영했다. [이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40663445452)은 검사 통과 후 `sha256:4faa5c5ca812d20a1306bab9d3f8ab2513b50182dc9cf439dfc5e07e3f196a3e`를 AI ECR에 게시했다.
- 2026-10-28 18:25 KST에 `ci-projection-identity` Secret을 생성하고, `ai-ci/ci-worker`에 **`projection-read` sidecar와 Secret 볼륨만 추가**했다. `ci-worker` 본체 이미지는 기존 digest, 복제수는 1, `ci-queue` PVC는 동일하게 유지했다. rollout 뒤 두 컨테이너가 Ready·재시작 0이었다. 기존 `ci-deny`의 인바운드 차단을 유지하고 Service는 만들지 않았다.
- 현재 PVC의 `inc-087b8cb2438443f3b09f7004fbb049d4`/`run-262cbdc5bd6242ebadb8cccd06f42d6a`를 TLS 클라이언트와 운영자 CLI에서 조회했다. 고정 practice 대상, `partial` 상태, 증거 개수·인용 ID 개수·세 검증 해시만 반환했다. 잘못된 토큰과 없는 실행 ID는 거부됐다. 검증용 로컬 서비스·port-forward는 종료했다. 이 한 건은 **읽기 경로의 실측**이며 원인 해결이나 변경안 성공의 증거가 아니다.
- 이번 작업은 기존 Argo Application, S10·정상 CI workflow, Kubernetes 권한, GitHub 보호 규칙·자격을 변경하지 않았다. 다른 작업의 미커밋 파일도 보존했다.

## C-02 종료까지 남은 연결

1. **조회 경로 권한·재설치 마무리:** 실습 사건의 실제 조회·거부는 확인했다. 관리자의 광범위한 임시 kubeconfig를 대신할 **대상 Pod 한정 port-forward RBAC**와 자격 회전·재설치 절차를 검증해야 한다. 운영자 CLI에 `kubectl exec`나 클러스터 Secret 조회 권한을 추가하지 않는다.
2. **values 전환과 기존 실습 호환:** S10 실행기의 `deployment.json` 비교, Argo directory 입력, 정상 CI의 App 검사와 artifact 연결을 Helm values에 맞춰 함께 변경한다. 렌더 동등성·실패→기존 CI 원복·정상 관측 회귀 후 Argo를 동일 단일 소유자로 전환한다. 이 전에는 실제 변경안/PR을 활성화하지 않는다.
3. **검증된 변경안 입력:** 새 초안의 현재 이미지/UID/base SHA, 이전 정상 digest와 ECR 존재성, CI/Argo 이력, 공유 `LabLease` 상태를 읽기 전용으로 재검사한다. 원본 증거 ID·변경 영향·검증/복구 조건도 제안 계약에 연결한다. 자료가 없거나 기존 CI가 이미 원복했으면 새 제안을 만들지 않는다. 제안·취소·만료 상태를 실제 저장소와 연결한다.
4. **승인·실행은 C-03:** GitHub review의 사용자/commit/diff 검증과 `main` 보호 규칙 적용 뒤에만 승인 상태를 허용한다. 현재 C-02 DB와 API는 `draft` 이상 상태를 수용하지 않는다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
