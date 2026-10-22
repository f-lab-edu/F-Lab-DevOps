# B-06 S10-03 실습 전용 CI 실패·자동 복구

상태: **구현·로컬 검증 완료, 실제 CI 실행은 승인 후 별도 확인**. S10-04 이력 가져오기와 S10-05 보고서 검증 완료를 의미하지 않는다.

## 동작 흐름과 파일

main에서 검토한 실패 커밋 SHA를 수동 입력 → `ai-ops-lab-ci` 환경의 사용자 승인 → 전용 OIDC 역할 → EKS 최소 group 인증 → 정상 이미지·고정 revision·실패 소스 차이 확인 → 공유 Lease 취득 → 실습 이미지 취득 실패 → 실패 관측 저장 → 항상 원래 revision 복구 시도 → 정상 이미지/Ready/Argo 상태 확인 → 복구 검증 후만 잠금 해제 → 최초 실패를 CI 실패로 유지한다.

- `.github/workflows/ai-ops-lab-ci.yml`: main 수동 실행·GitHub 승인·직렬 실행·복구/관측 보관. 운영 앱 CI와 별도다.
- `src/ai_ops/b6_ci.py`: 출처·고정 이미지·대상 UID·Pod/ReplicaSet 소유 관계·실제 ImagePull 오류·복구·잠금 검사.
- `src/ai_ops/b6_gitops.py`: coordinator group의 실습 Pod 목록·ReplicaSet 조회 추가. 이미지 취득 실패가 해당 Deployment에서 발생했는지 확인하기 위한 읽기 권한이며 Secret·exec·Deployment patch 권한은 없다.
- `terraform/ai-ops-registry/b6-ci.tf`: 이미지 등록 역할과 별개의 OIDC 역할·정확한 클러스터 DescribeCluster/앱 ECR DescribeImages·EKS group 연결. 관리자 access policy를 연결하지 않는다.
- `tests/test_b6_ci.py`: 임의 변경·잘못된 실행 출처·UID/revision 변경·복구 실패 잠금·최초 실패 보존 검사.

실패 revision은 정상 기준 SHA와 비교해 `ai-ops/lab/deployment.json` 한 파일의 이미지 한 값만 달라야 한다. 정상은 실제 ECR의 digest, 실패는 실제 ECR에 없는 `b6-missing-<32자리 hex>` 태그만 허용한다. 실패 소스를 main 정상 선언으로 병합하지 않는다. Argo의 영구 targetRevision은 기준 SHA로 유지하고 실험에서는 별도 SHA를 일회성 sync한다.

## 인증과 권한

AI 전용 Terraform state에서 `enable_ai_ops_registry=true`·`enable_b6_lab_ci=true`로 적용한다. S10 활성 시 기존 ECR/IAM 4개 외에 역할·inline policy·EKS access entry **3개**가 추가된다. production state로 옮기지 않는다. OIDC subject는 저장소의 `ai-ops-lab-ci` environment 한 개이며 해당 환경에는 main만 허용하는 branch 정책과 사용자 필수 승인을 설정한다. 기존 이미지 등록 환경·역할은 변경하지 않는다.

GitHub environment 변수는 `S10_CI_ROLE_ARN`, `S10_BASELINE_REVISION`이며, `S10_ARGOCD_TOKEN`은 `b6-lab:ci-sync`의 24시간 만료 토큰만 사용한다. 운영 Argo 토큰·개인 API 키를 복사하지 않는다. Argo 토큰의 값은 출력·파일 저장하지 않고 환경 Secret에 전달한다. 실제 CI 종료와 후속 필요에 따라 폐기·회전한다. CI 검증 전에는 만료와 서버 Project 토큰 등록 상태를 확인한다. OIDC 세션은 15분이다.

EKS group `b6-s10-coordinator`는 고정 Lease get/update·실습 앱 get·실습 Pod list·ReplicaSet get만 허용한다. Argo 역할은 `b6-lab/lab-target` get/sync만 허용한다. 런타임의 AI worker·실습 수집기에는 CI 쓰기 권한이나 GitHub 인증을 주지 않는다.

## 실패·복구와 artifact 의미

배포 전 정상 기준선이 아니거나 실패 태그가 실제 존재하면 변경 전에 중단한다. 실행은 공유 Lease와 GitHub concurrency로 직렬화한다. 잘못된 이미지의 Pod가 대상 Deployment/ReplicaSet 소유이고 실제 `ErrImagePull` 또는 `ImagePullBackOff`, Ready 0인 것을 확인해야 의도한 실패로 기록한다.

배포 명령 실패·응답 유실에도 상태 파일에 복구 요청을 먼저 남기고 별도의 `always()` 복구 단계가 정상 revision 동기화를 시도한다. 원래 이미지·Ready 1·Deployment 관측 generation·Argo Synced/Healthy·Succeeded·원래 operation revision을 모두 확인해야 복구 완료다. 복구되지 않으면 공유 Lease를 보존하며 다음 실행을 막는다. 정상 복구 후에는 305초 지표 창을 유지한다.

artifact `b6-lab-observation-<run>-<attempt>`에는 `deployment.json`, `rollback.json`, `result.json`만 보관한다. 자료는 스키마 2의 실습 관측으로 기존 운영 앱 v1과 구분한다. 저장소·workflow·run/attempt·소스 SHA·실습 대상 UID·정상/실패 revision·고정 상태/시각을 담으며 원시 로그·Secret·CLI 오류 본문·상태 파일·앱 manifest는 올리지 않는다. 현재 `evidence_import_enabled=false`와 `user_path_recovery_verified=false`다. CI 복구 확인은 사용자 HTTP 경로 복구 확인과 다르며 S10-04에서 신뢰할 가져오기를 추가해야 한다.

최종 CI는 의도적으로 실패한다. 이것은 처음 배포가 실패했다는 기록을 보존하기 위한 것이며, 원복 성공 여부는 rollback 관측과 result를 따로 확인한다. CI 성공/실패 아이콘만 보고 실습 앱이 고장 난 채 남았다고 판단하지 않는다.

## 중단·정리·재설치

runner 강제 종료·job 시간 초과·AWS 세션 만료 등으로 `always()` 단계가 실행되지 못하는 경우까지 자동 복구를 보장하지 않는다. 이때 원래 run/attempt의 artifact·공유 Lease holder/UID·Application·Deployment를 확인하고 기존 실행이 끝난 뒤 고정 baseline으로 수동 복구한다. 이미지·Ready·Argo 상태와 해당 실행 소유권을 확인한 뒤에만 Lease를 해제하며 오래됐다는 이유로 삭제하거나 탈취하지 않는다.

전체 AWS 정리는 AI root를 production보다 먼저 destroy해 EKS access entry·전용 역할도 제거한다. state 버킷·이전 버전 보존 정책은 기존 사용자 결정을 유지한다. **S10을 사용 중인 같은 state에 일반 AI 설치를 다시 적용할 때 `enable_b6_lab_ci=true`를 빠뜨리면 CI 역할 삭제 계획이 나올 수 있으므로 중단하고 입력을 대조한다.** 새 전체 재설치의 기본 AI 설치는 S10 비활성으로 진행하고 선택 실습 준비 후 명시적으로 활성화한다. GitHub 환경·변수는 AWS 자원이 아니며 AWS 삭제만으로 지워지지 않는다. 재설치 후 Argo 토큰은 옛 클러스터의 것을 쓰지 않고 재발급한다.

로컬 전체 Python 335개·S10 신규 8개·기존 GitOps 21개와 Terraform validate를 확인했다. Python 3.13 전체 검사의 기존 SQLite ResourceWarning 로그 캡처 현상은 앞 단계와 같은 경고 필터로 분리했다. 실제 OIDC·실패·자동 복구의 완료 여부는 승인된 실행 결과에 별도로 기록한다.
