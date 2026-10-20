# B-04 영속 큐·수동 worker·재시작 복구

현재 상태: **첫 묶음 큐·복구와 두 번째 묶음 보관·연계 삭제 로컬 검증 완료, 실제 worker 배포 전**이다. B-03에서 배포한 Pod는 계속 진단 모드다. 경보 HTTP 수신·자동 조사 worker·외부 모델·앱 변경은 활성화하지 않았다.

## 어떤 기능인가

운영자가 조사 요청을 먼저 저장하고, worker가 나중에 한 건씩 처리하는 기능이다. CLI 실행 중에만 요청이 메모리에 있다가 사라지는 문제와 중복 전달 시 새 조사가 반복되는 문제를 줄인다.

처리 흐름은 다음과 같다.

1. `queue-submit`이 등록 대상·고정 설정 해시·route/operation을 검사하고 질문을 마스킹한다.
2. 요청 키와 내용을 비교해 동일한 요청은 기존 job ID를 반환한다. 같은 키에 내용이 다르면 거부한다. SQLite commit이 끝난 뒤에 접수 결과를 출력한다.
3. `queue-work`가 저장소 전체의 단일 worker 잠금을 얻고 한 작업을 claim한다. 작업 소유권인 lease는 60초, 갱신은 10초 간격이다.
4. 기존 B-02 수집기로 읽은 자료를 검증하고 `queue/job-….json`에 정제 snapshot을 고정한다. 이후 재시도는 이 자료를 재사용한다. 최신 상황을 다시 조사하려면 새 요청 키를 사용한다.
5. 기존 A/B IncidentStore로 규칙 보고서를 검증·저장한다. 큐는 보고서 본문을 복제하지 않고 incident/run ID와 bundle hash를 연결한다.
6. `queue-show`로 상태를 보고, 완료/부분 결과의 incident/run ID를 기존 `show`에 넘겨 보고서를 읽는다. 표시 시각은 기존 KST, 내부 기록은 UTC다.
7. 프로세스가 종료되면 다음 worker가 만료된 시도를 조정한다. 완료 보고서가 이미 있으면 해시·대상·질문·감사·파일을 확인해 큐만 마무리한다. 완료되지 못했다면 새 attempt/request ID로 제한적으로 재시도한다.

## 파일과 책임

| 파일·저장 위치 | 책임 |
| --- | --- |
| `src/ai_ops/job_queue.py` | 접수·중복·claim·취소·lease·generation·시도 이력과 상태 전이 |
| `src/ai_ops/worker.py` | 만료 시도 조정·단일 작업 처리·heartbeat·정제 입력 고정·규칙 보고서 연결 |
| `src/ai_ops/cli.py` | `queue-submit`, `queue-work`, `queue-show`, `queue-cancel` 명령 |
| `src/ai_ops/incidents.py` | 기존 input/report/audit 저장과 요청별 완료 결과 검증 조회. 큐의 대기 상태는 맡지 않음 |
| `<store>/queue/jobs.sqlite3` | schema v2의 jobs/episodes/attempts/purges. WAL·synchronous FULL·busy timeout 5초 |
| `<store>/queue/job-….json` | 고정한 정제 증거와 수집 감사. 원시 API 응답이나 로그 원문을 저장하지 않음 |
| `<store>/queue/worker.lock` | OS 프로세스 잠금. 종료되면 해제되며 동일 저장소의 worker 실행을 한 개로 제한 |
| `<store>/inc-…/run-…/` | 기존 보고서·입력·감사 파일. 큐와 별도 transaction이므로 재시작 조정이 필요 |
| `tests/test_job_queue.py` | 중복·포화·취소·retry·lease·다른 프로세스 잠금·강제 종료 후 복구 검증 |

저장소와 queue 폴더는 0700, DB·snapshot·잠금 파일은 사용자 전용 0600이다. DB 및 SQLite 부속 파일의 링크·다른 소유자·공유 권한을 거부한다. `user_version=2`를 생성하며 v1은 기존 자료를 보존한 채 삭제 재개 장부를 transaction으로 추가한다. 알 수 없는 버전은 거부하며 v1만 지원하는 이전 worker와 혼용하지 않는다. 향후 schema 변경에는 명시적 migration과 별도 복구 시험이 필요하다.

## 중복·상태·재시도

- **job**은 접수한 조사 요청 한 건이다. **episode**는 같은 수동 요청 키의 중복을 묶는 기록이다. 현재는 manual 요청 키만 지원하며 Alertmanager의 alertname/startsAt/fingerprint 해석은 B-05에서 연결한다.
- 원래 요청 키는 hash로, 원래 질문의 내용 비교는 개인 저장소 키를 사용한 HMAC으로 처리한다. DB에는 마스킹된 질문만 저장하고 일반 상태 조회·지표에는 질문과 요청 키를 넣지 않는다.
- 상태는 `queued → collecting → analyzing → completed/partial`이며, 실패·취소·자료 검증 문제는 `failed/cancelled/needs_review`로 구분한다. 부분 보고서도 저장 성공이며 앱 정상이나 원인 확정을 뜻하지 않는다.
- 대기/진행 중 작업을 합쳐 최대 100개까지 접수해 재시도 시 대기가 100개를 넘지 않게 한다. 동시 worker는 1개, 시도는 최초 포함 최대 3회다. 재시도 대기는 5초·10초이며 반복 실패 후 failed로 남긴다. 완료·실패 작업을 새 요청처럼 자동 재활성화하지 않는다.
- 각 attempt에는 별도 `queue-<job UUID>-a<generation>` request ID와 parent generation을 기록한다. 기존 IncidentStore의 실패 실행을 새 attempt가 그대로 재사용하지 않는다.
- settings hash가 바뀌거나 고정 증거·완료 보고서가 맞지 않으면 needs_review로 두고 자동 재처리하지 않는다. 운영자는 원본을 보존한 채 원인을 확인하고 필요하면 새 요청 키로 접수한다.
- `queue-cancel`은 대기·진행 작업을 취소한다. 이미 진행 중인 읽기 HTTP 요청을 강제 중단하지는 않지만 checkpoint/보고서 게시 전 소유권 검사가 늦은 결과의 큐 반영을 막는다. 이미 완료된 결과를 취소했다고 표시하지 않는다.
- 보고서 게시 구간은 queue 쓰기 transaction 안에서 소유권을 검사한다. 이 구간에서는 취소가 기다릴 수 있으며 먼저 commit된 상태가 기준이다. 프로세스가 멈췄을 때 다른 worker가 파일을 동시에 게시하지 못하도록 OS 잠금도 유지한다.
- 규칙 보고서를 저장했는데 큐 commit이 안 됐다면 lease 만료 뒤 기존 보고서를 검증해 완료/부분 상태만 복구한다. 이때 수집·분석을 새로 실행하지 않는다. 파일이 불완전하면 완료로 꾸미지 않는다.

현재 worker는 **규칙 분석만** 실행한다. 외부 모델의 응답·비용이 불명확한 시도를 재전송하는 기능은 없다. 모델 모드를 추가할 때는 전송 전후 상태·예산 예약과 `needs_review` 전이를 먼저 구현해야 한다. 읽기 API 요청의 정확히 한 번 실행이나 일반적인 exactly-once 처리를 보장하지 않는다.

## 명령 사용 예

아래는 진단 Pod에 이 코드가 포함된 새 이미지를 명시적으로 배포한 이후의 경로 예시다. 현재 배포된 이전 이미지는 queue 명령을 지원하지 않으므로 그대로 실행하지 않는다. 로컬에서는 등록된 자료 폴더의 settings 경로와 개인 store를 지정한다. 수집 예제의 base_url=null은 실제 호출을 시작하지 않는다.

```sh
python -m ai_ops queue-submit \
  --bindings /etc/ai-ops/bindings.json \
  --runtime /etc/ai-ops/runtime.json \
  --metric-queries /etc/ai-ops/metric-queries.json \
  --target url-shortener-production \
  --question '최근 상태와 부족한 자료를 설명해줘' \
  --request-id manual-unique-request-001 \
  --route /items --operation select_all \
  --store /data/incidents

# 기본값은 한 번 처리하고 종료한다. 대기 작업이 없으면 idle을 출력한다.
python -m ai_ops queue-work \
  --bindings /etc/ai-ops/bindings.json \
  --runtime /etc/ai-ops/runtime.json \
  --metric-queries /etc/ai-ops/metric-queries.json \
  --store /data/incidents

python -m ai_ops queue-show --store /data/incidents --job job_대신_반환된_실제_ID
python -m ai_ops queue-cancel --store /data/incidents --job job_대신_반환된_실제_ID
# 완료 결과의 incident_id/run_id로 기존 show 명령을 사용한다.
```

`queue-work --serve`를 명시한 경우에만 연속 처리한다. SIGTERM/SIGINT를 받으면 새 작업을 시작하지 않고 현재 읽기의 안전 지점에서 멈춘다. 수집이 끝난 경우 snapshot을 보존해 다음 시도가 재사용한다. Python 동기 I/O 전체의 즉시 강제 종료를 보장하지 않으며 강제 종료 후에는 lease 만료까지 최대 60초 기다릴 수 있다.

queue 명령의 exit 0은 명령·상태 조회 자체가 성공했다는 뜻이다. `queue-work`가 출력한 state가 partial/failed/needs_review인지는 반드시 별도로 읽어야 한다. `queue-submit`을 성공시키지 못한 DB 쓰기 오류는 exit 2다. 기존 collect-investigate의 부분 결과 exit 1 규칙은 바꾸지 않았다.

## 자료 보관·삭제 책임

큐는 접수·시도·중복 소유자이고 IncidentStore는 보고서·증거 소유자다. 큐 snapshot도 정제된 운영 자료이므로 같은 접근·보관 정책을 적용한다. **현재 실제 production 검증 자료는 기존 정책의 7일 기한이 우선이며, 설계의 30일 보고서 보관 제안을 자동으로 적용하지 않는다.** 합성 시험 자료는 저장소 밖 임시 폴더에서 생성하고 시험 종료 시 제거한다.

`queue-retention`으로 접수 시각 기준 7일 보관표와 삭제 대상을 확인하고, `--apply`를 지정한 경우에만 종료·만료 작업의 질문·snapshot·연결된 모든 시도의 engine 자료와 request index를 정리한다. 중복 해시 키·예산 장부·API 키·다른 수동 보고서는 보존한다. 삭제 계획을 commit한 뒤 파일을 이동·정리하므로 부분 삭제나 DB 기록 실패 후 재개할 수 있다. 미완료·유효 lease·불명확한 소유권은 차단한다. 예약 정리와 실제 worker 배포는 아직 없으며 [두 번째 묶음](B4-retention.md)에 범위·명령·v1 전환·한계를 기록했다.

## 검증과 남은 범위

첫 묶음 검증 당시 전체 Python 178건(신규 큐 16건 포함)을 통과했다. 실제 별도 프로세스를 `os._exit`로 종료해 claim 직후·snapshot 저장 뒤·보고서 파일 저장 뒤의 복구를 확인했다. 마지막 경우 새 collection/attempt/run을 만들지 않고 같은 보고서로 완료 상태를 복구했다. 별도 프로세스 worker 잠금, 60초 lease/generation 검사, 취소 후 늦은 게시 차단, 설정 변경/자료 변조 보류, 최대 세 시도·포화·원문 미저장도 검증했다. 이 시험은 저장소 밖 합성 입력만 사용하며 AWS·외부 AI를 호출하지 않는다.

**두 번째 묶음:** 보관표·연계 삭제 신규 시험 17개를 포함한 현재 전체 Python 199개 통과. 실환경 자료 삭제·worker 배포는 하지 않았다.

**다음 묶음:** 실제 worker 이미지/배포 모드·queue/worker 관측과 상태 조회 접근 제어를 연결한 뒤 실제 Pod 종료/재시작에서 큐·보고서 보존을 확인한다. B-05는 그 뒤 경보 접수/인증·episode startsAt·resolved tombstone과 기존 Slack/Watchdog 유지 검증을 추가한다. B-04 전체 완료나 클러스터 자동 조사가 시작됐다고 표시하지 않는다.

**세 번째 묶음:** [worker 배포 모드·고정 지표·진행 검사](B4-worker-deployment.md)를 로컬 구현했다. 전체 Python 207건·Helm 진단/worker 경계 검사 통과. 새 이미지·실제 Pod 복구 검증은 대기다. 연속 worker는 유휴 중에도 잠금을 유지하므로 보관 정리는 진단 모드 전환 후 수행한다.
