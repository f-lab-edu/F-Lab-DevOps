# B-06 정상 관측 CI 실행자와 실습 전용 권한

기준: 2026-10-27 KST. 상태: **실행기·수집기 내부 검증·전용 workflow·IAM/RBAC를 배포하고 격리 실습 CI 한 건을 성공적으로 실행했다.** 정상 CI artifact의 자동 출처 검증·전달기/worker 연결과 B-06 전체 완료는 후속 작업이다. 유료 모델 호출은 하지 않았다.

## 무엇을 실행하는가

`ai-ops-normal-ci.yml`을 `main`에서 수동 실행하면 전용 GitHub 환경의 승인과 OIDC 역할을 거친다. 실행자는 현재 고정된 `ai-ops-lab/lab-api`에 자기 이름의 임시 아이템을 만들고 네 번 조회한다. 정상 기준은 같은 아이템의 **miss → hit → miss → hit**, 네 요청의 HTTP 200이다. 두 번째 쌍 전에는 35초를 기다리며, cold 동작은 자기 캐시 키를 제거하므로 자연 TTL 만료의 검증으로 해석하지 않는다.

조회 시각·응답 아이템·캐시 counter는 요청별로 실습 수집기에 checkpoint한다. 수집기가 같은 Pod의 제한된 로그를 직접 읽어 대조하고 규칙 보고서와 v3 projection을 저장한다. 원본 로그는 CI runner나 GitHub artifact로 반환하지 않는다. 그 뒤 실행자가 자기 아이템을 정리하고 실습 앱·수집기·Redis·Postgres·Argo 기준선이 바뀌지 않았는지 확인한다. 원복 확인과 공유 lease 해제가 모두 성공한 경우에만 정제 영수증을 발행한다.

CI의 concurrency 그룹은 기존 S10과 같은 `ai-ops-b6-lab`이며, 클러스터 공유 잠금도 같은 `b6-scenario-lock`이다. 다른 실행이나 미복구 실행의 잠금을 만료 시각으로 빼앗지 않는다. 실패·프로세스 중단 시에도 자기 데이터 정리를 시도하고, 확인하지 못하면 잠금을 유지한다. runner 강제 종료나 노드 장애로 `finally` 자체가 실행되지 않은 경우에는 자동 복구 완료를 주장하지 않으며, 보존된 잠금과 내부 checkpoint를 운영자가 확인해야 한다.

## 변경 파일

| 파일 | 역할 |
|---|---|
| `.github/workflows/ai-ops-normal-ci.yml` | main 수동 실행·전용 환경·공유 concurrency·고정 도구·정제 결과 한 파일 업로드 |
| `terraform/ai-ops-registry/b6-normal-ci.tf` | 정상 관측 전용 OIDC 역할과 EKS 그룹. 기본 비활성이며 기존 S10 역할을 확장하지 않음 |
| `src/ai_ops/normal_ci_contract.py` | 고정 Pod·이미지·클러스터·실행 코드 해시, CI 실행 출처와 네 조회 정제 영수증 |
| `src/ai_ops/normal_ci_rbac.py` | 현재 실습 API·수집기 Pod 두 개의 이름으로 제한한 exec와 실습 읽기·기존 lease 갱신 권한 생성 |
| `src/ai_ops/normal_ci_runner.py` | GitHub 출처·실제 클러스터 검증, 자기 데이터 네 조회와 실패 시 정리, 기준선·잠금 확인 |
| `src/ai_ops/normal_ci_source.py` | 수집기 내부 원본 로그 대조·규칙 조사·v3 정제·비공개 source 기록·최종 영수증 발행 |
| `scripts/prepare_b6_normal_ci.py` | 관리자가 현재 클러스터와 Pod를 읽어 binding·RBAC 계획만 생성. 적용·실습은 하지 않음 |
| `tests/test_normal_ci.py` | 원본 제외·원복·중단·출처·권한 범위와 수집기 내부 처리 시험 |

## 권한과 신뢰 범위

- AWS 역할은 해당 EKS의 `eks:DescribeCluster`와 `b6-normal-coordinator` 그룹 연결만 갖는다. ECR 등록·다른 AWS 리소스 변경·클러스터 관리자 정책은 없다. Terraform의 `enable_b6_normal_ci` 기본값은 `false`다.
- Kubernetes의 쓰기는 기존 공유 lease 한 개의 갱신과 **현재 고정한 두 Pod의 exec**다. Deployment 변경·Pod 생성/삭제·Secret 조회·운영 namespace 접근 권한은 생성하지 않는다. Argo는 `argocd/lab-target` 조회만 허용하며 동기화하지 않는다.
- exec 권한은 해당 Pod 안에서 임의 명령을 실행할 수 있는 권한이다. Kubernetes RBAC가 Python 동작별 허용 목록까지 강제하는 것은 아니다. 실습 API가 가진 실습 DB·Redis 자격에 간접 접근할 수 있으므로, 검토된 `main` workflow·전용 환경의 사용자 승인·고정된 Pod 이름과 UID·digest·코드 해시를 함께 사용한다. CI에 실습 DB 자격을 따로 복사하지 않는다.
- Pod 교체·재설치·수집기 이미지 교체 시 기존 binding으로 실행하지 않는다. 관리자가 현재 Pod를 재고정해 RBAC와 GitHub 환경 변수를 함께 갱신한다. 이름이 재사용돼도 UID 검사가 달라지면 실행을 거부한다.
- 영수증의 JSON 해시는 출처 인증 수단이 아니다. 이후 전달기에서 GitHub 저장소·workflow·main SHA·실행 ID·재실행 번호·artifact digest를 검증하는 단계가 추가돼야 자동 입력으로 사용할 수 있다.

## 원본과 결과의 보관 위치

| 자료 | 위치와 수명 |
|---|---|
| 생성·조회 checkpoint, 원본 로그, 원본 대응표가 포함된 projection, 원복 결과 | 수집기 내부 `/data/incidents/normal-ci/<세션>/source.json`, 디렉터리 0700·파일 0600 |
| 규칙 보고서와 고정 snapshot | 수집기의 기존 `/data/incidents/inc-…/run-…/` |
| GitHub 결과 artifact | `b6-normal-observation-<run_id>-<attempt>`의 `result.json` 한 개, 7일. 성공 시 정제 view·해시·보고서 참조·원복 확인, 실패 시 고정 사유와 복구 확인만 포함 |
| CI runner의 임시 파일 | 비밀값 없는 binding과 정제 결과. 원본 로그·아이템 ID·원본 대응표는 저장하지 않음 |

**현재 lab-collector의 `/data`는 emptyDir이다.** 이 기록은 해당 Pod 수명 동안만 남으며 수집기 재생성·인프라 정리 후에도 유지되는 영구 보관소가 아니다. 이번 단계에서 이를 PVC라고 설명하거나 재시작 후 source 복구가 가능하다고 판정하지 않는다. 다음 전달기·worker 연결 단계에서는 정상 원본과 보고서의 보관·만료·재시작 경계까지 확정해야 한다.

Slack URL·Watchdog URL·GitHub 토큰·OpenAI API 키를 이 workflow나 source 파일로 옮기지 않는다. 모델 호출 코드와 캠페인 활성화도 이 실행자에 넣지 않는다.

## 실제 활성화까지의 순서

1. 코드 검사 후 AI 이미지를 등록하고, 새 source 모듈이 포함된 digest로 실습 수집기를 배포한다.
2. 관리자 kubeconfig로 `prepare_b6_normal_ci.py --output <전용 경로>/normal-ci-plan.json`을 실행해 현재 binding·RBAC를 생성한다. 출력은 `AI_OPS_FILES_ROOT` 또는 개인 전용 저장 폴더에만 쓴다. `rbac` 값만 적용하고 `binding` 값만 GitHub 환경 변수로 전달한다. 전체 계획을 Kubernetes 매니페스트로 적용하지 않는다.
3. AI 전용 Terraform root에서 `enable_b6_normal_ci=true`의 변경 계획을 확인해 새 역할·정책·EKS 연결만 적용한다. 삭제 시에는 같은 AI root의 state가 함께 관리한다.
4. GitHub의 `ai-ops-normal-ci` 환경에 기존 실습 환경과 같은 main 제한·사용자 필수 승인을 설정한다. `NORMAL_CI_ROLE_ARN`과 `NORMAL_CI_BINDING`을 등록한다.
5. 역할의 긍정·부정 권한 검사를 실제로 확인한 뒤 정상 CI 한 건을 실행한다. 이 단계는 유료 모델 전송 없이 영수증 생성까지 확인한다.
6. 이후 정상 CI 출처 검증·큐·worker 연결과 v3 캠페인의 별도 검토·승인을 진행한다. 이전 일회 비교나 v2 캠페인의 승인을 재사용하지 않는다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.

## 이번 검증 결과

정상 CI 전용 15개를 포함해 AI 모듈 전체 **511개 시험 통과**. Terraform `fmt -check`·`validate`, Python 컴파일 및 두 workflow의 YAML 파싱도 통과했다. 전체 시험에서 기존 SQLite 연결의 `ResourceWarning`이 출력됐지만 시험 실패는 없었다. 실제 모델 호출은 없다.

현재 클러스터를 읽어 `/private/tmp/urlshortener-install-20261026/normal-ci-plan.json`에 binding과 RBAC 계획을 생성했다. AWS·RBAC 적용과 workflow 실행은 하지 않았다. 계획은 현재 Pod를 읽어 고정한 자료이며, 배포된 수집기가 새 source 모듈을 실행할 수 있는지까지 확인한 활성화 허가는 아니다. 준비 출력의 `ready_for_execution`과 `source_module_verified`는 `false`로 남긴다. 새 이미지 배포 후 계획을 다시 만들고 권한 검사를 진행한다.

정상 관측 실행·권한·source 코드도 상시 캠페인의 구현 해시에 포함했다. 향후 이 코드가 바뀌면 이전 캠페인 승인으로 계속 실행하지 않고 새 검토안을 준비한다.

## 2026-10-27 실제 배포·실습 결과

- [PR #92](https://github.com/f-lab-edu/F-Lab-DevOps/pull/92)를 검사 통과 후 `main`에 반영했다. AI 전체 511개 시험과 Terraform 검증을 통과했다.
- [AI 이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40468469936)은 필수 사용자 승인 후 성공했다. digest `sha256:5039521ae7ffa81ce7ed99edca5217c3de5a053fbd0a51c6bc89955248c452cf`의 ECR 스캔은 완료됐고 지적 0건이다. 해당 이미지로 격리 실습 수집기만 교체했다.
- 같은 AI Terraform state에서 기존 S10 역할을 보존하며 정상 CI용 역할·정책·EKS access entry **3개 추가, 변경·삭제 0개**를 적용했다. 전용 GitHub 환경은 `main` 제한·사용자 필수 승인으로 만들고 현재 Pod의 binding을 등록했다. RBAC는 현재 앱·수집기 Pod의 exec를 허용하고 Secret 조회·Deployment 변경·다른 Pod의 exec·운영 namespace 목록 조회를 거부하는 것으로 확인했다.
- [정상 관측 CI 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40469788128)은 필수 승인 후 성공했다. 정제 artifact `result.json`을 `NormalCIReceipt`로 다시 검증한 결과, 같은 아이템의 네 요청이 모두 HTTP 200이고 캐시 결과는 **miss → hit → miss → hit**였다. 자기 자료 삭제·배포 기준선 유지·공유 잠금 해제가 모두 참이며 외부 모델 호출은 **0건**이다. 수집기 내부 기록도 관측 4건·완료 상태이고, 실습 배포 4개는 모두 Ready, Argo `lab-target`은 Synced/Healthy, 공유 잠금은 해제된 상태로 확인했다.
- 이 검증은 정상 조회 한 건의 정제 영수증 발행까지다. 이후 [정상 CI 영수증 출처 검증과 worker 인계](B6-normal-ci-handoff.md)를 별도 구현했다. 수집기 원본 기록은 현재 Pod의 emptyDir 수명에 묶인다. 이미지·Pod 교체로 기존 binding 및 열려 있던 모델 캠페인 검토안은 재사용할 수 없으므로 향후 유료 캠페인은 새로 준비·검토한다.
