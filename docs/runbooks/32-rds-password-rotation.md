# 32. Terraform 버전과 RDS 비밀번호 회전

## Terraform 실행 버전

이 root의 검증 대상 CLI는 `terraform/.terraform-version`과 `required_version`에 지정한 **1.14.7**이다. 개발자는 해당 버전을 설치해 `terraform/`에서 실행한다. 현재 GitHub Actions에는 Terraform을 실행하는 작업이 없다. 추후 작업을 추가하면 같은 파일의 버전을 읽어 설치하고, 버전을 바꿀 때 두 설정과 CI를 함께 변경한다. RDS 모듈은 최소 1.11.1을 요구한다.

## 비밀번호 입력과 버전

`db_password`는 sensitive 및 ephemeral 입력이며 RDS 모듈의 write-only `password_wo`로 전달한다. Terraform plan/state에 새 비밀번호 값을 저장하지 않는다. 다만 입력 원천(`terraform.tfvars`, 환경변수, Secret 저장소), 셸 기록, CI 로그와 기존 state 백업은 별도로 보호해야 한다. 기존 로컬 `terraform.tfvars`를 다른 입력 방식으로 옮길 때는 안전하게 이전했는지 확인한 뒤 파일의 보관 여부를 결정한다.

비밀번호 값은 Terraform이 이전 값과 비교할 수 없다. `db_password_rotation_version`은 매 실행마다 지정하는 양의 정수다. 초기 배포는 1로 지정하고, **새 비밀번호를 적용할 때마다 이전 값보다 큰 값으로 함께 올린다.** 버전만 올리면 현재 전달한 비밀번호가 다시 적용될 수 있다. 비밀번호만 바꾸고 버전을 유지하면 RDS에 변경이 전달되지 않는다. 운영/실습의 입력값과 버전을 각 환경별로 안전하게 기록하고, 재실행·복구 시에도 최신 버전보다 낮추지 않는다.

## 회전 절차

현재 구성은 단일 `postgres` 계정의 비밀번호를 앱 연결에 사용한다. DB 변경과 새 Pod 준비 사이에 새 연결 실패가 발생할 수 있으므로 먼저 비운영 환경에서 절차를 확인하고 운영 작업 시간을 잡는다. 무중단 회전이 필요하면 별도 앱 계정과 겹치는 유효 기간을 갖는 자격 증명 방식이 필요하다.

1. 현재 `db_password_rotation_version`, Kubernetes Secret 이름(`<Helm release>-secret`), `DATABASE_URL` 및 `DATABASE_READ_URL`의 사용자 이름과 연결 대상, Argo CD 동기화 상태를 확인한다. 새 비밀번호와 두 URL을 Git에 넣지 않는 승인된 비밀 저장소/전달 경로에 준비한다. 읽기 URL도 동일한 `postgres` 계정을 사용하면 함께 교체한다.
2. 새 DB 비밀번호를 Terraform의 `db_password` 입력으로 준비하고 회전 버전을 **현재보다 크게** 지정한다. 기존에 예약된 RDS 변경 사항과 `apply_immediately` 설정을 확인해 계획된 중단 범위를 파악한 후 plan을 검토한다. Terraform apply로 RDS 변경이 완료될 때까지 기다린다. plan 파일·출력·로그에 비밀값을 남기지 않는다.
3. 외부에서 관리하는 Kubernetes Secret의 URL을 새 비밀번호로 갱신한다. 운영 Argo CD 애플리케이션은 `values.prod-secret.yaml`을 읽지 않으며, `rds.writeUrl`이 비어 있으면 Helm 차트도 운영 Secret을 생성하지 않는다. Secret 갱신은 클러스터에서 사용하는 비밀 관리 절차로 수행한다. URL 외의 Secret 키(예: `REDIS_URL`)는 유지한다.
4. `values.prod.yaml`의 `api.secretRevision`을 증가시켜 커밋한다. Argo CD가 Deployment의 Pod 템플릿 annotation을 동기화하면 새 Pod가 갱신된 Secret 환경변수를 읽는다. 비운영 Helm 배포에서는 해당 환경의 values에서 같은 값을 증가시킨다. Secret 환경변수는 실행 중인 Pod에 자동 반영되지 않는다.
5. 새 Pod의 rollout과 `/readyz`를 확인하고 쓰기·읽기 경로에서 새 연결이 성공하는지 확인한다. 이전 자격 증명으로 새 연결이 실패하는지 확인하고, 구 Pod 종료 및 남은 연결 오류를 관찰한다. 비밀번호나 전체 URL을 로그/티켓에 붙여 넣지 않는다.

## 실패 시 복구

- DB 변경이 실패했으면 기존 Secret과 Pod를 유지하고 Terraform 오류와 RDS 상태를 확인한다. 비밀번호 변경을 재시도할 때도 실제 적용된 버전과 입력값을 먼저 대조한다.
- DB는 바뀌었는데 새 Pod의 인증이 실패하면 Secret의 사용자 이름, URL 인코딩 및 DB 비밀번호가 같은지 먼저 확인하고 Secret을 수정한 뒤 `secretRevision`을 다시 증가시킨다.
- 이전 비밀번호로 되돌려야 하면 **그 비밀번호를 새 입력값으로 제공하고 회전 버전을 다시 증가**시켜 RDS에 적용한다. DB 적용 완료 후 Secret을 복구하고 `secretRevision`도 증가시켜 Pod를 교체한다. 단순히 버전을 낮추거나 Git의 annotation만 되돌리면 DB 비밀번호가 복구되지 않는다.

현재는 관리 리소스가 제거된 상태이므로 실제 회전과 연결 검증은 인프라 재구축 후 일괄 점검에서 진행한다.
