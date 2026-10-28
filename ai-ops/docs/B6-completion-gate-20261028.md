# B-06 완료 조건 재판정 — 2026-10-28 KST

최종 판정(17:13 KST 갱신): **B-06의 격리 실습 범위 필수 종료 조건 충족.** 이후 [B-02 원본/시각 대조](B2-cross-source-time-audit-20261028.md)도 원래 목록의 제한 범위를 충족해, [B단계 전체 대조](B-stage-readiness-audit-20261028.md)상 합의된 읽기 조사·격리 실습 종료 조건을 마쳤다. [C-01 설계](C1-approval-recovery-design.md)는 작성했으나 인증·승인·변경 기능과 권한 부여는 하지 않았다. [2026-10-26 판정](B6-completion-gate-20261026.md)의 세 필수 조건을 10월 28일의 새 정상 관측 실측·모의 경계 시험·중지 후 장부 조회로 대조했다. 독립 의미 검토는 사용자 선택으로 생략했으며 보고서는 계속 `needs_review`다. 아래 기록의 모의 시험과 실제 조회 범위는 합쳐서 실환경 실패 주입으로 표현하지 않는다.

| 필수 조건 | 이번에 확인한 근거 | 현재 판정 |
|---|---|---|
| 배포된 읽기 조사와 모델 분석 연결 | [정상 CI #40643171148](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40643171148) 성공 → 전달기 자동 접수 → worker 영수증 `ready` → 한 건 `submitted` → gateway outbox `needs_review`. 출처 실행·정제 view·보고서의 입력 해시와 원복 조건을 [실측 기록](B6-normal-source-persistence.md#2026-10-28-v3-승인-캠페인-한-건-실측)에서 대조했다. 기존 v2의 이미지 실패·CI 원복 경로와 별개의 정상 입력이다. | **격리 실습의 정상·제한 장애 두 경로 확인.** production 전체 장애나 AI의 변경 실행은 확인하지 않았다. |
| 새 경로의 의미 검증: 정상 관측·제한 장애·자료 부족 | v3 보고서는 같은 아이템의 `miss(200)→hit(200)→miss(200)→hit(200)`을 인용하고 첫 miss 다음 hit를 제한된 캐시 동작으로 설명했다. 세 번째 miss의 원인, 자연 TTL, 전체 서비스 정상은 유보했다. [v2 제한 장애](B6-model-ci-projection-v2-connection.md)에서는 이미지 취득 실패를 후보로, 기존 CI의 원복을 별개로 설명했다. [첫 v1](B6-model-campaign-first-live-run.md)은 자료 부족을 유보했다. | **자체 대조상 세 부류의 제한 범위 충족.** 정형 계약 검사는 통과했지만 독립 의미 검토는 사용자의 선택으로 생략했고 모든 모델 보고서는 `needs_review`다. 정확도나 운영 장애 원인 적중률은 산출하지 않는다. |
| 배포·실패 경계: 중단·중복·예산·만료 | 승인된 v3 캠페인 한 건의 비용은 추정 `$0.008931`, 장부 예약 `$0.10`이다. 한 건 뒤 캠페인을 중지하고 worker 모델 연결·토큰 마운트와 클러스터 모델 Secret을 제거했으며 gateway/proxy를 0으로 내렸다. 동일 CI 재접수는 `duplicate=true`, 영수증/전달 각각 1건 유지였다. 중지 후 읽기 전용 PVC 조회에서도 `stopped`, outbox/보고서 1건, sending 0건과 worker 입력 결합·SQLite 무결성을 확인했다. 응답 유실·만료·토큰 계산 직후 만료·UTC 일일 한도는 기존 로컬 모의 시험으로 검사했다. | **제한 범위 충족.** 중복·중지·보존/정리는 실제 배포에서, 불명 전송·예산 거부·만료 차단은 모의 시험에서 확인했다. 실제 생성 요청 도중 강제 종료·유료 429/timeout·24시간 자연 만료·Kubernetes 자동 정리는 실측하지 않았다. 이를 완료 근거로 주장하지 않는다. |

## 이번 경계 검증

`test_model_runtime`·`test_normal_ci_model_bridge`·`test_continuous_model_live`의 로컬 Python 3.14 시험 **17개가 모두 통과**했다. 처음에는 샌드박스의 loopback 소켓 제한으로 기존 HTTP 시험 1개가 막혔고, 로컬 소켓 권한으로 같은 세 모듈을 재실행해 통과를 확인했다. 새 시험은 live 캠페인 장부에서 **중지·만료 후 전송/예약 0건**과 **일일 예약액 100센트에서 추가 전송/이중 예약 0건**을 확인한다. 외부 transport는 모의로 대체했고 OpenAI 호출·비용·클러스터 변경은 없었다. 이전 원격 CI의 통과 기록도 이번 새 코드의 원격 검증으로 세지 않는다.

현재 EKS 주소를 AWS에서 다시 확인하고 기본 kubeconfig를 바꾸지 않은 임시 설정으로 조회했다. `ai-ci/ci-worker`·`ci-relay`는 각각 1/1, `ai-model/model-gateway`·`model-egress-proxy`는 복제수 0이었다. worker 장부에서 CI `40643171148`의 정상 영수증은 `ready` **1건**, 해당 v3 캠페인의 전달은 `submitted` **1건**이었다. 원문 증거·Secret·보고서 본문은 출력하지 않았다.

이후 worker의 모델 연결 변수가 꺼져 있고 모델 gateway/proxy가 0임을 재확인했다. GitHub에서 해당 CI의 head SHA `1771c8026e2c88de301e8ed38752863676804fc5`를 다시 확인하고, 배포된 `ci-relay`의 기존 `--normal-run-id` 경로로 **같은 실행을 한 번 재검증·접수**했다. 결과는 `duplicate=true`, 영수증 상태 `ready`였으며 worker의 해당 실행 영수증/캠페인 전달 건수는 **재접수 전 1/1 → 후 1/1**로 유지됐다. 새 CI 실행·앱 변경·유료 모델 호출은 없었다. gateway가 중지된 상태라 gateway 장부의 outbox 건수를 이번에 직접 재조회한 것은 아니다.

## 만료 경계와 종료 후 리소스 — 2026-10-28 KST

로컬 Python 3.14에서 `test_model_runtime`·`test_normal_ci_model_bridge`와 live 장부의 만료 시험, 총 **12개**가 통과했다. 준비된 영수증이 있어도 만료한 정상 CI bridge는 전달 기록을 만들지 않는다. live 장부는 만료 후 토큰 계산 전에 요청을 거부하고 예약도 하지 않는다. 토큰 계산 응답 직후 만료되면 gateway의 전송 전 재검사가 보고서 생성 호출을 막고 `needs_review/campaign_stopped`로 남긴다. 재기동 검사는 자격 파일을 읽기 전에 만료 캠페인을 거부한다. 외부 HTTP 호출은 모의로 대체했으며 OpenAI 전송·비용은 없었다.

현재 EKS를 읽기 전용으로 재조회한 결과 `model-gateway`·`model-egress-proxy`는 복제수 0, 모델 Pod 0, `ai-model` Secret 0, `ai-ci/model-client-auth` Secret 부재였다. worker의 두 모델 bridge 설정은 모두 꺼져 있고 worker·relay는 각각 Ready 1/1이다. 보존 대상인 `ai-model/model-ledger` 암호화 PVC는 `Bound`다. **실제 승인 캠페인은 만료 전에 조기 중지했다.** 그러므로 이번 조회는 만료 시점에 Kubernetes가 Pod/Secret을 자동 정리했다는 실측이 아니다. 코드상 gateway 프로세스의 만료 감지와 Deployment·Secret 정리는 별도이며, [운영자 정리 절차](B6-model-campaign-first-live-run.md#캠페인-만료-시-확인정리)를 유지한다.

## 중지 후 모델 장부 보존 확인 — 16:45 KST

`model-gateway`를 기동하지 않고 기존 고정 이미지로 임시 조회 Pod를 만들었다. API 키·클라이언트 Secret·서비스 계정 토큰은 마운트하지 않았다. `model-ledger` PVC와 root filesystem은 읽기 전용이고, Pod label은 gateway/proxy Service·허용 정책과 다르게 정해 namespace의 기본 ingress/egress 차단만 적용되게 했다. SQLite는 `mode=ro`와 `query_only=ON`으로 조회했으며 모델 서비스 코드나 저장소 초기화 코드를 실행하지 않았다.

| 대조 항목 | 실제 조회 결과 |
|---|---|
| 캠페인 | `campaign-683ca19c2a7341498afa306518de89db`는 **stopped** |
| 모델 outbox·본문 | 해당 캠페인 outbox **1건**, 보존된 보고서 **1건**, `needs_review/semantic_review_required`, 전송 중 `sending` **0건** |
| worker 연결 | CI `40643171148`의 영수증 `ready`·전달 `submitted` 조인 **1건**. 내부 조사 ID·outbox ID·view hash를 결합한 해시가 gateway 저장 기록과 일치 |
| 보고서 입력·실행 | 보고서 `input_hash`가 저장 view의 해시와 일치. `semantic_review_required=true`, `execution_status=not_executed` |
| 무결성 | SQLite `quick_check=ok`. DB·view·보고서 원문은 외부 파일이나 출력에 복사하지 않음 |
| 조회 종료 후 | 임시 Pod 두 개 모두 삭제. gateway/proxy 복제수 **0**, 모델 Pod **0**, `ai-model` Secret **0**, `ai-ci/model-client-auth` 부재. worker 모델 연결 환경변수 없음. worker·relay 각각 Ready **1/1**, 장부 PVC **Bound** |

GitHub CI 실행 ID와 저장소 내부 `run-…` 조사 ID는 서로 다른 식별자다. 처음 조회의 두 값 직접 비교는 출처 검사가 될 수 없어, 실제 worker 영수증의 `receipt.source_run`·outbox·입력 해시를 연결해 재대조했다. 최초 조회와 결합 재조회 모두 읽기 전용이었고 장부 상태를 바꾸지 않았다. 이번 확인은 **전송 완료 뒤 중지·동일 CI 재접수·보고서 보존**의 근거이며, 생성 요청이 진행되는 도중 프로세스를 강제 종료한 시험은 아니다. 그런 응답 유실·재기동 경계는 앞서 통과한 모의 시험 범위다.

## 남은 판정과 다음 순서

1. **B-02 원래 잔여 조건 판정 완료.** [별도 실습 두 사례의 시각 및 S10 원본 artifact 대조](B2-cross-source-time-audit-20261028.md)로 출처별 원본/수집 시각·UTC 정규화·누락 이유·실패→원복 순서를 확인했다. 현재 Deployment를 읽은 시각을 배포 발생 시각으로 사용하지 않고, 빠진 로그·지표를 남겼다. production 전체 장애나 사용자 요청 경로 복구를 입증한 것은 아니다.
2. **B-05 Healthchecks 최종 수신 확인 완료.** 사용자 로그인 후 보존 URL과 동일 Check의 **2026-10-28 17:06 KST 수신 OK·현재 Up**을 확인했다. [수신 기록](B5-watchdog-delivery-20261028.md)에 근거를 갱신했다. 최근 약 10분 간격과 소스의 5분 설정 차이는 별도 운영 후속이며 5분 정시 수신을 완료 성과로 주장하지 않는다. 수동 ping·일정 변경은 없었다.
3. **변경한 문서와 경계 시험을 검토·커밋한 뒤 원격 CI에 반영한다.** 로컬 새 시험의 통과를 원격 CI 통과로 표시하지 않는다. 원래 종료 기준은 적합한 모의 실패 검증을 허용하므로 실제 24시간 대기·유료 한도 도달·독립 AI 검토를 추가 필수 작업으로 만들지 않는다. 기능 범위의 B 종료 조건은 충족했고 [C-01의 요청·승인·권한 경계 설계](C1-approval-recovery-design.md)를 작성했다. 다음 기능 작업은 C-02의 제한된 사건 조회와 제안 계약 구현이다.

모델의 제안 조치는 모두 `not_executed`다. C단계의 해결 요청·특정 변경 승인·GitOps 복구 권한은 이 평가로 부여되지 않는다. 다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
