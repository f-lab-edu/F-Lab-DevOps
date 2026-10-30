# C-02 로컬 사건 조회·이미지 변경안 첫 구현 — 2026-10-29 KST 갱신

판정: **인증된 실습 사건 조회와 현재 Pod 한정 포트 전달을 EKS에서 확인, C-02 전체는 진행 중.** [C-01 설계](C1-approval-recovery-design.md)의 요청과 특정 변경 승인을 분리하는 작업이다. 실습 `ai-ci/ci-worker` PVC의 제한된 TLS sidecar를 배포하고 실제 사건 한 건을 운영자 CLI에서 읽었다. 2026-10-29에는 실습 Argo 입력을 Helm `values.yaml`로 전환했다. 현재 앱은 이미 복구되어 실제 제안·승인·실행은 연결하지 않았다.

## 구현한 처리 흐름

1. 운영자 컴퓨터의 Unix socket 서비스가 **커널 peer UID**와 로컬 `gh api user`의 불변 GitHub ID를 허용 목록과 대조한다. 클라이언트가 적은 사용자 이름은 받지 않는다. 서비스는 `show`와 `request`만 지원하고 `approve`·`execute` 명령은 없다.
2. `show`는 `incident_id`·`run_id`를 모두 요구한다. 기존 `IncidentStore`의 저장 계약을 **읽기 전용**으로 다시 검사하고 `urlshortener/ai-ops-lab/lab-api/practice` 대상만 통과시킨다. 반환 값은 대상·묶음/보고서/감사 해시, 상태·증거 종류별 수·인용 ID와 해당 v3 보고서가 뒷받침하는 이미지 장애 결합값이다. 질문·로그·보고서 문장·원본 UID·이미지·토큰은 반환하지 않는다.
3. `request`는 위 조회를 통과한 사건과 인증 사용자 ID를 묶어 개인 SQLite에 기록한다. 이 단계의 결과는 **해결 요청**이며 변경안 승인이나 GitHub 쓰기가 아니다. 개인 디렉터리는 `0700`, DB는 `0600`; 동일 요청 ID의 다른 내용과 동일 요청의 다른 변경안을 거부한다.
4. 기존 v1 초안 함수는 요청의 증거 묶음·보고서 해시, 고정 저장소·실습 대상·base SHA·현재 이미지와 `values.yaml`의 이미지가 일치하는지 확인한다. `api.image.digest` 한 필드만 바꾼 정확한 diff·해시·24시간 만료를 생성한다. 이 함수는 오프라인 계약으로 남긴다. v2 초안은 아래의 실시간 근거 재조회와 사건·요청 연결을 추가로 통과해야 만들 수 있다. 두 버전 모두 `draft`이며 승인·실행 경로는 없다.

| 파일 | 역할 |
|---|---|
| [c_recovery.py](../src/ai_ops/c_recovery.py) | 실습 사건의 원문 없는 투영, 요청·초안 계약, 이미지 한 필드 diff와 개인 DB |
| [c_local_auth.py](../src/ai_ops/c_local_auth.py) | macOS `getpeereid`·Linux `SO_PEERCRED`와 GitHub 사용자 ID 대조 |
| [c_operator.py](../src/ai_ops/c_operator.py) | 사용자 전용 Unix socket의 `show`·`request` 인터페이스 |
| [c_projection_server.py](../src/ai_ops/c_projection_server.py)·[c_projection_client.py](../src/ai_ops/c_projection_client.py) | 실습 worker PVC의 제한된 TLS 요약 응답과 운영자 로컬 TLS 검증 클라이언트 |
| [ci_service.py](../src/ai_ops/ci_service.py)·[prepare_ci_service.py](../scripts/prepare_ci_service.py) | 별도 Secret이 지정됐을 때만 실습 worker Pod에 읽기 전용 sidecar를 추가하는 비활성 배포 정의 |
| [Chart.yaml](../lab/Chart.yaml)·[values.yaml](../lab/values.yaml)·[Deployment template](../lab/templates/deployment.yaml) | 기존 단일 실습 Deployment와 동일하게 렌더되는 Helm 입력. 2026-10-29부터 실습 Application의 활성 소스 |
| [검증 시험](../tests/test_c_recovery.py) | 대상/권한 거부, 원문 제외, 정확한 digest diff, 중복 저장, 로컬 소켓 경계 |

`values.yaml`은 JSON 형식으로 쓴 유효한 YAML이며, 추가 키와 다른 repository를 [values.schema.json](../lab/values.schema.json)이 거부한다. 제안 생성 함수도 같은 고정 구조를 별도로 검사한다. 2026-10-29 이전의 Argo 입력은 [deployment.json](../lab/deployment.json)이었고, 현재는 Helm chart가 활성 입력이다. `deployment.json`은 비교 기준 파일로 남아 있으며 Argo는 Helm 렌더의 단일 Deployment만 관리한다.

## 실습 worker 사건 조회 경로의 현재 상태

S10 조사 사건은 운영 `ai-ops` StatefulSet이 아니라 **`ai-ci/ci-worker`의 `ci-queue` PVC**에 있다. 선택적 `projection-read` sidecar는 같은 Pod에서 이 PVC를 **읽기 전용**으로 마운트하고, 등록된 `urlshortener/ai-ops-lab/lab-api/practice` 사건의 ID·대상·상태·증거 종류별 수·인용 ID·묶음/보고서/감사 해시만 TLS로 반환한다. 원문 질문·로그·보고서 문장·토큰은 응답에 없다. 인증용 `ci-projection-identity` Secret의 `token`은 CI 전달기 GitHub 토큰이나 운영 경보 토큰과 공유하지 않는다. 이 sidecar에는 Kubernetes 서비스 계정 토큰·GitHub 자격을 마운트하지 않고, Service와 인바운드 NetworkPolicy 허용도 추가하지 않았다.

운영자 로컬 서비스는 같은 OS 사용자와 `gh api user`의 허용 ID를 먼저 확인한 뒤, 별도 로컬 토큰 파일과 CA 인증서로 sidecar TLS 연결을 검증한다. 로컬 포트 전달은 **전송 경로일 뿐 인증 수단이 아니다.** 현재 `ai-ci/ci-projection-identity` Secret은 `token/tls.crt/tls.key`를 담고, `ci-worker.ai-ci.svc` SAN 인증서·토큰의 로컬 원본은 사용자 전용 `/Users/jounghyeon/.local/share/url-shortener-ai-ops/c2-projection-identity-20261028/`에 `0700/0600`으로 보존한다. 인증서 만료는 **2026-11-27 18:22 KST**다. 만료 전에 새 자격을 발급하고 Secret·로컬 클라이언트의 CA를 함께 교체해야 한다. 재설치에서는 Secret 복사본을 다시 만들되 원본 값을 Git·문서·실행 출력에 남기지 않는다. 2026-10-28의 첫 실측은 관리자 kubeconfig로 포트를 열었다. 2026-10-29에는 아래의 Pod 한정 RBAC와 단기 토큰으로 같은 조회를 다시 확인했다. 운영자 CLI에는 `kubectl exec`나 Secret 조회 권한을 부여하지 않았다. `prepare_ci_service.py --projection-read`는 복제수 0의 배포 정의만 생성하므로 활성 worker에 그대로 적용하면 worker가 내려간다. 이번에는 현재 Deployment의 UID·resourceVersion·복제수 1·PVC를 검사하는 조건부 JSON patch로 sidecar와 Secret 볼륨만 추가했다.

## 2026-10-29 Helm 전환 전 Pod 한정 접근과 변경안 선행 조건

당시 `ai-ci/ci-worker-bcd4f56ff-gg22s`의 UID·ReplicaSet→Deployment 소유 관계·두 컨테이너 Ready·9444 포트를 확인해 [권한 생성 코드](../src/ai_ops/c_portforward_access.py)와 [준비 명령](../scripts/prepare_c2_portforward.py)으로 ServiceAccount·Role·RoleBinding 세 개만 만들었다. Role은 **그 Pod 이름의 `pods get`과 `pods/portforward create`만** 허용한다. ServiceAccount의 자동 토큰 마운트는 껐다. [단기 자격 발급 명령](../scripts/issue_c2_portforward_kubeconfig.py)은 적용된 Role의 정확한 규칙과 현재 Pod UID를 다시 검사한 뒤 1시간 TokenRequest를 개인 `0600` kubeconfig로 쓴다. 사용자용 영구 Kubernetes 자격이나 서비스는 만들지 않는다. Pod가 교체되면 기존 Role의 이름·UID 결합은 낡으므로 새 Pod에서 계획을 다시 만들고 이전 단기 자격을 폐기한다.

재설치 시 관리자 kubeconfig가 담긴 개인 폴더를 `AI_OPS_FILES_ROOT`로 지정하고, 현재 Pod 이름을 다시 조회해 다음 순서로 실행한다. 생성한 매니페스트·단기 자격은 Git에 추가하지 않는다.

```bash
INSTALL_DIR="${INSTALL_DIR:?0700 개인 설치 폴더를 먼저 지정하세요}"
POD=$(kubectl --kubeconfig "$INSTALL_DIR/kubeconfig" -n ai-ci get pod -l app=ci-worker -o jsonpath='{.items[0].metadata.name}')
AI_OPS_FILES_ROOT="$INSTALL_DIR" PYTHONPATH=ai-ops/src python ai-ops/scripts/prepare_c2_portforward.py \
  --kubeconfig "$INSTALL_DIR/kubeconfig" --pod "$POD" --output "$INSTALL_DIR/c2-access.json"
kubectl --kubeconfig "$INSTALL_DIR/kubeconfig" apply --dry-run=server -f "$INSTALL_DIR/c2-access.json"
kubectl --kubeconfig "$INSTALL_DIR/kubeconfig" apply -f "$INSTALL_DIR/c2-access.json"
AI_OPS_FILES_ROOT="$INSTALL_DIR" PYTHONPATH=ai-ops/src python ai-ops/scripts/issue_c2_portforward_kubeconfig.py \
  --admin-kubeconfig "$INSTALL_DIR/kubeconfig" --pod "$POD" --output "$INSTALL_DIR/c2-kubeconfig.json"
kubectl --kubeconfig "$INSTALL_DIR/c2-kubeconfig.json" -n ai-ci port-forward "pod/$POD" 9444:9444 --address 127.0.0.1
```

마지막 포트 전달은 조회가 끝나면 종료한다. 단기 kubeconfig는 한 시간 후 재발급하고, 새 Pod가 생기면 Role을 새 Pod에 묶은 뒤 다시 발급한다.

실제 단기 자격으로 현재 Pod `get`·`portforward create`는 `yes`, 다른 Pod 포트 전달·Pod 목록·`pods/exec`·Secret 조회는 `no`였다. 그 자격으로 연 포트에서 새 S10 사건 `inc-62b340f119f342679acc71bfc8dc07a8`/`run-f530bc00ab54470894e3bddf94b58a68`의 제한된 TLS 요약을 읽었고 포트 전달은 종료했다. 토큰 원문은 출력·Git·문서에 저장하지 않았다. [권한 시험](../tests/test_c_portforward_access.py) 2개도 통과했다.

같은 시점의 변경안 선행 조건을 [실시간 사전 판정](../src/ai_ops/c_live_preflight.py)과 [읽기 전용 실행기](../scripts/audit_c2_live_preflight.py)로 확인해 개인 파일에 보관했다. Argo `lab-target`은 여전히 `deployment.json` directory를 입력으로 사용하고 `Synced/Healthy`, 실습 앱은 기존 digest로 1/1 Ready이며 공유 Lease는 비어 있다. 고정 revision의 `values.yaml` digest는 실제 Deployment 이미지와 일치하지 않는다. 판정은 `values_source_inactive`, `values_stale`, `already_recovered`이며 `proposal_allowed=false`, 클러스터·GitHub 쓰기 0건이다. 따라서 이전 S10 사건은 이미 자동 원복된 역사적 사건이고, 현재 values만 바꾸는 제안은 실제 배포를 바꾸지 못한다. **새 제안은 0건**이다. 이 사전 판정은 승인 가능한 변경안 검증기가 아니며, 조건이 모두 맞아도 검증된 이전 정상 digest·현재 실패·ECR·Argo/CI 원본 증거를 요구해 제안을 생성하지 않는다. [관련 거부 시험](../tests/test_c_live_preflight.py) 3개를 추가했다. Helm 전환과 이 근거 검증을 마친 후에만 실시간 변경안 생성 경로를 열 수 있다. 현재의 `draft_proposal`은 계속 오프라인 계약이다.

## 2026-10-29 Helm 입력 전환과 실측

[PR #108](https://github.com/f-lab-edu/F-Lab-DevOps/pull/108)의 `main` 병합 SHA `85549a8ee12b48bfd1d8da9d71e5ebe31b043d7d`에서 `values.yaml` digest를 현재 실습 앱 이미지와 일치시켰다. `helm lint`가 통과했고 Helm 렌더의 단일 Deployment가 기존 `deployment.json`과 구조상 같았다. Argo `argocd/lab-target`의 UID·수동 sync·프로젝트·경로를 고정한 채 `directory` 입력을 제거하고 이 SHA를 대상으로 수동 동기화했다. 결과는 `Synced/Healthy`, `deployment.apps/lab-api unchanged`였고 Deployment UID `cf2b76e1-29be-4b73-a39f-dd8e61b3fa78`, generation 4, 이미지 digest, Ready 1/1이 유지됐다.

S10은 이제 `values.yaml`의 `api.image.digest` 한 필드만 다른 실패 커밋을 허용한다. ECR에서 실패 digest가 없음을 확인한 뒤 [실습 CI #40817078189](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40817078189)를 실행했다. artifact는 새 digest의 이미지 취득 실패 `failure`, 원래 SHA·digest의 `rollback success`, 최종 `recovered_after_failure`를 담았다. 최초 실패를 보존하는 CI 최종 `failure`는 설계된 결과다. 이후 Argo는 같은 SHA에 `Synced/Healthy`, 앱은 같은 UID와 원래 이미지로 Ready 1/1이었다.

새 digest 형식의 CI 기록을 읽는 코드는 [이미지 등록 #40817266248](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40817266248)에서 검사·게시했고, worker·조회 sidecar·전달기를 새 digest `sha256:11aa823b63c78e136b8fee0eef31d7665d7db141544108544b509f2943150719`로 교체했다. `ci-queue` PVC와 읽기 전용 조회 경계는 유지했다. 전달기는 위 S10 기록 1건을 자동 접수했고 worker는 규칙 기반 부분 보고서를 작성했다. 새 worker Pod `ci-worker-b44c99bf9-h9qmm`에 맞춰 C-02 Role의 Pod 이름·UID를 다시 결합하고 1시간 자격을 재발급했다. 새 자격은 이 Pod의 포트 전달은 허용하고 `pods/exec`·Secret 조회는 거부했다.

전환 뒤 [읽기 전용 사전 판정](../scripts/audit_c2_live_preflight.py)의 차단 사유는 `already_recovered` 하나였다. 이전의 `values_source_inactive`·`values_stale`은 해소됐지만, 과거 S10 실패는 이미 자동 원복됐으므로 새 제안은 만들지 않았다. 모델 gateway·proxy는 계속 복제수 0이며 유료 모델 전송을 켜지 않았다.

정상 CI는 S10 뒤 앱 Pod UID가 바뀌고 실행기 코드 해시가 바뀌어 결합값·Pod 이름 제한 RBAC·GitHub 환경 변수 `NORMAL_CI_BINDING`·`ai-ci/ci-normal-binding`을 재생성해야 한다. 수집기 내부의 `executor_hash()`도 새 코드와 같아야 하므로 수집기 이미지를 같은 digest로 올리고 암호화 `lab-collector-source` PVC를 보존했다. relay는 결합값 변경 후 다시 시작한다. 초기 재실행 [#40817692942](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40817692942)는 옛 앱 Pod UID 때문에 시작 전 거부됐고, [#40817926084](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40817926084)는 옛 수집기 코드 해시 때문에 `capture_failed`였으나 자기 데이터 제거·배포 기준선 보존·잠금 해제가 모두 확인됐다. 앱·수집기 Pod를 다시 결합한 [#40818314394](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40818314394)는 성공했다. 결과 artifact의 `record_type=b6_normal_ci_observation`, `owned_data_removed=true`, `baseline_unchanged=true`, `lock_released=true`, `external_model_calls=0`을 확인했고, relay 로그에서 이 정상 기록 1건의 자동 접수를 확인했다. 앱·수집기·worker·relay는 각각 Ready 1/1이며 모델 gateway·proxy는 복제수 0이다.

## 2026-10-29 복구 근거 읽기 검증

[복구 근거 검증기](../src/ai_ops/c_recovery_proof.py)는 기존 [실시간 사전 판정](../src/ai_ops/c_live_preflight.py)이 Helm 소스·현재 이미지·Argo·Lease를 통과했을 때만 동작한다. [감사 명령](../scripts/audit_c2_live_preflight.py)에 정상 CI 결합값 파일, 실행 ID와 head SHA를 함께 주면 GitHub 원본 실행·artifact를 `verify_normal_ci_history`로 검증한다. 동일 EKS의 지문과 현재 kubeconfig, ECR 계정을 대조하고 이전 정상 digest의 ECR 존재 및 현재 실패 digest의 부재를 확인한다. 현재 Deployment의 단일 Pod와 ReplicaSet 소유 UID, 관측 generation, 이미지 취득 실패 이유, Argo의 현재 revision·동기화 시각이 정상 CI 완료 이후인지 검사한다. 확인되지 않은 값은 `blockers`에 남긴다.

이 검증만 통과하면 결과는 `proof_ready=true`, `proposal_allowed=false`, `incident_correlation_and_request_required`다. 정상 CI #40818314394의 GitHub 원본 실행·artifact와 당시 결합값은 별도 읽기 검증을 통과했다. 현재 실습 앱은 정상 이미지로 Ready 1/1이라 실시간 감사 결과는 `already_recovered`, `proof_ready=false`, `proposal_allowed=false`였다. 정상 CI 기록만으로 과거 실패를 현재 장애로 취급하지 않는다. 변경안·PR·배포는 0건이다.

## 2026-10-29 사건·요청과 현재 실패의 연결 구현

[사건 투영](../src/ai_ops/c_recovery.py)은 저장 계약을 재검증한 `rules-captured-v3` 보고서에서 같은 원인 후보가 인용한 **Pending Pod 상태·이미지 취득 실패 Event·Deployment 이미지** 세 자료를 확인한다. 수집 감사의 available/collected 상태, Pod·Deployment UID, 이미지, Event 대상 UID, 120초 이내 시각 관계가 맞을 때에만 `image_failure` 결합값을 만든다. 외부 응답에는 원본 UID·이미지 대신 SHA-256 해시와 세 근거 ID·UTC 관측 시각만 담는다. 자료가 누락되거나 후보가 둘 이상이면 결합값을 만들지 않는다. `request`는 이 결합값의 해시를 개인 DB에 고정한다.

[읽기 전용 감사 명령](../scripts/audit_c2_live_preflight.py)에 `--operator-state-root`, `--incident-id`, `--incident-run-id`, `--request-id`를 정상 CI 근거 인자와 함께 주면, 현재 이미지 실패 근거를 새로 조회한 뒤 인증된 운영자 socket의 `show`와 개인 DB의 기존 요청을 읽는다. `gh api user`의 GitHub ID가 요청자와 같아야 한다. [연결 검증기](../src/ai_ops/c_recovery_proof.py)는 현재 Pod·Deployment·이미지 해시, 사건/실행 ID, 묶음·보고서·장애 결합 해시, 관측 시각과 60초 이내의 실시간 재조회 시각을 대조한다. 맞으면 `incident_ready=true`이지만 `proposal_allowed=false`와 `fresh_recheck_and_proposal_contract_required`를 유지한다. 이 값은 사건과 요청을 같은 장애에 연결했다는 뜻이며, 승인 가능한 변경안을 뜻하지 않는다. 현재 앱이 복구된 상태에서는 이 연결 단계에 들어가지 않는다. 현재 클러스터에서 양성 경로는 검증하지 않았고, 합성 사건 시험만 통과했다.

## 2026-10-29 v2 초안 직전 재조회와 조건

감사 명령에 `--proposal-recheck`를 추가하면 위의 Kubernetes·Argo·ECR·GitHub·운영자 사건 조회를 **같은 실행에서 다시 수행**한다. 첫 조회가 사건 연결을 통과하지 못하면 두 번째 조회나 초안 저장을 하지 않는다. 두 번째 조회의 Pod UID·Deployment UID·현재/이전 digest·Argo revision·정상 CI artifact·사건/요청 해시가 첫 조회와 같고, 순서대로 60초 이내에 읽혔으며 두 번째 근거가 30초 이내일 때만 `proposal_ready=true`가 된다. 도중 자동 원복되면 `already_recovered`를 유지하고 초안을 만들지 않는다.

`--proposal-output`까지 지정했을 때에만 [v2 초안](../src/ai_ops/c_recovery.py)을 개인 DB와 새 JSON 파일에 기록한다. v2는 세 원본 증거 ID, 장애 결합 해시, 두 번째 재조회 결합 해시, 영향 범위 `ai-ops-lab/lab-api`의 이미지 한 필드, Argo 동기화·적용 digest·Pod Ready·`/readyz`·`/items` 확인 조건을 포함한다. 확인 실패 시 자동으로 실패 이미지를 다시 적용하지 않고 중단·보고한다. 초안의 24시간 만료는 검토 기한일 뿐 실시간 장애 근거의 유효기간을 늘리지 않는다. 결과는 계속 `draft`, `proposal_allowed=false`이며 GitHub PR·승인·배포는 만들지 않는다. C-03 실행 전에는 원본 서비스와 승인 상태를 다시 조회해야 한다. 현재 클러스터는 정상이라 이 v2 양성 경로는 합성 시험으로만 확인했다.

## 2026-10-29 초안 취소·만료·이력

[개인 변경안 저장소](../src/ai_ops/c_recovery.py)는 초안 본문을 그대로 두고 SQLite의 별도 상태·이력 표에 `draft → cancelled` 또는 `draft → expired`만 기록한다. 생성·전이마다 버전과 UTC 시각, 요청자 ID(자동 만료는 없음)를 남긴다. 상태 전이는 하나의 트랜잭션에서 이전 버전을 검사하고, 이력 순서·현재 상태가 맞지 않으면 조회를 거부한다. 기존 DB에 상태 이력이 없는 초안은 첫 조회에서 생성 시각을 기준으로 초기 `draft` 이력을 채운다.

[운영자 로컬 소켓](../src/ai_ops/c_operator.py)의 `proposal-status --proposal-id <ID>`는 커널 peer UID·현재 GitHub ID와 원래 요청자를 대조해 상태와 이력만 반환한다. `cancel-proposal --proposal-id <ID>`는 같은 요청자의 미만료 초안만 취소하며 반복 호출로 이력을 늘리지 않는다. 만료는 별도 상시 작업이 아니라 상태 조회·취소·재사용 시 현재 UTC 시각을 확인해 원자 기록한다. 취소·만료된 초안은 일반 변경안 읽기와 재저장을 거부한다. 앞서 출력한 JSON 파일은 **생성 시점의 불변 사본**이므로 현재 사용 가능 여부는 반드시 소켓의 상태 조회로 확인한다. 이 소켓은 여전히 GitHub PR·승인·배포 명령을 받지 않는다.

재현 시 `--normal-binding`에는 성공한 정상 CI 실행 당시의 개인 결합값 파일을, `--normal-run-id`와 `--normal-head`에는 그 실행의 GitHub ID와 전체 SHA를 넣는다. `--kubeconfig`는 현재 클러스터, `--output`은 개인 폴더의 새 JSON 파일이어야 한다. 예: `AI_OPS_FILES_ROOT="$INSTALL_DIR" PYTHONPATH=ai-ops/src python ai-ops/scripts/audit_c2_live_preflight.py --kubeconfig "$INSTALL_DIR/kubeconfig" --normal-binding "$INSTALL_DIR/normal-binding.json" --normal-run-id 40818314394 --normal-head 85549a8ee12b48bfd1d8da9d71e5ebe31b043d7d --output "$INSTALL_DIR/c2-proof-new.json"`. 앱이 이미 복구됐으면 GitHub·ECR 조회 전에 중단한다. 관련 거부 시험 4개와 기존 사전 판정 시험 3개가 통과했다.

## 2026-10-29 새 이미지 배포와 Pod 한정 자격 갱신

[AI 이미지 등록 실행 #40822529008](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40822529008)의 시험·빌드·ECR 게시가 통과했고 `main`의 C-02 코드를 담은 digest `sha256:850dc9d53ebf5a41f047615a7c6c20be5566b895b22204e571fd7f7da02ef5ef`를 ECR 원본에서 확인했다. 현재 `ai-ci/ci-worker` Deployment의 UID·resourceVersion·복제수 1·두 컨테이너의 이전 이미지·`ci-queue` PVC를 검사한 조건부 패치를 server dry-run한 다음, worker와 `projection-read` 이미지만 이 digest로 교체했다. 새 Pod `ci-worker-69dc5ff957-6ll7f`의 두 컨테이너는 Ready, 재시작은 0회였다. 실습 앱 `ai-ops-lab/lab-api`는 기존 digest와 Ready 1/1을 유지했다.

새 Pod의 소유 관계를 확인해 C-02 ServiceAccount·Role·RoleBinding의 대상 이름과 UID를 다시 결합했다. 1시간짜리 새 kubeconfig는 개인 `0600` 파일에만 발급했다. 이 자격으로 **현재 Pod 조회·포트 전달은 허용**, **이전 Pod 포트 전달·Pod 목록·exec·Secret 조회는 거부**됨을 확인했다. localhost 포트 전달 위에서 기존 S10 사건 `inc-62b340f119f342679acc71bfc8dc07a8`/`run-f530bc00ab54470894e3bddf94b58a68`의 제한된 TLS 요약을 다시 읽고 포트 전달을 종료했다. 이 과거 사건의 보고서는 `partial`이고 이미지 장애 결합값이 없다. 새 이미지의 조회 경로와 Pod 교체 뒤 권한 갱신을 확인한 결과이며, 실제 장애 제안 성공의 근거는 아니다.

Helm 소스는 revision `85549a8ee12b48bfd1d8da9d71e5ebe31b043d7d`에서 `Synced/Healthy`, 실습 앱 이미지는 `values.yaml`과 일치, Lease는 비어 있다. 읽기 전용 사전 판정 결과는 `already_recovered`, `proposal_allowed=false`였다. 새 제안·PR·실습 앱 변경은 만들지 않았다.

## 2026-10-28 첫 구현 당시 확인 결과

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

### 라이브 양성 경로 실습 준비

기존 S10 CI는 이미지 취득 실패를 확인하면 곧바로 자동 원복하므로 C-02가 같은 현재 장애의 사건·요청·변경안 초안을 조회할 시간이 부족하다. [격리 실습 CI](../../.github/workflows/ai-ops-lab-ci.yml)에 기본값 `false`인 `c2_observation_window` 수동 입력을 추가했다. 별도 승인된 실행에서만 입력을 켜고, 고정 실패 소스·ECR 부재·정상 기준선·공유 Lease 검사와 실제 실패 관측을 모두 통과한 경우에만 [실습 실행기](../src/ai_ops/b6_ci.py)가 **최대 5분** 실패 상태를 관측한다. 실패가 사라지거나 대상이 바뀌면 관측을 일찍 끝낸다. 뒤의 `always()` 원복·검증 단계와 최초 실패를 보존하는 CI 결과는 유지한다.

**관측 창만으로는 C-02 양성 경로가 연결되지 않는다.** 현재 [CI 전달기](../src/ai_ops/ci_relay.py)는 실행이 끝나 `conclusion=failure`가 된 뒤 마지막 단계에서 올라온 실패·원복 artifact를 검증한다. [CI 큐 입력](../src/ai_ops/job_queue.py)의 보고서 엔진은 `rules-captured-ci-v1`이며, [C-02 이미지 장애 결합](../src/ai_ops/c_recovery.py)은 Pod 상태·Event·Deployment를 수집한 `rules-captured-v3` 사건만 허용한다. 따라서 S10 관측 창 동안에는 해당 CI artifact가 없고, 완료 뒤 접수되는 CI 보고서에는 C-02 결합값이 생기지 않는다. 이 차이를 확인하기 전의 실습 승인만으로 현재 workflow를 실행해도 라이브 양성 검증은 성립하지 않는다.

관측 창에서 쓸 **일회성 수동 조사 접수 경로**를 [Job 정의](../src/ai_ops/c2_live_job.py)와 [준비 명령](../scripts/prepare_c2_live_job.py)으로 추가했다. 준비 명령은 고정 정상·실패 커밋의 `values.yaml` 이미지 한 필드 차이, 현재 Argo의 실패 동기화 revision, Deployment·ReplicaSet·Pod의 이미지 취득 실패, S10 실행 ID·시도 번호에 결합된 Lease를 읽기 전용으로 확인한 뒤에만 현재 `ci-worker` Deployment의 이미지·설정·`ci-queue` PVC로 같은 노드의 일회성 Job을 생성한다. Job은 서비스 계정 토큰·조회 sidecar의 Secret을 마운트하지 않고, 고정된 실습 대상·질문·S10 실행별 요청 키 한 건을 기존 `queue-submit`으로 접수한다. 기존 worker가 자기 읽기 전용 권한으로 Pod·Event·Deployment를 수집해 `rules-captured-v3` 보고서를 작성한다. CI 원복 artifact와 수동 조사 요청은 별도 입력으로 유지한다. Job에는 180초 실행 기한·재시도 0회·완료 후 300초 삭제를 둔다. Job 구조는 Kubernetes server dry-run이 통과했고, 정상 앱에서는 생성 명령이 `현재 격리 실습 이미지 실패를 확인하지 못했습니다`로 거부된다. [경계 시험](../tests/test_c2_live_job.py) 4개와 기존 큐 시험 16개가 통과했다.

실제 실습에서는 관측 창이 열려 있을 때 해당 S10 실행 ID·시도 번호로 새 Job을 준비·적용해야 한다. 접수 Job의 완료만으로 사건 생성이나 변경안 성공을 선언하지 않는다. 자동 원복이 먼저 끝나거나 자료가 부족하면 제안을 만들지 않는다. 조회 sidecar의 읽기 전용 권한은 그대로 둔다. **S10만으로 v2 변경안 양성 경로까지 검증할 수는 없다.** S10은 실패 커밋을 일회성 동기화하지만 Application의 고정 `targetRevision`은 정상 기준선으로 남긴다. C-02 초안은 활성 GitOps 소스의 `values.yaml`과 실패 이미지가 일치해야 하므로 현재 S10 관측 창에서는 `values_stale`로 차단된다. 이 실시간 접수 경로로 v3 사건을 검증한 다음, 별도 승인·자동 원복 조건을 갖춘 GitOps 소스 전환 실습을 설계해야 한다.

첫 실습은 격리 대상 `ai-ops-lab/lab-api`, 정상 기준선 revision·digest, 실패 커밋 SHA, 최대 관측 시간과 자동 원복 범위를 제시한 뒤 [C-01 설계](C1-approval-recovery-design.md)에 따라 별도 승인받아 실행했다. 모델 호출은 꺼 두었다. 이후 실패 주입도 실행마다 별도 승인받는다. 이 S10 실습의 성공 기준은 관측 창 안에서 새 v3 사건의 이미지 장애 결합값을 TLS로 읽고, 이후 실습 앱이 원래 digest로 `Synced/Healthy`·Ready 1/1로 복귀하는 것이다. v2 초안 양성 경로는 GitOps 소스 전환 실습의 별도 완료 조건으로 남긴다. 어느 단계에서든 자료가 부족하면 제안을 만들지 않고 복구 상태를 확인한다.

### 2026-10-29 승인된 실시간 관측 결과

[S10 실행 #40826245462](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40826245462)은 정상 revision `85549a8ee12b48bfd1d8da9d71e5ebe31b043d7d`에서 `values.yaml`의 이미지 digest 한 필드만 다른 실패 revision `14bb613a807483e7a2d92082449ab2261d6d5d80`을 동기화했다. 실패 digest는 ECR에 없음을 시작 전에 확인했다. CI artifact는 실패 이미지·Ready 0·`Progressing`을 기록했다. 관측 창 안에서 준비 명령이 현재 실패 Pod·Argo 동기화·실행별 Lease를 확인했고, `ai-ci/c2-live-capture-40826245462-1` Job을 server dry-run 후 적용했다. Job은 성공해 큐 작업 `job-8222f1f596844b468d5296849cd5d727` 한 건을 접수했다.

worker는 사건 `inc-38255794fd52414ea4e5ce428990f606`/실행 `run-e114984db2434665a5ec02a0fc0f2ffd`를 만들었다. 제한된 C-02 Pod 포트 전달과 TLS 조회에서 `suspected_cause` 및 Pod 상태·이미지 실패 Event·Deployment의 근거 ID 세 개를 묶은 `image_failure`가 반환됐다. 보고서는 일부 로그·지표가 없어 `partial`이며, 이 상태를 완료된 조사나 확정 원인으로 표시하지 않는다. 관리자 진단 조회에서는 근거 종류별 상태만 추가 확인했고 원문은 문서에 복사하지 않았다.

같은 관측 창의 읽기 전용 C-02 사전 판정은 `values_stale`, `argo_not_stable`, `lab_locked`를 반환해 `proposal_allowed=false`였다. S10이 실패 revision을 일회성으로 동기화하는 동안 활성 GitOps `targetRevision`은 정상 기준선이므로, **이 실습에서 v2 변경안은 생성하지 않았다.** CI는 고정 정상 revision으로 자동 원복했고 artifact의 결과는 `recovered_after_failure`다. 재조회 시 Argo는 기준선 `Synced/Healthy`, Deployment는 원래 digest로 Ready 1/1, 공유 Lease는 빈 상태였다. CI 최종 `failure`는 최초 장애를 보존하는 설계 결과다. 모델 전송·운영 앱 변경은 없었다.

자동 원복으로 실습 앱 Pod가 교체되자 정상 관측 CI의 Pod 한정 Role·결합값을 현재 Pod로 갱신하고 이전 Pod 권한 제거를 확인했다. worker 재시작으로 바뀐 Pod에도 C-02 조회 Role의 대상 이름·UID를 다시 결합하고 1시간 조회 자격을 재발급했다. 새 자격은 현재 worker Pod 조회·포트 전달만 허용하고, 이전 Pod 포트 전달·Secret 조회·exec는 거부했다. TLS 조회에서 위 사건의 `image_failure` 근거 결합을 다시 확인했다.

[정상 관측 CI #40828027673](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40828027673)은 같은 실습 앱의 자기 아이템 네 번 읽기에서 `miss→hit→miss→hit`와 HTTP 200을 기록하고 성공했다. 영수증은 기준선 유지·실습 소유 자료 삭제·공유 잠금 해제·외부 모델 호출 0건을 기록했다. 전달기는 영수증 한 건을 자동 접수했고 worker의 정제 요약 상태는 `ready`였다. 최종 재조회에서 Argo는 기준선 `Synced/Healthy`, 앱은 원래 digest로 Ready 1/1, `ci-worker`와 `ci-relay`는 각각 Ready 1/1, 공유 Lease는 빈 상태였다.

### 2026-10-30 재설치 후 C-02 조회 재검증

새 클러스터에 C-02 TLS 조회 sidecar와 현재 worker Pod만 허용하는 포트 전달 권한을 복원했다. 승인된 [S10 실행 #41001016263](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/41001016263)은 정상 revision `0fd3e2e4b2cd00d8684b94e83c9b7b0abdf75a09`에서 `values.yaml`의 digest 한 값만 다른 실패 커밋 `a71eba53368f5ecb8ad1fe730f04537f8e50925d`를 적용했다. 실패 digest가 ECR에 없는 것을 먼저 확인했다. 최대 5분 관측 창에서 일회성 `c2-live-capture-41001016263-1` Job이 완료되고 큐 작업 `job-85eb13f017b84108985bb1e58012c8ad`가 접수됐다.

worker는 사건 `inc-84f996db483f425ab71bb92a7bc166f4`와 실행 `run-9c55e9ae65fb43b1bedcba60594372b0`를 `partial`로 저장했다. 현재 worker Pod 한 개로 제한된 포트 전달과 TLS 인증 조회는 `suspected_cause`와 Pod 상태·Event·Deployment 근거 ID 세 개의 `image_failure` 결합을 반환했다. 조회 응답에는 원문 로그나 보고서 문장이 없었다. 이 결과는 장애 조사 근거를 확인한 것이며 원인 확정이나 변경안 승인을 뜻하지 않는다.

S10 artifact는 `deployment=failure`, `rollback=success`, `result=recovered_after_failure`였다. 종료 뒤 Argo는 정상 revision에서 `Synced/Healthy`, Deployment는 원래 digest·Ready 1/1, 공유 Lease는 비어 있었다. CI 최종 `failure`는 최초 이미지 실패를 보존한 결과다. 모델 호출이나 운영 앱 변경은 없었다. 자동 원복으로 실습 앱 Pod 이름이 바뀌어 정상 관측 CI의 Pod 한정 권한·결합값은 새 Pod에 맞춰 회전해야 한다.

이후 앱 Pod `lab-api-6c9cbdc496-mzph7`에 맞춰 정상 관측 CI Role의 `pods get`·`pods/exec create` 대상 이름을 교체하고 이전 Pod 이름을 제거했다. GitHub 환경의 `NORMAL_CI_BINDING`과 `ai-ci/ci-normal-binding` ConfigMap은 같은 새 결합값으로 갱신했으며 relay·worker를 순서대로 재시작해 Ready를 확인했다. worker 재시작 뒤 C-02 조회 Role도 현재 Pod `ci-worker-75754b4758-wfpvs` 한 개로 회전하고 1시간 자격을 새로 발급했다. 새 자격은 현재 Pod 조회를 허용하고 이전 Pod·Secret 조회를 거부했다. 새 Pod의 TLS sidecar에서 위 사건의 `partial`·`suspected_cause`와 이미지 실패 근거 ID 세 개를 재조회한 뒤 포트 전달을 종료했다. 정상 관측 CI의 새 실행은 이 회전 과정에서 반복하지 않았다.

1. **조회 경로의 장기 자격 관리:** Pod 교체 뒤 RBAC 재결합·단기 토큰 재발급·이전 Pod 거부와 실제 TLS 조회는 검증했다. 인증서 만료일인 2026-11-27 18:22 KST 전에 Secret·로컬 CA를 함께 회전하고 구 자격 거부를 확인해야 한다. 운영자 CLI에 `kubectl exec`나 클러스터 Secret 조회 권한을 추가하지 않는다.
2. **검증된 변경안 입력:** 이전 정상 CI·ECR·현재 이미지 취득 실패와 저장된 사건·인증 요청의 대조, 초안 직전 두 번째 조회, 근거·영향·검증 조건을 담은 v2 초안 및 취소·만료 이력은 구현했다. 실제 S10 실패에서 v3 사건의 이미지 장애 결합은 확인했지만, 활성 GitOps 소스 revision과 공유 잠금 조건 때문에 v2 초안 양성 경로는 여전히 합성 시험만 통과했다. 자료가 없거나 기존 CI가 이미 원복했으면 새 제안을 만들지 않는다. 다음 실습은 [활성 GitOps 소스 전환 실습 준비](C2-live-source-trial-plan.md)에 따라 실패 revision을 활성 소스로 전환하고 반드시 정상 기준선으로 원복한다.
3. **승인·실행은 C-03:** GitHub review의 사용자/commit/diff 검증과 `main` 보호 규칙 적용 뒤에만 승인 상태를 허용한다. 현재 C-02 DB와 API는 `draft` 이상 상태를 수용하지 않는다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
