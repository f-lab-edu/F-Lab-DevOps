# B-06 상시 CI 조사 서비스 배포 경과 — 2026-10-23

실습 환경을 새 클러스터에 복원하고 `ai-ci` namespace에 전용 worker·CI 전달기를 배포했다. 운영 worker의 설정·이미지·PVC는 변경하지 않았다. 현재까지 검증 범위는 상시 프로세스·GitHub 목록 조회·권한 분리이며, 실제 완료 CI 자동 발견→접수→현재 자료 수집→규칙 보고서 저장·재조회와 중복 방지까지 확인했다.

## 확인한 범위

- 새 이미지 등록 실행 39846774835 성공, ECR 검사 COMPLETE·지적 0건. 이미지 소스는 PR #61 병합 커밋이다.
- 별도 실습 Pod 4개 정상, 실제 수집 검사 passed. Argo `argocd/lab-target` 고정 기준선 `ccbb475a02a2ced15adbabbaa23136d0d4fd833f` 인계·권한·서버 정책·운영 및 실습 식별자 불변을 확인했다.
- worker는 실습 대상과 지정 Argo Application 읽기만 허용한다. Secret 조회·운영 앱 읽기·배포 수정 거부를 확인했다. GitHub 토큰 파일은 worker에 없고 Kubernetes 토큰 파일은 relay에 없다.
- 전용 GitHub 파일 토큰으로 CI 목록·artifact 다운로드를 검증하고, 값은 출력·기록하지 않았다. 클러스터에서는 relay Secret에만 마운트한다.
- 두 Pod는 같은 노드의 1Gi 암호화 RWO PVC를 공유한다. 반복 목록 조회와 worker probe를 확인했다. 오래된 CI 1개는 skipped이며 새로운 자동 접수 성공으로 계산하지 않는다.

## 배포 중 발견·수정한 문제

최초 relay에는 필수 config/store 인자가 빠져 종료됐다. 또한 기본 fsGroup 재적용이 공유 큐의 0700/0600 권한을 변경해 worker의 비공개 권한 검사가 실패했다. relay·worker를 중지하고 접수 전의 전용 저장소 권한만 복구한 뒤 명시 실행 인자와 `OnRootMismatch` 정책으로 재배포했다. 시작 조건 수정 PR #62는 원격 검사 후 main에 반영했고, 관련 시험 43개가 통과했다. 임시 복구 Pod는 삭제했다. 운영 PVC·자격은 변경하지 않았다.

## 실제 자동 처리 결과

실습 CI 39848636802의 원본 artifact는 deployment=failure, rollback=success, result=recovered_after_failure였다. 실습 앱은 Ready, Argo는 Synced/Healthy로 복구됐고, CI 최종 failure는 최초 장애 보존의 의도된 결과다.

최초 전달기에서는 비동기 Argo operation=Running·이전 health=Healthy 집계값 때문에 계약 검사가 거부했다. PR #63에서 새 generation·직접 Pod 취득 실패·Ready=0·실패 이미지/revision 조건을 유지하며 비동기 실패 상태를 처리하도록 수정했다. 복구 Succeeded와 출처·해시·UID·시각 검사는 유지했다. 전체 시험 374개와 변경 없는 실제 artifact 검증이 통과했다.

수정 이미지 등록 실행 39850059064는 사용자 승인 후 성공했고 ECR 검사 COMPLETE·지적 0건이었다. 두 프로세스를 중지하고 동일 PVC를 유지해 worker→relay 순서로 교체했다. relay의 첫 자동 조회는 submitted=1·rejected=0, 다음 조회는 duplicates=1·submitted=0이었다. 운영자가 수동으로 큐에 접수하지 않았다.

큐 작업 `job-07c0445cd060434cb1f290f999f269df`는 첫 시도에 partial로 종료했고 보고서 JSON을 저장했다. 저장 보고서 계약·큐 HMAC·snapshot과 CI 출처/해시 결합 재검증이 통과했다. incident는 `inc-9c7cee70debd4590bdff1567a4ba57e6`, run은 `run-0809ad1c12e5485eb4b7befd906bceba`다. CI 두 관측은 원래 UTC 시각을 유지한 available이었다. 상세 식별자·해시·집계는 [본문 없는 검증 JSON](assets/B6-ci-service-deployment.json)에 있다.

보고서 엔진은 `rules-captured-ci-v1`, 외부 모델 호출은 없다. 결과는 partial/suspected_cause/unknown, 사실 4개·가설 1개다. available 18·missing 7·invalid 2·stale 1이며 이벤트 2개는 invalid_payload, 이전 배포 이력 1개는 outside_window였다. 이를 정상 자료로 바꾸거나 조사 실패로 숨기지 않았다. CI 자동 원복 성공과 현재 사용자 경로의 복구 확정을 구분한다.

worker에는 GitHub 토큰이 없고 relay에는 Kubernetes 토큰이 없다. 전용 1Gi EBS 암호화와 worker probe·두 Pod Ready/재시작 0을 확인했다. 원래 운영 worker·수신기와 운영 앱은 정상이며 별도 서비스 설정으로 변경하지 않았다. GitHub 토큰과 원문 보고서/증거는 문서·Git에 넣지 않았다.

## 남은 범위

이번 완료 범위는 격리 실습 CI 자동 조사 연결이다. 외부 모델 분석·운영 CI 전체 확대·C단계 조치 위임·장기간 가용성 측정은 포함하지 않는다. 최근 완료 15분·최대 20개 조회 제한과 RWO/단일 worker 가용성 한계는 유지한다. 누락·invalid 자료의 추가 수집 개선은 필요 시 별도 작업으로 다룬다.

전체 정리에는 `ai-ci` 두 프로세스 중지→전용 PVC/PV·EBS 실물 확인→namespace·두 RoleBinding 제거를 포함한다. 재설치 시에는 전용 GitHub 토큰 만료와 새 실습 UID·API 주소·Argo CI 자격을 확인해야 한다.
