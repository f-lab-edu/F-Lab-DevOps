# B-06 S10 배포·롤백 이력 준비

2026-10-21 현재 **출처 사전 점검 구현, S10 실제 실패·자동 롤백은 미실행**이다. 이 단계는 CI가 남긴 자료가 어느 실행에서 나왔는지 검사하는 기반 작업이다. 조사 수집기나 운영 배포 경로를 변경하지 않았다.

## 구현 기능과 처리 흐름

운영자가 검토한 실행 번호와 main 소스 SHA를 지정한다 → 운영자 컴퓨터의 기존 `gh` 인증으로 고정 GitHub 저장소를 조회한다 → 실행·재시도·workflow·브랜치·저장소를 대조한다 → 그 시도의 배포/롤백 artifact만 고른다 → GitHub API의 ZIP SHA-256과 실제 바이트를 대조한다 → 허용한 JSON 한 파일의 대상·시간·형식을 검사한다 → 개인 폴더에 점검 결과를 새 파일로 저장한다.

- [ci_history.py](../src/ai_ops/ci_history.py): 출처·압축 자료·관측 계약과 두 기록의 관계를 검사한다.
- [inspect_ci_history.py](../scripts/inspect_ci_history.py): 운영자용 읽기 전용 실행 경로다. worker에 GitHub 인증을 넣지 않는다.
- [test_ci_history.py](../tests/test_ci_history.py): 다른 실행 혼입·누락·충돌·압축 파일·시각·허위 복구 해석과 읽기 한도를 검사한다.

여기서 **artifact**는 GitHub Actions 실행이 보관한 결과 파일 묶음이다. **배포 검증**은 CI가 새 버전을 확인한 단계이고, **롤백 검증**은 이전 이미지로 되돌린 뒤 확인한 단계다. **출처 점검**은 이 기록의 저장소·실행·내용이 서로 맞는지 확인하는 것이다. 실제 클러스터에서 같은 대상이었는지 확인하는 작업은 아직 포함하지 않는다.

## 현재 보장하는 검사

저장소는 `f-lab-edu/F-Lab-DevOps`, workflow는 `.github/workflows/ci-cd.yml`, 브랜치는 main으로 고정한다. 검토한 전체 소스 SHA와 API의 head SHA가 일치해야 한다. 완료된 push/workflow_dispatch 실행만 허용한다.

같은 run ID라도 과거 attempt의 artifact를 재사용하지 않는다. 파일 이름은 `deployment-observation-<run>-<attempt>-deployment`와 `...-rollback`으로 고정하며, artifact 메타데이터의 실행·소스·브랜치도 대조한다. 누락·만료는 `missing_stages`, 동일 이름 중복과 해시 불일치는 오류로 남긴다. 목록이 100개를 넘으면 첫 페이지만 보고 판단하지 않고 중단한다.

각 API 호출은 20초·메타데이터 2MiB·ZIP 1MiB로 제한한다. ZIP을 디스크에 풀지 않으며 `deployment.json` 또는 `rollback.json` 한 파일만 읽는다. 부모 경로·다른 파일·심볼릭 링크·암호화 항목·64KiB 초과 본문을 거부한다. JSON 중복 키·미등록 필드·잘못된 enum·잘못된 UTC 시각도 거부한다. 원시 Argo 응답·자유 텍스트·인증값·ZIP은 저장하지 않는다.

두 기록이 있어도 즉시 복구 성공으로 표시하지 않는다. 최초 배포 검증 실패, 뒤이은 롤백 검증 성공, 서로 다른 이미지/예상 GitOps revision, 검증 시간 순서, 롤백의 예상 revision과 실제 sync/operation revision·Succeeded·Synced·Healthy·이미지·reconcile 시각을 함께 확인해야 `failure_then_rollback_verification_success`가 된다. 이는 **CI가 기록한 롤백 검증 성공**이며 사용자 HTTP 요청 복구나 원인 확정이 아니다. 현재 Healthy가 최초 `verification_result=failure`를 덮어쓰지 않는다.

## 이번 실제 GitHub 점검

| 기존 실행 | 결과 |
|---|---|
| [39538744371](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39538744371), head `c9a3f772c2f8ca1bf4a4f7c99a97afc8993dd5e9` | 정상 완료한 수동 실행의 출처 조회 통과. 선택되는 배포/롤백 관측 artifact 0개, 두 단계 모두 누락으로 기록 |
| [39202258522](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39202258522), head `432f9bde203c42cd643174f8bacd1eabce4bd73b` | 정상 완료한 push 실행의 출처 조회 통과. 선택되는 배포/롤백 관측 artifact 0개, 두 단계 모두 누락으로 기록 |

두 실행의 `pair_status=incomplete`를 보존했다. 기존 실행이 성공했다는 이유로 없는 배포·복구 기록을 채우지 않았다. 실제 선택 대상 ZIP 다운로드·해시 검증 성공은 아직 미검증이며, 압축/해시와 실패→복구 짝의 동작은 자동 시험으로만 확인했다. 점검 결과는 [요약 JSON](assets/B6-s10-ci-history-preflight.json)에 기록한다.

신규 시험 13개가 통과했다. 기존 전체 314개 시험을 이번 출처 도구 추가만을 위해 다시 실행하지 않았으며, 전체 327개 통과나 원격 CI 통과로 표시하지 않는다.

## 운영자 사용 방법

현재 계약 v1은 기존 운영 앱 `argocd/url-shortener`·배포 namespace `url-shortener`용이다. 실습 namespace로 이름을 바꿔 가져오면 안 된다. 결과에는 다음 값이 항상 유지된다.

```json
{
  "cluster_identity_verified": false,
  "evidence_import_enabled": false,
  "lab_target_compatible": false,
  "user_path_recovery_verified": false
}
```

`gh`는 기존 운영자 계정의 Actions 읽기 권한을 사용한다. 개인 키를 새로 입력하거나 Pod에 전달할 필요가 없다. 등록된 개인 자료 폴더에 실행마다 새 JSON 이름을 지정한다. 같은 파일은 덮어쓰지 않는다.

```sh
AI_OPS_FILES_ROOT=/전용/개인/자료폴더 PYTHONPATH=ai-ops/src python \
  ai-ops/scripts/inspect_ci_history.py \
  --run-id <실행번호> --expected-head <검토한_main_전체_SHA> \
  --output /전용/개인/자료폴더/ci-history-새실행.json
```

`missing_history`는 수집기에서 그대로 유지된다. 이번 출력은 조사 증거·원인 후보의 입력으로 연결되지 않으며 클러스터 읽기 권한, 앱 Deployment, AI 이미지·PVC, 외부 모델 정책을 바꾸지 않는다.

## 남은 구현 순서와 완료 조건

| 순서 | 작업 | 완료 조건 |
|---|---|---|
| S10-01 | CI 출처 사전 점검 | 이번 구현. 실제 메타데이터 읽기와 누락 보존 확인. 실제 유효 ZIP 수집은 대기 |
| S10-02 | 독립 실습 GitOps·실행 권한 준비 | `ai-ops-lab/lab-api`만 관리하는 별도 source/path·Application을 정의한다. 기존 수동 실습과 충돌하지 않게 소유권·실행 잠금·복구 책임을 이전한다. 운영 앱 values/namespace와 다른 경로임을 확인한다 |
| S10-03 | 실습 CI 실패·자동 롤백 기록 | main에서 수동 시작하고 동시 실행을 막는다. 기존 정상 digest와 GitOps revision을 고정한 후 검증한 잘못된 태그를 실습에만 적용한다. 실패 전/후 관측을 보존하고 CI가 원래 digest로 자동 복구한다. 복구 성공 후에도 최초 실패는 최종 CI 실패로 남긴다 |
| S10-04 | 클러스터까지 결합한 이력 계약·가져오기 | 기존 v1 운영 기록과 구분하는 계약이 필요하다. 실제 cluster/namespace/Deployment/Application UID, 허용 workflow/source, run/attempt·파일 해시·관측 창을 검증한다. 신뢰된 운영자 취득 경계에서 전달하고 조사 worker에는 GitHub·쓰기 권한을 주지 않는다 |
| S10-05 | 현재 정상과 과거 실패를 구분하는 보고서 | 동일 실습 대상으로 한 번 수집해 실패·자동 롤백의 서로 다른 시점과 인용 근거를 설명한다. 누락·충돌은 partial/unknown으로 남긴다. 자기 실습 자료·설정 복구와 운영 앱 불변을 확인한다 |

Argo가 직접 배포를 관리하기 시작하면 기존 S3/S11 실행기의 직접 patch와 소유권이 충돌할 수 있다. Application 추가 전에 실습용 GitOps values, replica/이미지/env 관리 주체, 실행 잠금, 자동 복구 실패 시 수동 복구 방법을 먼저 구현한다. namespace 전체·DB·Redis·수집기까지 실습 CI가 관리하도록 권한을 넓히지 않는다. 기존 실습 자료는 emptyDir에 있으므로 수집기 교체가 필요하면 기존 보고서 보존 절차를 재사용한다.

다음 작업은 S10-02의 격리된 GitOps 경로와 최소 실행 권한을 구현·검토하는 것이다. 권장 추론 깊이는 GPT-6 SOL high다. S10 실환경 성공·B-06 전체 완료·C 진입·라이브 모델 활성화는 계속 보류한다.

2026-10-21 후속: [S10-02 GitOps 준비](B6-gitops-preparation.md)에서 고정 Deployment 경로·수동 Argo 구성·최소 권한·서버 대상 제한 정책·공유 Lease를 구현하고 서버 dry-run과 잠금 경쟁/해제를 확인했다. 새 파일이 포함된 원격 검토 SHA 준비와 실제 권한 적용·소유권 인계·CI 연결은 남아 있다.

2026-10-22 검토 보완: 롤백 관측의 이미지 집합이 예상 이미지와 정확히 같고 `images_omitted=0`일 때만 복구 검증 성공의 근거로 인정한다. 혼합·생략 이미지는 `inconclusive`로 남긴다. 출처 점검 시험은 14개이며, GitOps 준비와 함께 전체 Python 327개·고정 fixture·기존 배포 경계/스키마 검사를 확인했다. 자세한 로컬 경고와 현재 미실행 범위는 [원격 반영 전 검토](B6-gitops-preparation.md#2026-10-22-원격-반영-전-코드-검토)를 따른다.
