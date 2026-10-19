# B-03 자체 관측·중지·복구

현재 상태: 2026-10-19 19:28:15 KST 자체 관측·중지/복구 실환경 검증 완료. PR #36·main e1b30b5의 새 이미지 등록 성공, ECR 검사 전체 0건과 코드 해시 일치를 확인했다. Helm revision 4에서 Prometheus up=1·진단 갱신·수동 수집/보고서 partial 각 1회 증가를 확인했다. AI StatefulSet 0→1 복구 후 동일 PVC·기존/신규 보고서 재조회와 해시·집계 보존, 앱 UID/generation 불변·Ready 2개를 확인했다. 외부 모델은 호출하지 않았다. [본문 없는 검증 기록](assets/B3-observability-validation.json).

## 무엇을 하는 단계인가

AI 조사 도구 자체가 준비되어 있는지, 수동 수집과 규칙 보고서 저장이 어떤 결과로 끝났는지를 관측한다. 현재 상주 프로세스는 진단 모드이며, 경보 수신·자동 조사 worker·외부 AI 호출은 시작하지 않는다.

동작 흐름은 **진단 또는 수동 수집 실행 → 고정 코드 JSON 로그 → 전용 SQLite 누적 집계 → 읽기 전용 `/metrics` → ServiceMonitor → Prometheus**다. 수동 수집은 collection과 report를 각각 기록한다. 모든 시각은 내부 UTC이며 사람이 보고서를 읽는 시각은 기존 KST 표시를 유지한다.

## 기록하는 것과 해석

| 지표·로그 | 의미 |
| --- | --- |
| `ai_ops_operation_duration_seconds_count{stage,result}` | 완료된 호출 횟수. stage는 diagnostic/collection/report, result는 success/partial/failure 세 값만 사용한다. |
| `ai_ops_operation_duration_seconds_sum{stage,result}` | 같은 호출의 누적 소요 시간(초). count와 함께 평균을 계산한다. |
| `ai_ops_diagnostic_last_success_timestamp_seconds` | 마지막 로컬 설정·런북·저장소 검사 성공의 Unix 시각. 진단 루프가 30초마다 갱신한다. |
| `up{namespace="ai-ops"}` | Prometheus가 메트릭 HTTP 응답을 읽었는지 나타낸다. 수집 성공·앱 정상·원인 확정을 뜻하지 않는다. |
| JSON stderr 로그 | UTC timestamp, 허용된 event/stage/result, duration_seconds만 기록한다. 시작·종료·작업 완료·관측 실패를 구분한다. |

collection은 감사 상태 complete→success, partial→partial, unavailable→failure로 바꾼다. report는 저장 결과 completed→success, 부분 결과→partial이며 예외·취소는 failure다. 예외 원문은 관측 로그에 남기지 않는다. 같은 request ID의 재조회성 호출도 호출 횟수에 포함하므로, 이 숫자를 고유 사건 수로 해석하지 않는다. 입력 검증에서 차단된 명령은 조사 호출로 세지 않는다.

집계는 `/data/incidents/.telemetry.sqlite3`의 고정 9개 행으로 유지한다. 저장 폴더는 0700, 파일은 0600이며 링크·공유 쓰기 파일을 거부한다. 질문·run ID·로그 본문·토큰·원인 후보를 지표 label로 사용하지 않는다. 메트릭은 19개 값만 노출한다. 이 파일은 보고서가 아니며, Pod 재시작 뒤에도 같은 PVC의 집계가 이어진다. 삭제 후 재생성하면 누적 counter가 초기화되므로 Prometheus `rate`/`increase`로 해석한다.

지표 저장 실패는 `telemetry_failed`로 알리며 수동 조사 원래 결과를 바꾸지 않는다. HTTP에서 읽기 실패 시 503을 반환한다. 진단 프로세스의 집계 실패는 비정상 종료한다. 강제 종료·노드 장애로 완료되지 못한 작업의 횟수까지 보장하지 않으며, 작업 재시작 복구는 B-04 큐에서 다룬다. 예전 수집 호출은 소급 집계하지 않는다.

## 배포와 통신 경계

`observability.enabled` 기본값은 false다. 활성화할 때만 `--metrics`, TCP 9091, metrics Service, ServiceMonitor, 별도 ingress NetworkPolicy를 추가한다. HTTP는 GET `/metrics`만 제공하며 중지·조사 실행·설정 변경 API는 없다.

현재 Prometheus의 ServiceMonitor namespace selector와 label selector는 `{}`다. ServiceMonitor는 ai-ops namespace의 metrics 전용 Service 하나만 선택하고 30초 간격·5초 timeout·sampleLimit 50·targetLimit 1을 사용한다. 수신 정책은 monitoring namespace **및** 기존 Prometheus Pod labels를 함께 만족하는 발신자의 TCP 9091만 허용한다. 기존 default deny·egress·읽기 RBAC·외부 모델 차단은 유지한다. 이 기능은 Secret·쓰기·exec 권한을 AI 계정에 추가하지 않는다.

새 이미지 등록 artifact의 digest·소스 SHA·ECR 결과를 확인한 뒤 적용한다. 이전 이미지에는 `--metrics`가 없으므로 새 overlay를 적용하면 안 된다.

```sh
helm upgrade ai-ops ai-ops/chart --namespace ai-ops \
  -f ai-ops/config/b3-values.preflight.yaml \
  -f ai-ops/config/b3-values.observability.yaml \
  --set-string image.digest=sha256:검증한_새_digest \
  --wait --timeout 180s
kubectl -n ai-ops rollout status statefulset/ai-ops --timeout=180s
kubectl -n ai-ops get service,servicemonitor,networkpolicy
kubectl -n ai-ops logs ai-ops-0 --tail=10
```

Prometheus에서 `up{namespace="ai-ops"}`, 위 count/sum, 마지막 진단 성공 시각을 확인한다. 실제 수집 CLI 한 번의 전후 collection/report 증가와 partial 구분도 대조한다. 일반 stdout의 보고서 내용은 공개 로그로 복사하지 않는다. 메트릭 응답 성공만으로 일반 Pod의 접근 차단까지 실증했다고 표시하지 않는다.

## 안전한 중지·복구

수동 Helm 관리인 현재 배포 기준이다. 이후 Argo 자동 동기화나 worker를 도입하면 해당 제어기를 먼저 멈추고 진행 중인 작업을 정리하는 절차를 다시 정의해야 한다.

1. 적용 중인 Helm revision·실제 image digest·PVC UID와 PV 이름을 개인 임시 위치에 기록한다. 기존 보고서의 파일 해시와 현재 집계 값만 따로 기록한다. 보고서 본문은 반출하지 않는다.
2. **AI StatefulSet 하나만** 0개로 줄인다. `helm uninstall`, `enabled=false` 변경, PVC 삭제, 앱 Deployment 변경은 중지 명령으로 사용하지 않는다.
3. Pod가 사라졌는지 확인하고 PVC UID·PV 이름이 같은지 확인한다. 이때 수동 조사도 실행할 수 없고, 새 자료를 수집하지 않는다. EBS 비용과 기존 저장 자료는 남는다.
4. 같은 StatefulSet을 1개로 복원하고 Ready를 기다린다. PVC·이미지·설정이 원래 값인지, 저장 보고서의 재조회·해시가 같은지, 집계가 이어지고 Prometheus가 다시 읽는지 확인한다. 전후 앱 UID/generation·Ready 수도 대조한다.

```sh
kubectl -n ai-ops get statefulset ai-ops
kubectl -n ai-ops get pvc data-ai-ops-0
helm -n ai-ops history ai-ops
kubectl -n ai-ops scale statefulset ai-ops --replicas=0
kubectl -n ai-ops wait --for=delete pod/ai-ops-0 --timeout=120s
kubectl -n ai-ops get pvc data-ai-ops-0
kubectl -n ai-ops scale statefulset ai-ops --replicas=1
kubectl -n ai-ops rollout status statefulset/ai-ops --timeout=180s
```

새 버전으로 Ready가 되지 않으면 직전 정상 Helm revision으로 `helm rollback ai-ops <검증한_revision> -n ai-ops --wait --timeout 180s`를 실행한다. PVC를 삭제해 해결하지 않는다. 이전 진단 이미지로 돌아가면 메트릭 기능도 사라지는 것이 정상이다. 데이터 복원·다른 AZ 재해 복구·큐의 진행 중 작업 재개는 이번 검증 범위가 아니다.

## 확인할 구현 파일

- `src/ai_ops/observability.py`: 고정 로그·영속 집계·읽기 전용 HTTP.
- `src/ai_ops/deployment_check.py`: 진단 heartbeat·종료 신호 처리.
- `src/ai_ops/cli.py`: 수동 collection/report 단계 계측.
- `chart/templates/observability.yaml`, `config/b3-values.observability.yaml`: 명시적 Prometheus 연결.
- `scripts/check_deployment.py`: 권한·포트·selector 범위의 정적 검증.
- `tests/test_observability.py`, `tests/test_deployment_check.py`, `tests/test_b_investigation.py`: 영속성·유출 방지·HTTP 경계·중지·부분 결과 검증.

Prometheus 노출 형식과 label 기준은 [공식 계측 가이드](https://prometheus.io/docs/practices/instrumentation/), ServiceMonitor 필드는 [Prometheus Operator API](https://prometheus-operator.dev/docs/api-reference/api/)를 따른다.

## 실제 적용·검증 결과

검증 시각은 **2026-10-19 19:28:15 KST**다. 로컬 Python 162건·원격 CI 5개 통과 후 사용자 환경 승인을 받아 [등록 실행 39235754071](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39235754071)을 완료했다.

| 항목 | 확인 결과 |
| --- | --- |
| 소스 | main `e1b30b510e49774ce25d54c79abc7ea21f221ec7` |
| 이미지 | `716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener-practice-ai-ops@sha256:8f716e93ce4d51fa5c310840d9084533b8265565639182c220f4e4cc55baff76` |
| identity | artifact·ECR manifest config/image ID·소스 해시·실제 Pod의 관측/진단/CLI 코드 해시 일치 |
| ECR 검사 | COMPLETE, 전체 0건 |
| 배포 | server dry-run 13개 리소스 통과, Helm revision 4 deployed·Pod Ready |
| Prometheus | `/metrics` 19개 값, `up=1`, 마지막 진단 성공 시각 갱신 |
| 수집·보고서 | 일반 CLI 한 번 실행 후 collection/report partial counter 각각 0→1. 종료 코드 1은 부분 결과 저장을 의미하며 저장 실패가 아님 |
| 실제 자료 | available 7·invalid 2·missing 7·stale 2. 규칙 부분 보고서이며 원인 확정·앱 정상 판정을 하지 않음 |
| 수신 제한 | Prometheus 수집 성공과 같은 namespace의 Prometheus label 없는 검사 Pod의 직접 TCP 9091 timeout을 대조. 임시 Pod 삭제 완료 |
| 중지 | AI StatefulSet만 0개로 축소·Pod 삭제 확인, PVC UID·PV 유지. 앱 UID/generation 불변·Ready 2개 유지 |
| 복구 | 1개로 복원·새 Pod Ready, 동일 이미지/PVC/PV. 기존/신규 보고서 show·해시 불변, 수집/보고서 집계 보존·진단 갱신·Prometheus up=1 재확인 |
| 모델·권한 | 키·모델·예산 경로 호출 금지 검사 통과. 기존 읽기 RBAC·모델 차단 유지 |

신규 검증 실행은 `b2-live-20261019102600-observability`이며 정제된 입력·보고서·감사는 암호화 PVC에 보관한다. 삭제 기한은 **2026-10-26 19:26:01 KST**, 책임자는 운영자이며 자동 삭제는 아직 없다. 집계 DB에는 이 본문이나 사건 ID가 들어가지 않는다.

이 결과는 진단·수동 CLI와 정상적인 0→1 복구의 검증이다. 강제 종료·노드 장애·다른 AZ 복구·큐의 진행 중 작업 재개·Helm rollback 실패 주입은 실행하지 않았다. 전체 B-03 완료로 확대하지 않으며, 다음 미착수 구현은 B-04 영속 큐·lease·중복/재시도·worker 복구다.
