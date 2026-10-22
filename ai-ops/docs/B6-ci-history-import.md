# B-06 S10-04 CI 이력 검증과 조사 입력 연결

상태: **검증·가져오기 구현 및 기존 실제 artifact의 읽기 전용 연결 완료**. 실제 두 관측은 현재 15분 수집 창보다 오래되어 `stale`이다. 현재 조사에 사용할 최신 CI 증거 확보나 S10-05 보고서 검증 완료를 뜻하지 않는다.

## 무엇을 구현했나

[실습 CI](B6-ci-experiment.md)가 남긴 이미지 오류와 자동 롤백 기록을 현재 실습 앱의 조사 입력에 연결한다. GitHub에 있는 파일이라는 이유만으로 신뢰하지 않고 실행 출처, ZIP 해시, 내용의 일관성과 현재 대상의 식별자를 대조한다. 현재 Argo의 Healthy만으로 과거 장애가 없었다고 판단하지 않는다.

동작은 **운영자 GitHub 인증으로 실행 조회 → 완료된 main 실습 workflow 확인 → 해당 run/attempt artifact 선정 → ZIP SHA-256 및 정확한 세 파일 검증 → 실패·복구의 상태/UID/revision/시간 순서 검사 → AWS ARN·현재 API 주소/인증서 확인 → 현재 Deployment·Application UID 및 원복 이미지/revision 대조 → 두 배포 관측을 기존 snapshot 처리에 전달 → 묶음과 수집 감사 기록을 개인 폴더에 저장** 순서다. CLI는 AWS 연결 검사를 먼저 수행한 뒤 GitHub 조회를 시작한다.

`사건`은 조사 요청 하나, `증거`는 분석에 사용할 출처·관측 시각·상태가 있는 자료, `snapshot`은 한 번의 수집 결과를 해시로 고정한 증거 묶음이다. `artifact`는 CI가 GitHub에 업로드한 결과 ZIP, `UID`는 Kubernetes 객체가 재생성되면 바뀌는 식별자다. `stale`은 출처가 틀렸다는 뜻이 아니라 요청한 관측 시간 구간보다 오래된 자료라는 뜻이다. 감사 기록에는 검증 출처와 제외 이유가 남는다.

## 파일과 책임

| 파일 | 역할 |
|---|---|
| `src/ai_ops/lab_ci_history.py` | 실습 v2 엄격 계약, 고정 GitHub workflow/run/attempt/head 검증, 제한된 ZIP 읽기, 실패·롤백 관측의 일관성 검증 |
| `scripts/import_lab_ci_history.py` | 운영자 CLI. 실제 AWS ARN·endpoint·CA 대조 후 검증 이력과 현재 읽기 snapshot을 새 개인 JSON에 저장 |
| `src/ai_ops/live_collectors/snapshot.py` | 명시적으로 전달된 검증 이력만 기존 Kubernetes·Argo·Prometheus 수집에 연결 |
| `src/ai_ops/live_collectors/deployment.py` | 현재 실습 대상 UID·이미지·revision과 연결하고 실패/롤백을 각각 배포 증거로 추가 |
| `src/ai_ops/collection.py` | 기존 관측 창과 UTC 정규화 유지. 수집 감사에 run/attempt/artifact ID/ZIP 해시/source SHA 추가 |
| `tests/test_lab_ci_history.py` | 출처·변조·시간 모순·재생성·오래된 자료·일반 JSON 주입 거부 검사 |

기존 운영 앱 v1 조회 `ci_history.py`는 별개다. worker와 수집기 Pod에 GitHub 인증·추가 RBAC·쓰기 도구를 주지 않았다. 기본 수집은 여전히 `missing_history`이며 명시적 운영자 호출에만 가져오기가 활성화된다. 외부 JSON의 `verified=true` 같은 값을 신뢰하는 로더를 만들지 않았다. Python 프로세스 내부의 운영자 코드는 신뢰하며 메모리 객체를 악성 Python 코드에 대한 보안 경계로 주장하지 않는다.

## 검증과 증거 의미

고정 저장소의 `.github/workflows/ai-ops-lab-ci.yml`, main `workflow_dispatch`, 완료된 run의 최종 failure를 요구한다. S10은 원복에 성공해도 최초 실패를 보존해 최종 failure인 실험이므로 다른 CI 성공 기록을 같은 계약으로 해석하지 않는다. artifact 목록이 잘리거나 누락/중복/만료되면 거부한다. ZIP은 정해진 이름의 일반 파일 세 개만 메모리에서 크기 제한으로 읽고 압축을 디스크에 풀지 않는다.

두 관측의 저장소/workflow/run/attempt/source/cluster/namespace/Application, baseline/failure SHA가 일치해야 한다. 실패는 없는 이미지 태그·실제 ImagePull 사유·Ready 0, 복구는 원래 digest·Ready 1·generation 관측·Synced/Healthy/Succeeded와 baseline operation SHA를 요구한다. Application/Deployment UID가 서로 같고 실패 관측 → 복구 시작 → 복구 관측 → 최종 결과의 순서여야 한다.

실제 현재 수집은 `urlshortener` 클러스터의 practice 대상 `ai-ops-lab/lab-api`, 컨테이너 `api`, 선택자 `app=lab-api`, `argocd/lab-target`만 허용한다. 수집 중 Deployment가 변경되거나 과거 UID와 다르면 가져오기를 거부한다. 과거 artifact에는 Namespace UID가 없으므로 과거 Namespace UID를 새로 만들어 채우지 않는다. 같은 Deployment UID를 가진 실제 객체인지 확인한다.

실패는 `status=failed`, 복구는 `status=healthy_after_rollback` 배포 입력이 된다. 그러나 이는 배포/Ready/Argo 상태 확인이며 사용자 HTTP 경로·DB·Redis까지 모두 복구됐다는 주장은 아니다. 원본 `user_path_recovery_verified=false`, `evidence_import_enabled=false`도 바꾸지 않는다. 전자는 CI의 확인 범위, 후자는 기록 생성 당시의 기능 상태다.

관측 시각은 원본 UTC 그대로이며 화면 보고서는 기존 KST 표시를 따른다. 수집 당시의 900초 창을 벗어난 자료는 `stale/outside_window`, payload 없는 증거와 원래 시각·출처 감사로 남긴다. 오래된 자료를 현재 시각으로 재기록하거나 수집 창을 몰래 늘리지 않는다.

## 운영자 실행 방법

기존 잠금된 Python 환경에서 `PYTHONPATH=ai-ops/src`를 사용한다. `gh`는 저장소 실행/artifact 읽기 인증, `aws`는 해당 EKS DescribeCluster, Kubernetes 연결은 기존 `lab-investigator`의 읽기 전용 권한이 필요하다. 모델 API 키는 필요 없다.

1. 저장소 밖 현재 사용자 소유의 전용 폴더를 0700으로 준비하고 `AI_OPS_FILES_ROOT`에 등록한다. 입력/출력은 이 폴더에서 관리한다.
2. 현재 `lab-collector` ConfigMap의 bindings/runtime/metric-queries JSON을 가져온다. 등록 alias는 `url-shortener-b6-lab`이다.
3. 운영자용 runtime의 Kubernetes URL을 AWS가 반환한 현재 EKS HTTPS endpoint로, CA와 token을 전용 파일로 지정한다. CA는 AWS 인증서와 일치해야 한다. 토큰은 기존 `ai-ops-lab/lab-investigator`의 짧은 수명 토큰을 사용한다. 관리자 토큰·Secret 원문을 복사하지 않는다.
4. Prometheus는 localhost에 읽기 port-forward하고 runtime의 Prometheus URL을 해당 주소로 지정한다. 이 운영자 연결의 runtime도 `llm_enabled=false`이며 예산/수집 제한을 유지한다.
5. 아래 명령을 실행한다. 출력 경로는 아직 없는 새 `.json`이어야 한다.
6. 성공 후 `history`, `audit.entries`, `snapshot`을 함께 확인한다. 실패/롤백 두 entry가 `available`인지 `stale`인지 구분한다. 임시 토큰 파일과 port-forward를 정리한다.

```sh
PYTHONPATH=ai-ops/src python ai-ops/scripts/import_lab_ci_history.py \
  --run-id 39719907612 \
  --expected-head b0a667fd77484eb126985b3dd6ea811e7831fd7c \
  --bindings "$AI_OPS_FILES_ROOT/bindings.json" \
  --runtime "$AI_OPS_FILES_ROOT/runtime.json" \
  --metrics "$AI_OPS_FILES_ROOT/metric-queries.json" \
  --output "$AI_OPS_FILES_ROOT/import-result.json"
```

이 명령은 파일 검증·읽기 수집만 하며 장애 주입, Argo sync, Lease 변경, 모델 호출, 기존 보고서 교체를 하지 않는다. 실제 클러스터의 토큰·CA·원시 수집 결과를 Git에 넣지 않는다.

## 2026-10-22 실제 확인과 다음 단계

[검증 요약](assets/B6-s10-history-import-validation.json)에 run `39719907612`·attempt 1·artifact `12401091372`의 원본 해시 및 현재 대상과의 연결 결과를 기록했다. 실제 두 CI 관측은 원래 13:02:13/13:02:33 KST를 보존한 `stale/outside_window`였다. 현재 AWS endpoint/CA와 Application/Deployment UID가 일치했고, 수집 권한 확대·추가 장애 실험·외부 모델 호출은 0회다. 사용한 임시 토큰 파일과 port-forward는 종료했다.

S10-04의 명시적 운영자 검증·가져오기는 완료했다. worker의 자동 GitHub 조회나 이력 큐 입력은 이번 범위에 포함하지 않는다. **다음은 S10-05: 보고서가 최초 CI 실패, 자동 원복, 사용자 경로 복구 여부와 시각 누락을 구분해 설명하는지 확인**하는 작업이다. 과거 자료가 제외된 현재 snapshot을 최신 성공 증거로 사용해서는 안 된다. 우선 합성의 유효 시간 창에서 보고서 해석을 검증하고 실제 과거 자료는 stale/한계 설명을 확인한다. S10 전체·B-06 전체 완료와 C 진입은 계속 보류한다.
