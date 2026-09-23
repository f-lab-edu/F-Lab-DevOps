# 30번: RDS 데이터와 ECR 롤백 이미지 보존

이 문서는 운영 반영과 일괄 검증 때 사용할 절차다. 현재 코드 변경만으로 AWS 리소스에 정책이 적용되거나 복원 실험이 완료되지는 않는다.

## 환경별 정책

| 항목 | `environment=production` (기본값) | `environment=practice` |
| --- | --- | --- |
| RDS Primary 삭제 보호 | 켬 | 끔 |
| RDS Primary 삭제 시 최종 스냅샷 | 생성 | 생략 |
| ECR 저장소 강제 삭제 | 금지 | 허용 |
| ECR 이미지 자동 정리 | 태그 없는 이미지에만 적용 | 태그 없는 이미지에만 적용 |

실습 환경은 **별도 Terraform state와 다른 `project_name`**으로 생성한다. 운영 state에서 `environment`만 `practice`로 바꾸면 운영 DB의 삭제 보호가 해제되므로 금지한다. Read Replica는 원본 Primary에서 다시 만들 수 있는 읽기 복제본으로 취급하며, 삭제 시 최종 스냅샷을 만들지 않는다. 장애 대응 중 Replica를 쓰기 DB로 승격했다면 독립된 운영 DB가 되므로, 보호 설정과 Terraform 관리 상태를 다시 정해야 한다.

## ECR 정책 적용 전 확인

1. GitOps의 `values.prod.yaml`에 기록된 현재 이미지 태그와 이전 안정 태그를 확인한다. 실제 Argo CD 적용 이미지 digest와 두 태그의 ECR digest를 대조한다.
2. `aws ecr describe-images --repository-name <repository> --image-ids imageTag=<tag>`로 두 이미지가 존재하는지 확인한다. 없으면 lifecycle 정책부터 적용하지 말고 이미지를 복구한다.
3. 변경 예정인 lifecycle 정책을 ECR preview로 검사한다. 현재 이미지와 이전 안정 이미지가 만료 대상으로 표시되면 적용을 중단한다. 이 정책은 `sha-`와 `release-`를 포함한 **모든 태그 있는 이미지**를 자동 만료하지 않는다.
4. 필요하면 기존 안정 이미지에 `release-<full-commit-sha>` 태그를 추가해 검증된 롤백 후보임을 표시한다. 먼저 SHA 태그와 새 release 태그의 digest가 같은지 확인한다. 전체 커밋 SHA를 확인할 수 없으면 임의 값을 넣지 말고 기존 태그와 digest를 운영 기록에 남긴다.
5. 정책 반영 후 현재 이미지와 이전 안정 이미지를 각각 pull할 수 있는지 확인한다.

CI는 새 배포가 Argo CD에서 해당 GitOps 커밋으로 `Synced`·`Healthy`가 되고 `/readyz`, `/items` 응답을 통과한 뒤 `release-<full-commit-sha>` 태그를 붙인다. 기존 release 태그가 있으면 SHA 이미지와 digest가 같을 때만 재사용한다. release 태그 생성에 실패하면 job이 실패하며, `sha-` 태그는 자동 만료되지 않으므로 이미지는 계속 남는다. 실패 원인을 해결한 뒤 release 태그를 복구한다.

태그 있는 이미지를 자동 정리하지 않으므로 저장 비용은 시간이 지나며 증가한다. 정기적으로 보관량을 확인하고, 삭제 전 현재 배포 digest·이전 안정 digest·복구 후보 목록과 대조한다. 운영 중인 이미지나 복구 후보를 삭제하지 않는다. 나중에 개수 제한을 도입할 때도 동일한 검사를 자동화하고 ECR preview에서 실제 삭제 대상을 확인한 뒤 적용한다.

## RDS 삭제 및 스냅샷 보관

- 운영 Primary는 `deletion_protection=true`, `skip_final_snapshot=false`로 관리한다. 삭제가 승인된 경우에만 보호를 해제한 Terraform 변경을 **별도 apply**하고, 이후 삭제 계획과 대상 식별자를 다시 확인한다. 보호 해제와 삭제를 한 번의 변경으로 처리하지 않는다.
- 운영 Primary의 자동 백업 보관 기간은 7일이다. 최종 스냅샷은 삭제 시 생성하며, 최소 90일 보관한다. 90일이 지나도 복원 필요성·규제 요건·대체 백업을 검토한 뒤에만 수동 삭제한다. 현재 최종 스냅샷의 자동 만료는 구성하지 않았다.
- 최종 스냅샷 식별자에는 Terraform RDS 모듈이 고유 접미사를 붙인다. 생성 후 실제 식별자와 상태를 기록한다. AWS에서 스냅샷을 만들 수 없는 DB 상태에서는 삭제 전에 별도 복구 계획을 세운다.

## 격리 환경 복원 실험

운영 DB를 삭제하지 않고 현재 Primary의 **수동 스냅샷**을 만들어, 별도 이름의 격리된 DB 인스턴스로 복원한다. 복원 인스턴스에는 격리용 subnet group과 security group을 지정하고 운영 앱이 접근하지 않게 한다.

1. 스냅샷 생성 완료와 복원 인스턴스 `available` 상태를 확인한다.
2. 격리된 클라이언트에서 새 endpoint로 접속해 대표 테이블의 행 수와 샘플 데이터를 원본과 비교한다. 앱의 읽기 경로도 격리된 설정으로 확인한다.
3. 스냅샷 시점부터 접속·데이터 확인까지 걸린 시간을 기록한다. 비밀번호와 접속 문자열은 로그에 남기지 않는다.
4. 격리 인스턴스와 실험용 스냅샷을 정리한다. 운영 최종 스냅샷은 위 보관 정책에 따라 유지한다.

완료 기준은 Terraform 설정 확인뿐 아니라 RDS 복원 후 데이터·접속 성공, ECR 현재·이전 이미지 pull 성공, 배포 성공 시 release 태그와 원본 이미지 digest 일치다.
