# B-04 worker 배포·관측·재시작 복구

현재 상태: **2026-10-20 16:01:23 KST worker 배포·실제 Pod 교체 복구 검증 완료**다. PR #40·main과 원격 Python 207건·Helm·이미지 실행 검사를 통과했다. 수동 접수만 처리하며 경보 수신·외부 모델·운영 변경·예약 보관 정리는 비활성이다.

## 구현한 기능과 흐름

운영자가 수동으로 요청을 접수하면 PVC의 SQLite 큐에 저장한다. worker 한 개가 대기 요청을 가져와 등록된 Kubernetes·Prometheus·Argo CD 자료를 읽고, 정제한 증거를 중간 결과로 고정한 뒤 규칙 보고서를 작성한다. 실제 AI 모델 호출·경보 수신·운영 리소스 변경은 활성화하지 않는다.

흐름은 **수동 접수 → 영속 큐 → 단일 worker 잠금 → 만료 시도 조정 → 읽기 수집 → 고정 증거 저장 → 규칙 보고서 검증/저장 → 큐 완료 참조**다. 중단 지점에 따라 새 시도로 재시도하거나, 이미 저장된 보고서를 확인해 완료 상태만 복구한다. 입력과 결과가 불명확하면 `needs_review`로 보류한다.

## 배포 모드와 상태 검사

- chart 기본 `enabled=false`, `mode=diagnostics`를 유지한다. 검증된 새 이미지 digest와 현재 클러스터용 private values에 `config/b4-values.worker.yaml`을 추가해야 worker를 실행한다.
- `python -m ai_ops.worker_runtime`이 기본 경로 `/etc/ai-ops`, `/data/incidents`에서 실행한다. 외부 모델 키를 읽거나 관리 HTTP API를 열지 않는다.
- `--metrics`를 명시한 경우에만 기존 9091 `/metrics`를 사용한다. NetworkPolicy는 지정 Prometheus Pod만 허용한다. 상태·질문·결과 조회 및 접수는 Kubernetes 인증을 통과한 운영자의 `kubectl exec`와 기존 CLI로 수행한다.
- 기존 조사용 ServiceAccount에는 `pods/exec`·Secret 읽기·앱 변경 권한이 없다. 이 구성은 별도 웹 로그인·검토자별 권한 UI를 제공하지 않는다. 운영자에게 namespace exec 권한을 부여하면 해당 Pod의 자료 접근도 가능하므로 그 권한을 별도로 관리한다.
- 연속 처리 중에는 유휴 시간에도 `worker.lock`을 유지한다. 같은 저장소의 두 번째 worker와 보관 정리를 차단한다. `queue-work` 단발 실행은 기존처럼 해당 호출 동안 잠금을 잡는다.
- 실제 처리 루프가 진행됐을 때만 최대 10초 간격으로 0600 `queue/worker-health.json`을 기록한다. 별도 생존 스레드가 멈춘 작업을 정상으로 표시하지 않는다.
- `--probe`는 폴더/파일 권한, PID 존재, worker 잠금, monotonic 기준 최근 120초 진행을 검사한다. 큐 DB를 만들거나 schema를 전환하지 않는다. 처리 루프가 멈추거나 worker가 종료되면 실패한다. 이는 API 연결이나 보고서 내용의 정확성을 보증하는 검사가 아니다.
- startup/readiness/liveness가 worker 검사를 사용한다. liveness는 30초 간격 세 번 연속 실패하면 컨테이너를 재시작한다. 정상 수집 예산은 최대 60초이며 종료 유예는 90초다. SIGTERM은 새 요청을 시작하지 않고 현재 수집의 안전 지점에서 멈춘다. 강제 종료 시 lease 만료를 기다려 복구한다.

## 고정 지표

기존 collection/report 호출 집계는 유지한다. worker의 준비 상태는 진단 모드의 `ai_ops_diagnostic_last_success_timestamp_seconds` 대신 worker 진행 지표와 scrape 성공을 사용한다.

| 지표 | 의미 |
|---|---|
| `ai_ops_queue_jobs{state="…"}` | queued/collecting/analyzing/completed/partial/failed/cancelled/needs_review의 현재 건수. 8개 고정 label만 사용 |
| `ai_ops_queue_oldest_waiting_age_seconds` | 가장 오래된 queued 요청의 접수 후 경과 시간. 재시도 대기도 포함 |
| `ai_ops_queue_expired_leases` | 처리 중이지만 lease가 만료된 요청 수 |
| `ai_ops_queue_retained_attempts` | 현재 보관된 시도 수. 보관 정리 시 감소하므로 gauge |
| `ai_ops_worker_last_progress_timestamp_seconds` | 현재 worker 루프가 마지막으로 진행한 Unix 시각 |

질문·job ID·대상명·원문 오류·로그 본문은 label과 상태 로그에 넣지 않는다. SQLite는 읽기 전용 WAL 스냅샷으로 조회한다. 현재 총 31개 sample이며 기존 ServiceMonitor의 상한 50을 넘지 않는다. 진행 검사/자료 읽기가 실패하면 `/metrics`는 503을 반환한다. Prometheus의 `up`과 `time() - ai_ops_worker_last_progress_timestamp_seconds`, 대기/실패 건수를 함께 본다. 자동 알림 규칙은 이번에 추가하지 않았다.

## 배포 및 보관 정리 절차

이미지 등록 artifact의 소스 SHA·digest·ECR 검사 결과를 검증하고 현재 연결값을 새로 생성한다. [재설치 가이드](../../docs/runbooks/ai-ops-cluster-lifecycle.md)의 진단 설치와 같은 차트·private values를 사용하되 마지막 overlay만 `ai-ops/config/b4-values.worker.yaml`로 지정한다. 임의의 과거 digest/IP를 복사하지 않는다.

배포 직후 worker probe·Prometheus `up=1`·고정 지표를 확인한다. 이어 수동 접수→보고서 저장을 확인하고 AI Pod만 재시작해 동일 PVC·완료 결과·중복 키·중단 작업 복구를 확인한다. 앱 Pod와 Deployment는 변경하지 않는다. 외부 모델 호출 없이 규칙 분석을 사용한다.

worker가 계속 잠금을 잡으므로 보관표 조회/삭제는 먼저 **같은 새 이미지·PVC를 유지한 채 Helm `mode=diagnostics`로 전환**하여 worker 종료를 확인한 뒤 수행한다. 그 Pod에서 `queue-retention` 미리보기를 검토하고 승인한 만료 작업에만 `--apply`한다. 이후 `mode=worker`로 복귀한다. PVC 삭제·직접 DB 변경·잠금 파일 삭제로 충돌을 우회하지 않는다. 예약 정리는 아직 연결하지 않았다.

## 로컬 검증과 남은 조건

기존 큐/보관 시험에 신규 7건을 추가해 전체 Python 207건을 통과했다. 고정 지표의 원문/식별자 미포함, 읽기 조회의 큐 불변, 만료 lease, 오래되거나 미래인 heartbeat·비정상 PID·링크 거부, 유휴 중 단일 worker 잠금, 정상 종료 후 snapshot 재사용을 검증했다. 별도 실제 프로세스를 SIGTERM으로 종료하고 probe 실패·미존재 저장소 비생성을 확인했다. Helm은 진단/worker와 관측 유무, 90초 종료 유예·worker probe, 기존 최소 RBAC/통신/비활성 모델 경계를 검증했다.

실제 이미지의 부팅·PVC 재연결·Pod 중단 복구는 아래 실행 기록에서 확인했다. B-04 전체 완료나 자동 경보 조사 시작으로 표시하지 않는다. B-05 경보 접수와 예약 보관 정리는 후속이다.

## 원격 반영 기록

[PR #40](https://github.com/f-lab-edu/F-Lab-DevOps/pull/40)은 [AI CI](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39382810627)의 contracts/image 통과 후 main `c17ac3dd13d43532c7f78a99fdcd773d0b2d4c47`에 반영했다. [이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39382980228)은 사용자 승인 후 성공했다. 새 이미지의 소스·ECR manifest/digest와 COMPLETE·지적 0건을 검증한 뒤 기존 PVC에 Helm revision 2 worker 모드를 배포했다.

## 실제 배포·Pod 중단 검증 결과 (2026-10-20 16:01:23 KST)

새 digest는 `sha256:10ca94de84949233fbf5521b32a01009ad7e5a0adc4cff19355493e102335180`다. worker probe, Prometheus `up=1`, 고정 지표 31개와 실제 루프 진행을 확인했다.

1. 수동 실제 읽기 조사 한 건을 규칙 `partial` 보고서로 저장하고 파일 다섯 개의 해시를 기록했다. 자료 부족을 완전한 원인 규명으로 표시하지 않았다.
2. 컨테이너 안에서 PID 1에 보낸 첫 SIGKILL은 Linux PID namespace의 init 보호로 종료를 일으키지 않았다. restartCount=0을 확인했으며 이를 복구 성공으로 계산하지 않았다.
3. 다음 요청에서는 운영자 검사 프로세스가 보고서 저장 파일 잠금만 일시 보유해, 실제 수집을 마친 checkpoint 이후 `analyzing` 지점을 고정했다. 이 상태를 확인한 뒤 AI Pod만 `--grace-period=0 --force`로 교체했다. 검사 프로세스의 exit 137과 새 Pod UID를 확인했다. PVC와 앱은 삭제하지 않았다. 이 조작은 검증용이며 정상 운영 중단 절차는 아니다.
4. 새 Pod에서 `analyzing(1) → queued(1) → partial(2)`를 확인했다. 첫 시도는 `failed/interrupted`, 두 번째 시도는 부모 generation 1을 가진 부분 완료다.
5. checkpoint 파일 해시와 보고서 bundle hash가 일치했다. 총 수집 호출은 두 요청의 최초 수집 2회뿐이며, 복구 시 재수집하지 않았다. 보고서 호출은 두 결과 2회다. 기존 완료 보고서 다섯 파일과 완료 상태도 불변이었다.
6. 기존 요청 키 재접수는 같은 job ID와 `duplicate=true`를 반환했다. 동일 PVC UID/PV, 앱 Deployment UID/generation·Pod UID와 Ready 2개를 확인했다.
7. 조사 ServiceAccount의 `pods/exec` 생성·Secret 읽기·Deployment patch는 모두 거부됐다. 운영 자료 외부 모델 차단과 `llm_enabled=false`를 확인했고 외부 모델 호출은 0회다.

[본문 없는 검증 기록](assets/B4-worker-validation.json)에 상태·식별자·해시·고정 지표를 남겼다. 운영 질문·증거·보고서 원문은 PVC 밖으로 복사하지 않았다. 일회성 검사 marker는 제거했고 실제 조사 자료에는 7일 정책을 적용한다. **예약 보관 정리와 B-05 경보 연동은 후속이며, B-04 전체 완료를 선언하지 않는다.**
