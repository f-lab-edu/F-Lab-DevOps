# B-03 이미지 workflow main 반영 검토

최신 후속 상태: **2026-10-19 KST** PR #32는 `c2169f7`로 main에 병합됐다. 이후 수동 빌드 및 사용자 승인 후 실제 OIDC·ECR 등록·digest·artifact 확인도 성공했다. 아래 내용은 병합 전 검토 이력이다. 현재 스캔 결과와 미완료 배포 항목은 [이미지 실행 기록](B3-image-registration.md)을 따른다.

2026-10-17 KST 기준 [PR #32](https://github.com/f-lab-edu/F-Lab-DevOps/pull/32)를 준비했다. **main 병합·수동 workflow 실행·OIDC 인증·이미지 등록은 아직 수행하지 않았다.** 경로 제한 구현과 CI 검증은 완료했다. 사용자가 미사용 SonarCloud 연동 제거를 선택해 자동 분석 workflow를 비활성화하고 PR에서 파일을 제거했다. 이전 오탐 확인은 더 이상 진행 조건이 아니다.

## 반영 범위

기존 `codex/ai-incident-copilot`는 최신 main과 앱 health 코드·운영 values가 달랐다. 앱 재배포가 함께 발생하지 않도록 최신 main `a70080becaab643604ad528cb7a50e7b21a3db63`에서 `codex/ai-image-registration`을 만들었다. 별도 worktree에 AI 패키지·시험 자료·진단 chart·수동 등록 workflow·AI 전용 Terraform root를 준비했다.

원래 작업 브랜치의 미커밋 내용은 유지했다. 앱 코드·운영 values·구조도·로컬 사이트·키·state·개인 tfvars는 PR에 포함하지 않았다. 앱 배포 workflow에는 정제 관측 artifact를 추가했지만 push 경로 필터는 main과 같고 PR 변경 파일 중 그 경로와 일치하는 파일은 0개였다. AI chart는 비활성·자동 sync 없음·미확정 revision 상태를 유지한다. 병합 직전 최신 main과 다시 대조해야 한다.

## 보안 검토와 수정

최초 PR head `31b5f95bed430e985838097da9e2ea3d66e2f9ea`에서 AI/호환성 CI는 통과했지만 SonarCloud 새 코드 Security Rating E로 Quality Gate가 실패했다. 분석 job의 success와 품질 판정의 failure는 별개다.

| 최초 지적 | 건수 | 현재 처리 |
|---|---:|---|
| S5443 공용 실행 경로 | 1 | RUNNER_TEMP 미설정 시 `/tmp/argocd` fallback 제거, 절대 runner 경로와 프로세스 미실행 회귀 검증. 해결 |
| S8707 에이전트 CLI 경로 주입 | 16 | 사용자 선택에 따라 모든 운영자 CLI의 명시적 경로·기본값에 지정 폴더 정책 적용. 최종 재분석에서 해결 |
| S2083 일반 출력 경로 주입 | 2 | 동일한 경계 검사와 개인 JSON의 원자적 게시·기존 파일/링크 덮어쓰기 차단. 해결 |
| S6739 Redis 비밀값 형태 | 1 | 테스트용 `DEMO_SECRET_SENTINEL` 표식. 실제 인증값이 아닌 합성 마스킹 시험 표식. 당시 분석의 미해결 지적으로 기록하며 후속 오탐 판정은 진행하지 않음 |

추가 폴더의 제외 목록에 쓴 경로 리터럴도 중간 분석에서 S5443 세 건으로 탐지됐다. 이 검증은 실제 소유권·쓰기 권한·기존 폴더·저장소 분리를 검사하는 방식으로 강화했다. 최종 코드에서 경로 관련 미해결 지적은 0개다. 분석 제외·NOSONAR·품질 기준 하향은 사용하지 않았다. 고정 합성 입력과 해시도 변경하지 않았다.

[경로 정책](cli-file-policy.md)은 프로젝트 자료·개인 저장·컨테이너 마운트·로컬 HTML 경계를 구분한다. 추가 위치는 운영자 환경변수 `AI_OPS_FILES_ROOT`로만 등록한다. 경계 밖·부모 탐색·symlink 우회·기본값 우회·기존 출력 덮어쓰기·공용 폴더·프로젝트 전체 쓰기 허용을 거부한다. A7 결과 재개는 정해진 이름과 원래의 실행 정보 검사를 유지한다.

## 최종 코드 검증

검증한 코드 head는 `a7c038a695cf9ca76da56ab866e4d453ce7c5a2c`다. 이후 문서와 Sonar workflow 제거 커밋의 상태는 PR에서 확인한다. 아래 Sonar 결과는 제거 전의 분석 이력이다.

| 검사 | 결과 |
|---|---|
| 로컬 단위 시험 | **152건 통과** |
| 고정 개발 입력·Helm 정적 경계·workflow YAML·Terraform fmt·diff 공백 | 통과 |
| [AI CI](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/38936593350) | contracts·image 성공 |
| [호환성 CI](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/38936593360) | terraform·application·helm 성공 |
| [Sonar 분석 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/38936593339) | 실행 성공 |
| [Sonar 품질 판정](https://sonarcloud.io/dashboard?id=f-lab-edu_F-Lab-DevOps&pullRequest=32) | 실패. 미해결 vulnerability **1건**, S6739만 남음 |

CodeRabbit은 수동 검토가 필요하다는 이유로 검토를 생략했으므로 독립 코드 검토 완료라고 표시하지 않는다. 실제 GitHub OIDC·이미지 등록·EKS 배포를 시험한 결과도 아니다.

## SonarCloud 연동 제거와 이후 작업

2026-10-17 사용자가 사용하지 않는 SonarCloud를 제거하도록 요청했다. GitHub의 `sonarcloud-analyze.yml` workflow를 비활성화했으며 `.github/workflows/sonarcloud-analyze.yml`을 PR에서 삭제했다. main에서 파일이 실제로 사라지는 시점은 PR 병합 후다. 외부 SonarCloud 프로젝트·과거 분석 기록·공유 Secret은 삭제하지 않았다.

과거 S6739 지적은 `build_dev_fixtures.py`의 `redis://user:DEMO_SECRET_SENTINEL@example.invalid:6379` 합성 마스킹 입력이었다. 실제 인증값이 아니며 고정 입력과 해시는 유지했다. 별도 로그인이나 오탐 상태 변경은 더 이상 요구하지 않는다. 이미 적용한 CLI 경로 제한과 `/tmp/argocd` fallback 제거는 유지한다.

남은 순서는 최신 PR의 AI·호환성 CI와 변경 범위를 검토한 뒤 main 반영, `publish=false` 빌드/artifact 확인, `publish=true` 사용자 환경 승인·OIDC·동일 이미지 push/digest 대조·등록 artifact 확인이다. ECR/IAM·등록 환경·변수는 [이전 적용 기록](B3-registry-apply.md)의 완료 상태를 유지한다. Sonar 연동 제거 자체가 코드 검토 완료나 이미지 등록 성공을 뜻하지 않는다.
