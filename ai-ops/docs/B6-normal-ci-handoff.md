# B-06 정상 CI 영수증 출처 검증과 worker 인계

기준: 2026-10-27 KST. 이 단계는 [정상 관측 CI 실행](B6-normal-ci-executor.md)의 다음 연결이다. 기본값은 **비활성**이며, 유료 모델 호출·운영 앱 변경·S10 실패 이력의 재분류를 하지 않는다.

## 처리 흐름

1. 별도 승인 환경에서 성공한 `ai-ops-normal-ci.yml` 실행만 GitHub API에서 찾는다. 전달기는 저장소·workflow 경로·`main`의 전체 SHA·실행 ID와 재시도 번호·성공 상태·시각을 대조한다.
2. 이름이 정확히 맞는 artifact 하나를 읽어 GitHub ZIP digest와 ZIP 안의 `result.json` 단일 파일·크기를 검증한다. 정제 영수증의 출처·시각·현재 고정한 클러스터/Pod binding 해시·실행 코드 해시·view 해시를 확인한다. 원본 로그는 GitHub나 전달기 큐로 옮기지 않는다.
3. 전달기는 기존 S10 실패·원복 작업과 **다른** `normal_ci_receipts` 표에 정제 영수증만 보관한다. 같은 실행·시도는 중복 처리하고 같은 키에 다른 artifact가 오면 거부한다. 기존 비공개 큐의 HMAC 키로 저장값을 결합한다.
4. 같은 PVC의 worker가 HMAC과 계약을 다시 확인해 정제된 v3 view에서 규칙 요약을 만든다. 결과 상태는 `ready` 또는 `needs_review`다. 모델 기능과 무관하게 실행하며, 여기까지 외부 모델 호출은 0건이다. 영수증과 요약은 7일 뒤 정리하고 최대 100건으로 제한한다.

## 신뢰 경계와 남은 일

- `VerifiedNormalHistory` 객체만으로 출처를 인증하지 않는다. 실제 인증 경계는 GitHub API의 실행·artifact ZIP 검증과 보호된 workflow/환경이다. relay의 GitHub 토큰은 worker에 전달하지 않는다.
- 현재 binding은 앱·수집기 Pod UID, Deployment UID, 이미지 digest와 클러스터 지문을 고정한다. Pod 또는 이미지가 바뀌면 새 binding을 생성해 CI 환경 변수와 relay/worker ConfigMap을 함께 갱신해야 한다. 과거 정제 영수증은 자동으로 새 실습의 현재 증거가 되지 않는다.
- 수집기 원본 로그·원래 규칙 보고서는 `emptyDir`에 있다. worker의 `normal_ci_rules_summary`는 **정제 view에서 파생한 별도 요약**이며, 수집기 원본 보고서와 동일한 파일이라고 주장하지 않는다. Pod 재생성 후 원본을 복구할 수 없으므로 원본 장기 보관과 실제 모델 제출의 결합 검증은 후속 단계다.
- 정상 영수증을 모델 gateway에 자동 제출하는 기능은 이 단계에서 켜지 않는다. 새 v3 캠페인의 데이터·비용·기간 검토 및 별도 승인이 먼저 필요하다. 이전 검토안은 이미지와 코드 변경 뒤 재사용할 수 없다.

## 설치·검증 순서

1. 검사 통과한 `main`의 새 AI 이미지를 ECR에 등록하고 digest를 검증한다.
2. [정상 CI 준비 절차](B6-normal-ci-executor.md)에 따라 현재 Pod 기준의 binding과 RBAC를 다시 만들고 권한을 확인한다. 새 binding을 `ai-ops-normal-ci` GitHub 환경의 `NORMAL_CI_BINDING`에도 넣는다.
3. `prepare_ci_service.py --normal-binding <개인 전용 경로>/binding.json`으로 manifest를 만들고 `ai-ci`의 ConfigMap·relay·worker에 새 이미지와 binding을 적용한다. 기존 PVC와 GitHub 토큰 Secret은 보존한다. 두 Pod의 준비 상태와 모델 기능 꺼짐을 확인한다.
4. 검증한 정상 CI 실행 ID와 SHA를 전달기의 `--normal-run-id`/`--expected-head`에 명시해 한 건 접수한다. worker의 `normal_ci_receipts` 상태가 `ready`이고 `external_model_calls=0`인지 확인한다. 같은 실행 재접수는 중복으로 끝나야 한다.

## 실제 적용 및 검증 기록

- 2026-10-27 KST: PR #94(`543ca36d236e0640a4568e063529cd367494aa1b`)의 시험 515개와 이미지 빌드가 통과했다. [이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40472193246)은 성공했고, ECR digest `sha256:f3260077d2518dd9f9ac31c525be15bdbe9ef8f51db5ebcfca16d1ddc67e51cc`의 검사 상태는 `COMPLETE`, 발견 항목은 0개였다.
- `ai-ci`의 `ci-config`에 `normal-ci-binding.json`만 추가했다. 기존 세 설정값, `ci-queue` PVC와 `ci-relay-github` Secret의 식별자는 유지했다. `ci-worker`와 `ci-relay`는 위 digest로 각각 1개 준비됐고, relay에 정상 CI binding 인자를 추가했다. 모델 관련 환경 변수는 설정하지 않았다.
- 앞서 성공한 [정상 관측 CI #40469788128](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40469788128)을 명시적으로 접수하자 worker가 `normal_ci_rules_summary`를 `ready`로 확정했다. `external_model_calls=0`이었고, 같은 실행을 다시 접수하면 `duplicate=true`였다.
- 새 [정상 관측 CI #40472973002](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40472973002)가 성공한 뒤에는 명시적 재제출 없이 전달기의 주기 조회로 같은 `ready` 요약이 생성됐다. 이 기록도 `external_model_calls=0`이었다. 이는 정상 CI 결과의 자동 접수·규칙 요약까지 검증한 것이며, 원본 장기 보관이나 모델 gateway 자동 제출을 검증한 것은 아니다.

후속 원본 보관과 v3 제출 연결은 [정상 CI 원본 보관과 모델 보고서 연결](B6-normal-source-persistence.md)에서 관리한다. 기존 영수증의 Pod binding은 새 PVC/이미지 전환 뒤 재사용하지 않는다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
