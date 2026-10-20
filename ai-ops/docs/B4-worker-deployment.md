# B-04 worker 배포·관측·재시작 복구

현재 상태: **로컬 구현·Python 207건·Helm 정적 검사 통과. 원격 CI·새 이미지 등록·실제 worker 배포/Pod 복구 검증은 대기**다. 기존 클러스터는 아직 진단 모드다.

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

실제 이미지의 부팅·PVC 재연결·Pod 중단 복구는 로컬 시험과 다르므로 별도 실행 기록이 필요하다. B-04 전체 완료나 자동 경보 조사 시작으로 표시하지 않는다. B-05 경보 접수와 예약 보관 정리는 후속이다.
