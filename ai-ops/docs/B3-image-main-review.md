# B-03 이미지 workflow main 반영 검토

2026-10-17 KST 기준 [PR #32](https://github.com/f-lab-edu/F-Lab-DevOps/pull/32)를 준비했다. **main 병합·workflow 수동 실행·OIDC 인증·이미지 등록은 아직 수행하지 않았다.** SonarCloud 보안 판정을 검토 중이며 사용자가 선택한 지정 폴더 정책을 구현했다.

## main 반영 범위를 분리한 이유

기존 `codex/ai-incident-copilot`는 최신 main과 앱 health 코드·운영 values가 달랐다. 그 브랜치를 그대로 병합하면 앱 배포를 트리거할 수 있어 최신 main `a70080becaab643604ad528cb7a50e7b21a3db63`에서 `codex/ai-image-registration`을 만들었다.

AI 패키지·합성 시험 자료·진단 chart·수동 등록 workflow·전용 Terraform root만 복사했다. 기존 작업 브랜치의 미커밋 내용을 유지하며 별도 worktree에서 작업했다. 앱 코드·운영 values·구조도·로컬 사이트·키·state·개인 tfvars는 PR에 포함하지 않았다. 앱 배포 workflow에는 기존에 구현한 정제 관측 artifact를 포함했지만, push 경로 필터는 main과 같고 PR 변경 파일 중 그 경로와 일치하는 파일은 0개다. AI chart는 비활성·자동 sync 없음·미확정 revision 상태를 유지한다.

## 검증 결과

초기 PR head는 `31b5f95bed430e985838097da9e2ea3d66e2f9ea`다.

| 검사 | 결과 |
|---|---|
| 로컬 단위 시험 | 최초 142건 통과 |
| Helm 정적 보안·권한·통신·저장 검사 | 통과 |
| Terraform fmt·workflow YAML·diff 공백 검사 | 통과 |
| [AI PR CI](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/38933571623) | contracts·image 성공 |
| [호환성 PR CI](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/38933571773) | terraform·application·helm 성공 |
| [Sonar 분석 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/38933571711) | 실행 성공, 별도 Quality Gate는 실패 |
| [Sonar 품질 판정](https://sonarcloud.io/dashboard?id=f-lab-edu_F-Lab-DevOps&pullRequest=32) | 새 코드 Security Rating E, 요구 A |

분석 실행 job의 success를 품질 판정 통과로 간주하지 않는다. CodeRabbit은 이 저장소에서 수동 검토가 필요하다는 사유로 검토를 생략했으므로 독립 코드 검토 완료라고 표시하지 않는다.

## 최초 보안 지적 20건과 처리 상태

| 종류 | 건수 | 대상·검토 내용 |
|---|---:|---|
| 공용 임시 디렉터리 실행 경로 S5443 | 1 | `capture-deployment-observation.py`의 RUNNER_TEMP 미설정 시 `/tmp/argocd` fallback. fallback 제거·절대 runner 경로 검사와 프로세스 미실행 회귀 시험을 추가했다 |
| 비밀값 형태 문자열 S6739 | 1 | `build_dev_fixtures.py`의 Redis URL. 값은 `.invalid` 호스트의 `DEMO_SECRET_SENTINEL` 합성 마스킹 시험 표식이며 실제 발급 인증값이 아니다. 분석 제외나 표현 우회로 숨기지 않았으며 오탐 여부 검토 대상이다 |
| 일반 경로 주입 S2083 | 2 | `score_holdout.py`·`score_manual_baseline.py`의 운영자 CLI 출력 경로. 허용 저장 위치·덮어쓰기·symlink·파일 권한 정책 검토가 필요하다 |
| 에이전트 CLI 경로 주입 S8707 | 16 | A7 실행/채점/검토 도구와 contracts·deployment_check·evidence의 운영자 지정 경로. 실제 AI 도구가 이 CLI를 노출하는지와 별개로, 지정 폴더 경계 정책을 정한 뒤 처리한다. 일괄 오탐으로 처리하지 않았다 |

공용 임시 경로 수정 후 로컬 시험은 **143건 통과**했다. 수정 커밋은 별도 PR에 push해 재분석을 요청했다. 최신 원격 결과는 PR에서 확인하며 최초 20건 수치를 수정 후 남은 건수로 간주하지 않는다.

## 다음에 필요한 결정과 작업

운영자 CLI는 기존 설계에서 사용자가 선택한 로컬 자료·저장 경로를 받는다. 지정 폴더만 허용하도록 바꾸면 기존 사용법과 컨테이너 마운트·로컬 평가 경로가 달라질 수 있어 사용자에게 다음 정책 선택을 요청했다.

- 지정된 자료·저장 폴더 안으로 접근을 제한하고 경계 밖·symlink·덮어쓰기 거부를 검증한다.
- 기존 명시적 운영자 경로 선택을 유지하고 실제 LLM 도구 노출 경계와 함께 Sonar 오탐 여부를 검토한다.

지정 폴더 정책의 원격 분석과 남은 보안 지적을 확인한 뒤 main 반영 여부를 결정한다. 이후 publish=false 수동 빌드·artifact, publish=true 환경 승인·OIDC·push/digest·등록 artifact를 검증한다. 품질 기준을 낮추거나 실패 상태에서 main을 병합하지 않았다. ECR/IAM 생성과 GitHub 환경·변수 준비는 [적용 기록](B3-registry-apply.md)에서 완료 상태를 유지한다.


## 지정 폴더 정책 구현 후 확인

사용자가 지정 폴더 제한을 선택해 모든 운영자 CLI의 명시적 인자와 기본값에 공통 경로 정책을 적용했다. 프로젝트 설정/평가 입력, 개인 저장, 컨테이너 마운트, 로컬 HTML 경계를 분리했다. 추가 폴더는 운영자 환경변수 하나로 등록하며 절대 경로·소유권·쓰기 권한을 검사한다. 경계 밖·부모 탐색·외부 입력 링크·출력 링크·기존 파일 덮어쓰기를 차단한다. A7 결과 재개는 정해진 파일 이름과 기존 실행 정보 검사를 통과해야 한다. [경로 정책과 기존 자료 이용법](cli-file-policy.md)을 참고한다.

공용 임시 경로 수정 커밋 b961754a73c51d0e91fed7f98b0abb34b07d4215에서는 AI/호환성 CI가 통과하고 S5443이 해결됐지만 미해결 보안 지적은 19건이었다. 경로 제한까지 적용한 뒤 로컬 전체 **151건**과 개발 합성 입력 고정 검사를 통과했다. 이 경로 제한의 원격 재분석은 후속 커밋에서 확인한다. 합성 Redis 마스킹 표식은 그대로 유지했으며 품질 기준을 낮추거나 실제 인증값으로 취급해 불필요하게 키를 교체하지 않았다.
