# B-06 S10-02 실습 재설치·Argo 인계 실측

2026-10-22 KST **격리 실습 재설치·실제 권한/정책 검사·Argo 인계·수집 검증 완료**다. CI 실패 주입·자동 롤백·S10 전체 완료와 구분한다. [본문 없는 검증 JSON](assets/B6-s10-live-adoption.json)을 함께 확인한다.

## 구현한 기능과 처리 흐름

S10 준비 [PR #53](https://github.com/f-lab-edu/F-Lab-DevOps/pull/53)의 원격 327개 시험·이미지 검사를 확인해 main에 병합했다. 인계 소스는 전체 SHA `bf3d377cf89525643cf71da5ac711c9f24f1e543`로 고정했다.

현재 계정·클러스터·실습 부재 확인 → 현재 ECR·공식 DB/Redis 이미지와 API /32 검증 → 실습 전용 암호·manifest 생성 → 앱/DB/Redis/수집기 4개 재설치 → 공유 305초 지표 창·Prometheus 표본 확보 → 현재 앱 spec과 고정 소스 일치·서버 dry-run·공유 잠금 검사 → 최소 권한·대상 제한 정책 적용 → 실제 허용/거부 검사 → 제한된 임시 Argo 역할로 고정 SHA 수동 동기화 → 수집기 읽기 binding 변경 → 임시 토큰 폐기·보고서 재조회·운영 불변 확인 순서다.

여기서 **인계**는 실습 앱 Deployment의 변경 주체를 기존 운영자 직접 patch에서 Argo의 Git 정의로 바꾸는 것이다. **AdmissionPolicy**는 Argo 컨트롤러가 실습 namespace에서 다른 Deployment를 만들거나 앱을 삭제하는 요청을 서버에서 차단한다. **공유 Lease**는 실행 간 충돌을 방지하는 객체이며 복구 실패 시 보존한다. AI 수집기는 읽기 전용을 유지한다.

## 재설치와 실제 연결

- 계정 `716174522908`·서울 리전·EKS `urlshortener`를 확인했다. 운영 앱·AI worker·PVC 식별자를 설치 전후에 대조했고 기존 노드 3대의 여유를 확인했다.
- 실습은 `ai-ops-lab`에만 생성했다. 운영 Secret·RDS URL·API 키를 복사하지 않았다. 앱·DB·Redis·수집기 4개가 Ready다. 실습 앱은 DB 초기 준비 중 2회 재시작 후 정상화됐으며 이를 장애 주입으로 계산하지 않는다.
- 앱은 이번 ECR의 `sha-38b5d85` digest, 수집기는 승인된 실행 `39713103932`의 스캔 0건 digest를 사용했다. PostgreSQL·Redis는 기존 고정 digest의 공식 레지스트리 바이트 해시·linux/amd64 config를 다시 대조했다. 상세 digest는 검증 JSON에 있다.
- 설치 직후 남아 있던 공유 지표 창은 단축하거나 초기화하지 않았다. 창 종료 후 서버 dry-run에서 현재 spec 불변, 두 번째 잠금 취득 거부와 정상 해제를 확인했다.

## 실제 권한과 서버 정책 검사

총 21개 권한 확인과 3개 서버 dry-run을 수행했다. 권한 확인은 실제 Secret·exec·쓰기 요청을 대신 실행한 것이 아니라 API의 인가 판단 또는 Argo `account can-i`를 조회한 것이다. AdmissionPolicy의 검사는 실제 API 서버가 dry-run 요청을 허용/거부한 결과다.

| 검사 주체 | 허용 | 거부 |
|---|---|---|
| Argo `b6-lab:ci-sync` | `b6-lab/lab-target` get·sync | 설정 update·delete·override, 운영 `default/url-shortener` get·sync |
| `b6-s10-coordinator` group | `b6-scenario-lock` get·update, `lab-api` get | Lease 생성·다른 Lease 조회·앱 patch·Secret·운영 앱 조회 |
| `lab-investigator` | `argocd/lab-target` get | 운영 Application·Secret·앱 patch·exec·Lease update |
| 실제 Argo 컨트롤러 identity | 실습 `lab-api` update dry-run | 다른 Deployment create 및 `lab-api` delete dry-run |

정책의 서버 type checking에 expression warning이 없었다. AppProject는 종류·namespace를 제한하고 별도 서버 정책은 이름·삭제를 제한한다. 이 정책을 전체 클러스터의 일반 운영자 변경을 막는 기능으로 확대하지 않는다. CI에 필요한 AWS/IAM/EKS 신뢰 identity는 아직 만들지 않았다.

## 고정 SHA 인계와 수집 확인

`argocd/lab-target`은 Project `b6-lab`, 경로 `ai-ops/lab`, 입력 `deployment.json` 하나로 고정했다. 실제 관리 대상은 `ai-ops-lab/lab-api` Deployment **1개**다. 자동 sync·self-heal·prune은 활성화하지 않았고 Application 삭제 finalizer도 없다.

검증용 `ci-sync` 역할 토큰은 10분 만료로 생성해 메모리의 프로세스 환경에만 전달했다. 이 역할로 고정 revision을 동기화하고 Synced·Healthy·Succeeded를 확인한 뒤 토큰을 즉시 폐기했다. 최종 Project의 남은 role token은 0개다. GitHub Secret이나 영구 CI 토큰은 만들지 않았다.

인계 전후 실습 Deployment UID·Pod template 해시·Pod UID, 운영 앱·AI StatefulSet·PVC 식별자가 같았다. 앱 Pod를 교체하지 않고 메타데이터 소유권만 인계했다. 수집기 ConfigMap의 Argo binding을 `argocd/lab-target`으로 바꾸고 projected 파일 반영을 확인했다. 수집기를 재시작하지 않아 emptyDir 저장소도 유지했다.

인계 후 기존 전체 실습 설치기와 수동 시나리오 guard가 실제 apply/patch 전에 거부하는지 확인했다. 이후 S3/S11 직접 patch 실행기로 GitOps 소유 앱을 변경하지 않는다.

실제 준비 검사에서 HTTP `/readyz`·`/items`·`/items/_db` 200, Redis PING, 두 DB 세션의 단일 Primary 역할을 확인했다. Prometheus up=1·최근 5분 표본 10개·scrape health=1로 표본 기준을 충족했다. 수집·저장·재조회한 규칙 v3 보고서는 available 15·missing 7·사실 3·가설 0·partial/insufficient_evidence/unknown이다. Argo 현재 상태·배포 이력은 available이지만 CI 이력은 여전히 missing_history다. 현재 정상만으로 과거 CI 실패·자동 복구나 전체 정상 진단을 주장하지 않는다.

## 재개·정리와 남은 단계

비공개 manifest·설치/인계 계획은 `/private/tmp/ai-s10-adoption-20261022/`에 있다. manifest에는 실습 Secret이 있으므로 Git·공유 HTML·CI artifact에 넣지 않는다. 임시 폴더가 없어져도 소유권 guard를 우회해 새 manifest를 기존 namespace에 재적용하지 않는다. 현재 Application·고정 SHA·실제 이미지·공유 Lease와 복구 상태부터 확인한다.

전체 삭제 요청이 있는 정리에서는 Argo 종료 전에 `argocd/lab-target`의 Project·고정 경로·관리 대상 하나·finalizer 없음과 Project 토큰 부재를 확인한다. 해당 Application을 제거하고 실습 namespace를 지운 뒤, 실습 전용 Argo Role/RoleBinding·AppProject 및 `b6-lab-argo-deployment-scope` Policy/Binding도 정확한 이름과 목적 label을 대조해 제거한다. 정책을 제거하기 전 실습 Application이 더 이상 존재하지 않아야 한다. 운영 앱·기본 Project·다른 정책은 제거하지 않는다. 기존 전체 AWS 정리는 EKS까지 제거하므로 이 리소스도 사라지지만 실습만 정리하는 경우에는 namespace 밖의 구성도 확인한다. 이 문서 작성 중 삭제는 실행하지 않았다.

다음 S10-03은 실습 전용 CI와 신뢰 identity를 연결하고 실패 배포 → 최초 실패 기록 → 원래 digest/revision 자동 복구 → 복구 기록을 구현하는 단계다. 이후 S10-04 이력 계약·가져오기, S10-05 보고서 설명 검증이 남는다. 장애 주입·CI 자동 롤백·외부 모델 호출은 이번 실행에서 모두 0회이며 B-06 전체 완료·C 단계 진입은 보류한다.
