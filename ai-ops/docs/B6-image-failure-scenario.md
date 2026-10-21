# B-06 S3 실습 이미지 취득 실패와 원래 digest 복구

상태: **이미지 부재·실제 취득 실패·원래 digest 복구는 확인, 보고서 설명은 needs_review**. [검증 요약](assets/B6-image-failure-validation.json), [실측 결과](assets/B6-s3-image-failure.json), [조사 전 관측](assets/B6-s3-image-failure.ground-truth.json), [변경 전 복구 계획](assets/B6-s3-image-failure.recovery-plan.json)을 함께 확인한다.

이 작업은 **실습 앱에 존재하지 않는 이미지 태그를 적용해 실제 Pod 실패를 관측하고, 수집·규칙 보고서와 대조한 뒤 원래 이미지로 복구하는 단계**다. 자동 CI 롤백·GitOps 복구 구현은 S10과 C단계의 별도 범위다. 운영 앱·RDS·Redis·AI worker 이미지는 변경하지 않는다.

## 처리 흐름

단일 실행 잠금·이전 지표 창 확인 → 실습 소유·Pod Ready·읽기 인가·scrape 준비 검사 → 정상 앱 digest·UID 기록 → 같은 ECR 저장소의 고유 태그 부재 확인 → 별도 복구 계획 저장 → 실습 앱 이미지 한 필드 변경 → 새 ReplicaSet 소유 Pod의 실제 취득 실패·Ready 0 확인 → 주입 관측 저장 → 중립 질문의 수집·규칙 보고서 → finally에서 원래 digest·Ready·HTTP·Redis 확인 → 운영 식별자 불변 확인 순서다.

`src/ai_ops/b6_scenarios.py`에 이미지 변경·실패 대기·복구와 S3 실행 경로를 추가했다. `scripts/run_b6_scenario.py`는 기존 실행 잠금·이전 305초 창·실패 후 차단을 재사용하고 변경 전 `.recovery-plan.json`을 새로 보관한다. `tests/test_b6_image_scenario.py`는 태그 부재·변경 경합·실패/원복 경로·보고서 판정 경계를 검사한다.

## 변경 전 확인과 제한

- 정상 이미지가 승인한 앱 ECR의 `@sha256:...` 형식인지 검사한다. 복구값을 변경 전에 파일로 저장하지 못하면 배포를 바꾸지 않는다.
- `b6-missing-<고유 실행 ID>` 태그를 ECR batch-get-image로 조회한다. images=[]와 해당 태그의 ImageNotFound가 모두 있어야 한다. 권한 오류·통신 오류·빈 failures는 이미지 부재가 아니다.
- `ai-ops-lab/lab-api`의 목적 label·Deployment UID·컨테이너 이름 api·replicas=1·Recreate 전략을 검사한다. UID·resourceVersion·기존 이미지 JSON Patch test가 통과할 때 이미지 한 필드만 교체한다. 다른 운영자의 변경이나 새 UID는 덮어쓰지 않는다.
- 실습은 Recreate·1개 Pod이므로 잘못된 이미지 적용 중 실습 API가 중단된다. 이 가용성 저하는 계획한 실습이며 운영 서비스 중단이 아니다. HTTP 기준선은 변경 전에, 복구 응답은 변경 후 검사한다. 실행 중 실패 Pod에 exec하지 않는다.

## 실패 관측과 보고서 판정

최대 120초 동안 고정 Deployment의 ReplicaSet 소유 관계가 확인된 새 이미지 Pod만 조회한다. 실제 대기 reason이 ErrImagePull 또는 ImagePullBackOff이고 Ready가 false, Deployment Ready 0이며 해당 Pod UID에 연결된 Failed·ErrImagePull·ImagePullBackOff 이벤트가 있는지 검사한다. 이벤트 자유 문장·오류 원문을 로컬 파일에 출력하지 않는다.

ECR 부재는 운영자 정답 기록이다. 조사에는 해당 결과나 시나리오 ID·주입 명령을 주지 않고 기존 중립 질문만 사용한다. 관측 시각·UID·이벤트 reason을 `.ground-truth.json`에 먼저 저장하고 실습 수집기의 실제 Kubernetes·Prometheus 입력으로 보고서를 만든다.

보고서가 suspected_cause를 내고 실제 수집한 이미지 관련 실패 이벤트를 원인 후보 근거로 인용하면 image_failure_supported로 기록한다. 자료에 실패 흔적이 있어도 보고서가 이를 설명하지 못하면 needs_review다. 실제 주입·원복 성공을 보고서 진단 성공과 합치지 않는다. 없는 Argo Application·CI 이력을 가짜 progressing·rollback으로 만들지 않는다. 일반 Failed·BackOff만으로는 이미지 원인 근거로 채점하지 않고, 유한한 ErrImagePull/ImagePullBackOff 표식이나 해당 고정 reason도 확인한다.

실습 앱 중단 중 up=0 또는 품질 표본 부족은 정상적으로 지표 누락이다. 복구 확인에서는 Pod/HTTP/Redis와 원래 digest를 직접 확인하며 지난 5분 scrape_health가 즉시 정상이어야 한다고 요구하지 않는다. 다음 시나리오의 사전 검사는 기존 305초 창과 실제 scrape 품질을 다시 요구한다.

## 실행과 중단 복구

```bash
export AI_OPS_FILES_ROOT=/private/tmp/ai-b6-lab-20261021
export PYTHONPATH=ai-ops/src
python ai-ops/scripts/run_b6_scenario.py --scenario S3 --state-dir "$AI_OPS_FILES_ROOT/scenarios" --output "$AI_OPS_FILES_ROOT/s3-image-failure.json"
```

같은 실습에는 기존 state-dir를 사용하고 출력 이름을 새로 지정한다. `.recovery-plan.json`은 원래 Deployment UID·digest와 시험 태그를 보관한다. `.ground-truth.json`은 조사 전 관측이며 최종 상태는 아니다. 최종 파일의 status·report_check·cleanup을 함께 확인한다.

변경 명령의 응답을 잃거나 수집/실패 확인이 실패해도 finally가 원래 digest 복원을 시도한다. 현재 이미지가 이미 원래 digest이면 중복 patch 없이 rollout을 확인한다. 다른 UID나 예상 밖의 새 이미지이면 임의로 덮어쓰지 않고 recovery_failed로 남긴다. 원복 후 /readyz·/items 200과 Redis PING, 실습 각 Deployment UID·원래 이미지, 운영 앱/AI StatefulSet/PVC UID/generation을 확인한다. 실습 앱 generation 증가는 이미지 변경과 복구에 따른 정상 결과이며 다른 자원의 generation 변경은 허용하지 않는다.

강제 프로세스 종료·컴퓨터 종료는 finally를 보장하지 않는다. active 제어 기록과 `.recovery-plan.json`을 보존한 채 해당 namespace·UID·현재 이미지부터 대조하고 원래 digest 복구·rollout·HTTP를 확인한다. 제어 파일 삭제나 새로운 폴더로 차단을 우회하지 않는다. 실습 앱 Pod 교체로 앱 `/tmp` 소유 기록이 사라질 수 있어 S3는 아이템을 생성하지 않는다. 단일 PostgreSQL·Redis·수집기 Pod와 그 자료는 이 시험에서 재시작하지 않는다.

최종 전체 Python 시험 283개(이미지 관련 신규 9개)가 통과했다. 제품 보고서의 진단 공백은 시험 성공과 별도이며 needs_review를 유지한다.

이 단계는 앱의 수동 이미지 복구이며 에이전트에게 patch 권한을 주지 않는다. 라이브 모델 호출·사용자 승인 기반 자동 복구·CI 자동 롤백 성공으로 표현하지 않는다.

## 2026-10-21 실제 결과와 진단 공백

16:06:04~16:06:42 KST 약 38.23초의 단일 실행이다. ECR은 고유 시험 태그의 ImageNotFound를 반환했다. 정상 digest·UID를 복구 계획으로 고정한 뒤 실습 앱만 변경했고, 새 Pod는 ErrImagePull·Deployment Ready 0, 관련 Failed·BackOff·Pulling 이벤트를 보였다. 이 시간은 실패 주입·수집·원복의 운영자 실행 시간이며 MTTR이 아니다.

수집된 자료는 available 13·missing 8·invalid 1이고, Pending/Ready false Pod와 Failed 이벤트가 저장됐다. 규칙 v2는 사실 3개·가설 0개·partial/insufficient_evidence/unknown을 반환해 설명 조건은 needs_review였다. Failed 이벤트 두 개에는 정제된 이미지 오류 표식이 존재하지만, 기존 규칙은 reason 자체가 ImagePullBackOff이고 배포 상태가 progressing인 조합을 요구한다. 이 실습은 Argo가 없어 deployment-object.status=unknown이다. 실제 증거는 있었으나 현재 규칙의 형태 조건으로 원인 설명을 만들지 못했다.

Pod가 실행되지 않아 로그는 upstream_error이고, 이벤트 한 개는 invalid_payload로 남았다. 해당 무효 이벤트의 세부 원인은 이번에 확정하지 않았다. 짧은 중단 직후에는 Prometheus의 마지막 up/과거 counter가 남을 수 있어 당시 지표가 유효하게 읽혔다고 현재 앱 정상으로 해석하지 않는다. 실제 Ready 0을 별도로 확인했고 보고서는 healthy로 판정하지 않았다.

finally에서 원래 digest 복구·Ready 1·/readyz와 /items 200·Redis PING을 확인했다. 실습 4개 Deployment 모두 Ready이며 운영 앱 Ready 2개·AI 2/2와 worker probe도 정상이다. 운영 앱·AI StatefulSet·PVC 식별자는 같았다. 수동 앱 HTTP 4회, 테스트 아이템 생성 0회, 외부 모델 호출 0회다.

실측 뒤 평가기에서 일반 Failed만 인용한 무관한 원인을 이미지 진단 성공으로 채점하지 못하도록 표식 조건을 강화하고 9개 이미지 시험을 통과했다. 이미 저장한 실측 결과는 덮어쓰지 않았고 장애 주입은 반복하지 않았다. 다음은 새 규칙 버전에서 native Failed 이벤트의 유한 표식과 Pod 소유·시각을 대조하는 설명을 보완하고, 기존 보고서 호환성을 유지하며 이 저장 증거로 먼저 검증하는 작업이다. GPT-6 SOL high를 추천한다. S3 보고서 품질 통과·B-06 완료·C 진입은 보류한다.

## 후속 v3 후보 비교

[규칙 v3와 동일 저장 입력 검증](B6-image-rules-v3.md)에서 native Failed 표식·Pod 소유·Deployment 이미지·근접 시각을 대조했다. 같은 입력에서 후보 0→1·사실 3→4, partial/unknown 유지와 기존 v2 6건/18개 파일 해시 보존을 확인했다. 전체 289개 시험이 통과했다. 후보 소스 비교이며 수정 이미지 배포·새 실환경 v3 보고서 검증은 아직 남아 있다. 위 최초 needs_review 기록은 그대로 유지한다.
