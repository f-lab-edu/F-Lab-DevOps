# B-03 수동 이미지 빌드·등록 실행 기록

2026-10-19 KST PR #32가 main에 병합된 뒤 이미지 수동 workflow를 실행했다. 소스는 `c2169f7b5398c07185906814d3b1d14b33ac7fab`이다.

## 등록 전 빌드 검증 완료

[빌드 실행 39204079671](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39204079671)은 `publish=false`로 성공했다. 잠금 의존성 설치·단위 시험·linux/amd64 빌드·통신 없는 read-only/non-root 실행·런북 검증·identity 기록·artifact 보존 단계를 통과했다. AWS 인증·이미지 push 단계는 실행하지 않았다.

artifact `ai-ops-image-39204079671-1`의 `image-registration.json`을 내려받아 다음 값을 확인했다.

- source revision: `c2169f7b5398c07185906814d3b1d14b33ac7fab`
- build image ID: `sha256:f5124a8e8184013834479474140c897645d9c7aab0edd3363eab31c9bcc96728`
- published: `false`; repository·registry digest: `null`
- Dockerfile·requirements.lock·runbook index의 SHA256 기록 존재

이 image ID는 빌드 전용 실행의 로컬 식별자다. ECR digest나 후속 등록 실행의 image ID로 사용하지 않는다. 등록 실행은 같은 소스를 다시 빌드·검증하고, 그 실행에서 검증한 이미지를 push·digest pull로 대조한다.

## 실제 등록 완료

사용자가 GitHub 환경을 승인한 뒤 [등록 실행 39204191122](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39204191122)가 성공했다. main 제한·전용 환경 승인 설정을 유지했으며 단위 시험·빌드·OIDC 인증·ECR 로그인·push·digest pull 대조·identity 기록·artifact 보존 단계가 모두 성공했다.

| 항목 | 확인값 |
|---|---|
| ECR | `716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops` |
| 소스 | `c2169f7b5398c07185906814d3b1d14b33ac7fab` |
| 등록 시각 | 2026-10-19 14:25:34 KST |
| 태그 | `sha-c2169f7b5398c07185906814d3b1d14b33ac7fab-r39204191122-a1` |
| registry digest | `sha256:f1c06fe3d07598c8e13db34217e3b6462a5ba4ddf94753648e34eb8e98c65511` |
| 등록 실행 image ID | `sha256:820d3c9a3fa0f8fd1ca96b23721618ba94e385b3a0e9133b096615279d2525af` |
| artifact | `ai-ops-image-39204191122-1`, `image-registration.json` |

artifact를 내려받아 `published=true`·source revision·태그·repository·digest를 실제 ECR과 대조했다. Dockerfile·의존성 lock·runbook index 해시도 해당 Git 커밋과 일치했다. ECR manifest 원문의 SHA256이 registry digest와 같고, manifest의 config digest가 artifact image ID와 같음을 별도로 확인했다. 빌드 전용 실행과 등록 실행은 서로 빌드가 다르므로 각각의 image ID를 구분한다.

## ECR 스캔 결과

**필수 후속 작업 `B-03-SEC-01` (미착수):** 사용자 요청으로 아래 HIGH 8건을 반드시 해결할 작업으로 등록했다. [AI 작업 리스트](<../../실습 가이드 문서 모음/AI/구현계획/AI 작업 리스트.md#b-03-sec-01-ai-이미지-high-취약점-8건-해결>)에서 수정·재등록·재스캔 및 완료 기준을 관리한다. 현재 지적을 해결한 상태는 아니다.

2026-10-19 14:25:42 KST 스캔 상태 `COMPLETE`를 확인했다. 보고 건수는 **HIGH 8·MEDIUM 14·LOW 7**이다. 스캔 완료는 취약점 없음이나 운영 배포 승인과 다르다. HIGH 지적은 다음 OS 패키지에 해당하며 실제 도달 가능성·수정 버전·대응 여부는 아직 검토하지 않았다.

| ECR에서 보고한 식별자 | 패키지 | 설치 버전 |
|---|---|---|
| CVE-2026-85091 | zlib | `1.3.dfsg+really1.3.1-1` |
| CVE-2026-103111 | pcre2 | `10.46-1~deb13u2` |
| CVE-2026-84784 | openssl | `3.5.7-1~deb13u2` |
| CVE-2026-84782 | openssl | `3.5.7-1~deb13u2` |
| CVE-2026-19499 | glibc | `2.41-12+deb13u4` |
| CVE-2026-5435 | glibc | `2.41-12+deb13u4` |
| CVE-2026-54873 | openssl | `3.5.7-1~deb13u2` |
| CVE-2026-72897 | openssl | `3.5.7-1~deb13u2` |

이미지 등록과 식별자 검증은 완료했다. 다음은 위 스캔 지적을 검토하고 실제 대상·네트워크·StorageClass를 구성해 diagnostics Pod를 배포·검증하는 작업이다. 현재 AI Pod 배포·읽기 권한/통신/PVC 재연결은 미실행이며 자동 조사·장애 조치 기능 전체가 완성된 상태는 아니다.
