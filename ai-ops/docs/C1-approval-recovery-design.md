# C-01 승인 기반 이미지 복구 설계 — 2026-10-28 KST

상태: **C-01 상세 설계 작성. [C-02 로컬 첫 구현](C2-local-request-and-proposal.md)은 진행 중이며, C-03~C-05·권한 발급·실습 변경은 미착수.** [B단계 종료 범위](B-stage-readiness-audit-20261028.md)의 읽기 조사·격리 실습 보고서를 입력으로 사용한다. 보고서의 `needs_review`는 승인이나 복구 완료가 아니다. 사용자의 “고쳐줘” 요청은 **변경안 작성 요청**이며 실행 승인이 아니다.

이 문서는 기존 `AI 작업 리스트`의 C-01 네 항목과 `EKS_AI_운영_에이전트_프로젝트_적용_계획안` 14.3절의 요구를 현재 코드에 맞춰 구체화한 설계안이다. 위 원본 문서는 다른 checkout에서 관리 중이므로 여기서 상태 체크박스를 직접 수정하지 않는다.

## 1. 목표와 허용 범위

첫 해결 유형은 **`ai-ops-lab/lab-api`의 잘못된 컨테이너 이미지를 검증된 이전 정상 digest로 되돌리는 GitOps 변경 한 종류**다. 고정 저장소 `f-lab-edu/F-Lab-DevOps`, Argo Application `argocd/lab-target`, 컨테이너 `api`와 등록된 `urlshortener/practice` 대상만 허용한다. AI는 근거·원인 후보·변경 이유를 설명하고 변경안을 제시한다. 변경 실행은 사람의 **정확한 변경안 승인**과 별도 실행 주체의 정책 재검사 후에만 시작한다.

초기 허용 변경은 실습 앱 values의 **이미지 digest 필드 하나**다. 저장소의 다른 파일, 운영 `production` 앱, Secret·RBAC·NetworkPolicy·DB·Redis·Terraform, 임의 shell, Pod 삭제와 직접 Deployment patch는 허용하지 않는다. 해결 범위 밖 요청은 실행 없이 이유와 수동 절차를 설명한다. 모델 보고서에 이미지 실패 후보가 있어도 원본 CI·Argo·ECR·현재 대상 상태를 독립 검사해야 한다.

## 2. 현재 구현과 선행 전환

| 현재 사실 | C 설계에 미치는 영향 |
|---|---|
| [실습 Deployment](../lab/deployment.json)의 `spec.template.spec.containers[0].image`가 현재 GitOps 입력이다. [Argo 구성](../src/ai_ops/b6_gitops.py)은 `ai-ops/lab`의 `deployment.json` 한 개를 directory로 읽는다. | **현재는 values-only PR을 만들 수 없다.** 이미지 필드만 바꾸는 JSON diff를 values-only라고 부르지 않는다. |
| [S10 실행기](../src/ai_ops/b6_ci.py)는 두 revision의 `deployment.json`을 비교하고 Argo directory 설정·기준 revision을 검사한다. [정상 CI 실행기](../src/ai_ops/normal_ci_runner.py)도 같은 실습 App과 공유 잠금을 사용한다. | 입력 형식을 바꾸면 S10·정상 CI 검증과 artifact 계약을 함께 바꾸어야 한다. 이전 원본 이력은 당시 형식의 역사적 자료로 보존한다. |
| [앱 CI](../../.github/workflows/ci-cd.yml)의 자동 push 경로는 앱 코드·Dockerfile·의존성에 한정된다. | values-only merge만으로 이미지 빌드·배포 검증이 자동 실행된다고 가정하지 않는다. 별도 승인 실행/검증 workflow가 필요하다. |
| 2026-10-28 GitHub API 읽기 결과: `main` branch protection은 **404 Branch not protected**, 적용 중인 branch rules와 ruleset 목록은 **빈 배열**이었다. | 현재 PR review·필수 CI·병합 제한을 강제하는 보호 장치가 없다. **C-03의 PR 생성/merge와 실행 자격 활성화 전** 보호 규칙을 적용하고 다시 읽어 확인해야 한다. |

전환 순서는 다음과 같다. `ai-ops/lab`에 **실습 전용 Helm chart와 `values.yaml`**을 만들고, 하나의 Deployment를 현재 `deployment.json`과 같은 spec으로 렌더링한다. `values.yaml`은 repository·digest를 분리하되 C에서 변경 가능한 키는 `api.image.digest` 하나로 고정한다. Argo의 동일 `lab-target`을 Helm 소스로 **한 번만** 전환한다. 기존 `deployment.json`은 실행 입력에서 제외해 두 정의가 같은 Deployment를 동시에 관리하지 않게 한다. 전환 전후 렌더의 허용 차이를 이미지 필드로 제한하고 Argo UID·대상 namespace·ServiceAccount·selector·Secret 참조·readiness를 검사한다. [S10](../src/ai_ops/b6_ci.py)과 [정상 CI](../src/ai_ops/normal_ci_runner.py), GitOps 입력 검증을 같은 변경 묶음에서 고쳐 정상·실패·자동 원복 회귀 시험을 통과한 뒤에만 C 복구안을 활성화한다. 전환 실패 시 기존 Argo 소스를 유지하며 C 실행은 잠근다.

## 3. 사용자 인증과 사건 접근

첫 인터페이스는 **운영자 컴퓨터의 단일 사용자 대화형 CLI + 로컬 승인 서비스**로 고정한다. 서비스는 사용자 전용 `0700` 디렉터리의 Unix socket만 열고 요청마다 OS가 제공하는 peer UID를 검사한다. CLI가 보낸 사용자 이름 문자열이나 Kubernetes port-forward 권한을 인증으로 인정하지 않는다. 서비스는 로컬 GitHub 자격으로 GitHub `GET /user`를 호출해 로그인 계정의 불변 사용자 ID를 확인하고, 고정된 실습 운영자 허용 목록과 대조한다. 사용자 토큰은 클러스터에 복사하거나 제안·감사 DB에 저장하지 않는다. 같은 OS 계정의 다른 로컬 프로세스까지 분리하는 방식은 아니므로 이 단계는 **단일 사용자 실습**에만 사용한다. 다중 사용자 웹/Slack 승인은 별도 인증 gateway 설계 전까지 열지 않는다.

사건 조회는 `incident_id`와 `run_id`를 **함께** 지정해야 하며 서비스가 읽기 전용의 제한된 보고서 조회 경로로 저장 보고서·bundle/감사 해시·대상 binding을 검증한 후 반환한다. 현재 B의 Alertmanager webhook에는 GET이 없고 사용자별 사건 읽기 API도 없다. 따라서 **C-02에서 보고서 원문 대신 허용 필드와 검증 해시만 반환하는 인증된 조회 경로를 마련할 때까지** 제안 생성은 중지한다. 로컬 CLI에 `kubectl exec`나 클러스터 Secret 조회 권한을 편의상 부여하지 않는다. 로그인한 사용자 ID, 고정 `practice` 대상과 사건의 등록 대상이 일치할 때만 읽는다. 다른 사건/환경은 기본 거부한다.

## 4. 요청·변경안·승인 계약

| 객체 | 필수 연결 필드 | 의미 |
|---|---|---|
| 해결 요청 | `request_id`, 요청자 GitHub ID, `incident_id`, `run_id`, 보고서/bundle hash, 대상 binding, UTC 요청 시각 | 설명 조회와 구분한다. 요청만으로 쓰기는 0건이다. |
| 변경안 | `proposal_id`, `request_id`, 대상 UID, repo·허용 경로, base SHA, 현재/목표 digest, **정확한 diff와 canonical diff hash**, 원본 증거 ID, 영향, 검증/복구 조건, UTC 만료 | 후보 원인을 확정한 사실로 바꾸지 않는다. 다수 사건/안은 ID를 명시해 선택한다. |
| 승인 | `approval_id`, `proposal_id`, 승인자 **GitHub user ID**, PR 번호, PR head SHA, base SHA, diff hash, 허용 작업, UTC 승인/만료 시각 | 요청 이후 제안 PR을 먼저 만들고, 사람이 그 정확한 변경을 검토해 GitHub `APPROVED` review를 남긴다. 서비스는 API에서 review의 `commit_id`, 승인자, 최신 head와 diff를 다시 확인한다. PR 본문 댓글·자연어 “진행해”·환경 배포 승인은 이 승인을 대신하지 않는다. |
| 실행·검증 | `execution_id`, `verification_id`, 승인 ID, PR·merge SHA, Argo revision, 적용 digest, 각 검증 증거와 UTC 시각 | PR 생성·merge·Argo Synced·사용자 경로 회복을 각각 별도 상태로 기록한다. |

승인자는 PR 작성자인 자동 실행 identity와 달라야 한다. 승인 유효시간은 **30분 이하**, 승인 전 제안은 **24시간 이하**로 시작한다. 만료·취소·거부·PR head 또는 base SHA 변경·diff 변경·대상 UID/현재 이미지 변경·다른 복구 작업의 잠금 획득은 이전 승인을 무효화한다. 정확한 시간은 서버의 UTC 시각으로 판정하고 화면만 KST로 보인다. 요청 ID와 실행 ID는 멱등 키이며 한 대상에 활성 실행은 하나다. 승인 후에도 실행 직전 GitHub 원본과 현재 클러스터 상태를 재조회한다. 이미 기존 CI가 정상 이미지로 원복했다면 `already_recovered`로 끝내고 새 PR·배포를 시작하지 않는다.

상태는 `draft → awaiting_approval → approved → executing → verifying → resolved | unresolved | verification_failed`로 기록한다. `rejected/expired/cancelled`는 실행 전 종결 상태다. 실행 중 취소는 미실행 단계만 멈추며 이미 반영한 Git 변경을 임의로 되돌리지 않는다. 재시작하면 저장된 PR·merge·Argo 상태를 먼저 조회해 진행 지점을 복구하고 같은 변경을 다시 만들지 않는다. 모든 상태 변경은 현재 상태·버전 조건을 검사한 원자 트랜잭션과 감사 기록을 요구한다.

## 5. 변경·GitHub·GitOps 경계

1. **정상 이미지 선정:** 이전 digest가 같은 ECR repository에 실제 존재하고, 이전 정상 CI/Argo 기록과 실습 앱의 imageID가 일치했으며, 현재 실패 digest와 다름을 확인한다. 단순히 오래된 태그나 모델 추천만으로 선택하지 않는다. 증거가 없으면 변경안을 내지 않는다.
2. **diff 정책:** 고정 Helm values 경로에서 `api.image.digest` 하나만 변경한다. JSON/YAML 파싱 전후 구조와 렌더된 Deployment를 비교해 이미지 외 차이·새 리소스·symlink·경로 우회·다른 파일 변경을 거부한다. base SHA와 PR head SHA를 고정하고 모든 비교는 GitHub의 최신 원본에서 재검사한다.
3. **권한 분리:** 조사 worker/모델에는 GitHub 쓰기·Kubernetes 쓰기 자격을 주지 않는다. 제안/PR 작성용 별도 GitHub App 설치 자격은 해당 저장소에만 발급하고, 코드 정책·CI로 경로를 제한한다. GitHub App 권한은 파일 경로 자체로 제한되지 않으므로 **main 보호 규칙과 별도 검사가 필수**다. 실습 동기화 주체는 `lab-target`만 sync/get 가능하고 production App·일반 Deployment patch 권한이 없다.
4. **PR과 merge:** C-03은 해결 요청을 받은 별도 PR 작성 주체가 제안한 diff의 **제안 PR을 먼저 생성**한다. 이때 `main`이나 클러스터에는 적용하지 않는다. 사람이 PR의 정확한 head에 GitHub review를 남기고 검사가 통과하면 **별도 실행 주체가 승인 유효시간 안에** base·head·diff·승인·필수 검사를 다시 확인해 보호된 `main`에 merge한다. 병합된 SHA도 재검사한 뒤 전용 workflow로 그 Argo revision만 동기화한다. 사람이 수동 merge하면 승인 만료 직전 상태를 서비스가 원자적으로 확인하기 어려우므로 첫 실행 경로에는 포함하지 않는다. 이 저장소의 현재 보호 규칙 부재가 해소되기 전에는 write 자격과 workflow를 활성화하지 않는다.
5. **경쟁·실패:** 새 workflow는 기존 S10/정상 CI의 `ai-ops-b6-lab` concurrency group과 Kubernetes `LabLease`를 공유한다. 이미 CI 원복 중이면 기다리거나 `already_recovered`로 종료한다. PR/merge 실패, Argo sync timeout, 실제 검증 실패는 단계별로 기록하고 유한 재시도 후 정지한다. 원복이 **해당 승인안에 명시된 조건과 digest** 안에 있을 때만 실행하며 그 밖의 변경에는 새 제안·승인이 필요하다.

`main` 보호에 적용할 최소 정책은 PR 경유, 승인 리뷰 1명 이상, 새 커밋 시 기존 승인 무효화, 지정 CI 상태 검사 필수, 강제 push/삭제 차단, 실행 주체의 우회 금지다. 실제 필수 검사 **이름**은 변경 workflow의 성공 기록에서 확인해 설정하고, 규칙을 API로 재조회해 강제 적용을 검증한다. 현재 GitHub 환경의 실행별 deployment 승인은 별개이며, 특정 diff에 대한 GitHub PR review를 대체하지 않는다.

## 6. 해결 판정과 평가 연결

복구 검증기는 승인한 merge SHA·목표 digest가 렌더/실제 Pod imageID에 반영됐는지, Argo `Synced/Healthy`, `/readyz`와 실습 사용자 경로(`/items`), 관련 경보·지표를 정해진 관찰 창에서 확인한다. `resolved`는 **모든 필수 조건의 근거가 있을 때만** 사용한다. 문제가 지속되면 `unresolved`, 자료가 없거나 시간 초과면 `verification_failed` 또는 `검증불가`를 기록한다. 최초 실패와 자동 원복, C 변경의 효과를 분리한다. PR이 만들어졌거나 Argo가 Healthy라는 사실 하나만으로 해결을 선언하지 않는다.

| 평가 ID | C 구현에서 검증할 경계 |
|---|---|
| R1 | 설명만 요청·미승인 안은 PR/동기화 0건 |
| R2 | 승인한 이미지 digest·diff와 실제 merge·Pod/사용자 경로 일치 |
| R3 | 다른 계정·만료 review·변경된 base/head/대상은 거부 |
| R4 | 중복 요청·승인 재전송·프로세스 재시작은 실행 1건 유지 |
| R5 | PR·merge·배포·검증 실패의 단계/근거와 승인된 복구 또는 중지 |
| R6 | 기존 CI가 이미 원복하면 변경 없이 그 상태 보고 |
| R7 | 실행 전/중 취소 시 이미 적용된 변경과 남은 단계 구분 |
| R8 | 다른 환경·파일·조치 유형은 실행 없이 거부 |

## 7. 구현 순서와 C-01 종료 기준

1. **C-02:** 제한된 사건 조회·로컬 사용자 확인·정형 요청/제안·정확한 diff/상태/감사 계약을 구현한다. 이 단계는 제안/조회만 허용한다. 현재 `deployment.json`을 Helm values로 옮기는 전환은 기존 S10/정상 CI와 함께 검증한다. 전환 전에는 오프라인 초안만 만들고 실제 제안 PR이나 실행 가능한 변경안은 만들지 않는다.
2. **C-03:** `main` 보호 규칙을 적용·검증하고 분리된 GitHub PR 작성/merge identity·실습 동기화 workflow를 준비한다. 요청된 변경의 제안 PR을 만들고, 승인 review의 불변 ID/commit/diff와 만료를 실행 직전에 검증한 뒤에만 병합·동기화 단계를 허용한다.
3. **C-04:** 목표 revision·사용자 경로·지표 검증, 승인된 실패 복구와 미해결/검증불가 보고를 연결한다.
4. **C-05:** R1~R8 및 격리 실습의 복구 데모를 수행하고, 독립된 최초 실패 주입과 기존 S10 자동 원복의 경쟁을 구분해 평가한다. 실습 실패 생성도 별도 승인과 유한 자동 정리 조건을 둔다.

**C-01 설계 산출물:** 허용 대상/변경, 로컬 사용자 인증, 사건 ACL, proposal→approval→execution→verification 연결, GitHub PR/merge·branch protection, 기존 CI 경쟁·실패 복구 정책을 이 문서에 고정했다. **활성화 선행 조건:** Helm values 전환과 기존 CI 호환 검증, 제한된 사건 조회, `main` 보호 규칙 및 실제 승인 identity, 별도 실행 자격·workflow가 필요하다. 이 문서 자체는 승인·자격 발급·클러스터 변경·PR 실행을 허용하지 않는다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
