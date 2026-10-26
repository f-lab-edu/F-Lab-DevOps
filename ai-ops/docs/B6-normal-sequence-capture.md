# B-06 실제 GET·같은 Pod 로그의 순서 수집

상태: **2026-10-26 KST 수집 연결 구현·실습 한 건 검증 완료.** 같은 아이템의 `miss → hit → miss → hit` 네 조회와 원문 로그를 대조했고 자기 데이터 삭제·기준선 유지·공유 잠금 해제를 확인했다. 모델 호출 0회다. v3 정제 exporter·전송 미리보기·상시 자동 접수는 아직 연결하지 않았다.

## 구현한 흐름

`scripts/run_b6_sequence_capture.py capture`는 개인 전용 저장 폴더를 만들고 `SequenceLabClient`의 GitOps 기준선을 확인한다. 공유 `LabLease`를 취득한 뒤 자기 아이템 한 건을 생성하고 cold/hit 두 쌍을 실행한다. 각 GET의 앞뒤 counter 측정 전체 구간과 실제 HTTP 요청 구간을 별도로 기록한다. 결과는 HTTP 200·응답 item ID·counter 전후값을 포함한다.

실행 전 고정한 Pod 이름·UID·Deployment UID/세대·이미지 digest·컨테이너 실행 ID·재시작 횟수를 각 exec/logs 전후에 다시 확인한다. Deployment 이름으로 임의 Pod를 고르지 않고 고정된 Pod 이름으로만 실행한다. 앱 로그는 `timestamps=true`, `follow=false`, `previous=false`, 첫 측정 시각 이후로 조회하며 64KiB·256줄 제한에 닿으면 거부한다. 같은 아이템의 고정 `cache_miss/cache_hit` 줄이 각 측정 구간에 하나씩 존재하고 counter가 연속적으로 증가해야 순서 기록을 만든다.

이 순서는 실습기의 private checkpoint에 먼저 고정한 뒤 기존 읽기 수집기로 규칙 보고서를 만든다. 끝에서는 자기 아이템만 삭제하고 실습 네 Deployment·운영 식별자·고정 Pod·Argo 기준선이 같음을 확인한다. 복구 확인 실패 시 `recovery_failed`로 기록하고 공유 잠금을 남긴다. 결과·checkpoint는 Git 밖 0700 폴더의 0600 JSON이며 원본 ID·로그를 공개 문서에 복사하지 않는다.

| 파일 (`ai-ops/` 기준) | 변경 |
|---|---|
| `src/ai_ops/b6_scenarios.py` | 앱 내부 GET의 측정 시작/종료·실제 요청 시작/종료·응답 item ID를 추가하고 counter를 정수로 보존 |
| `src/ai_ops/b6_sequence_capture.py` | GitOps 기준선·같은 Pod 고정, 제한 로그 조회·개별 요청 결합, 공유 잠금·checkpoint·실패 시 자기 데이터 정리 |
| `scripts/run_b6_sequence_capture.py` | 고정된 개인 저장 폴더와 `capture` 명령. 임의 대상·파일·URL 입력 없음 |
| `tests/test_b6_sequence_capture.py` | 순서 기록·별도 요청 시각·원문 로그 제한·Pod 교체·생성 응답 유실·수집 실패·복구 실패·잠금 보존 등 8개 모의 시험 |

## 실제 실행 결과

2026-10-26 **16:21:30~16:23:27 KST**에 격리 `ai-ops-lab/lab-api`에서 한 번 실행했다. 모델 계정·키·캠페인을 활성화하지 않았다. Deployment 설정과 운영 앱은 변경하지 않았다.

| 항목 | 결과 |
|---|---|
| 시작 전 | Argo `lab-target` Synced/Healthy, 실습 Deployment 4개 Ready 1/1, Prometheus 품질 사전 검사 통과 |
| 조회·로그 | HTTP 200 네 건, 같은 아이템의 `miss → hit → miss → hit`, 같은 Pod UID·컨테이너 실행 ID. 3,588바이트 제한 로그와 조회 기록을 연결 |
| 규칙 조사 | 원본 bundle 해시 `9c6db79c20a6facce4ed6cd5e1028330f69d79e1a3b3d02bc4b4da076231e5d9`, `partial / insufficient_evidence / unknown`, 사실 4개·가설 0개 |
| 복구 | 자기 아이템 삭제·Redis PING, 실습/운영 기준선 불변, Argo 기준선 유지. 공유 잠금 해제와 305초 재실행 대기 창 확인 |
| 보존 후 재검증 | 저장 JSON의 네 조회를 다시 `RequestObservation`·`ProbeRead`로 검사하고 원문 로그 SHA-256·선별 로그 해시 일치 확인 |
| 비용 | 외부 모델 호출·추가 API 예약 0. AWS 실행 비용은 이 기록의 API 비용에 포함하지 않음 |

비공개 결과는 `~/.local/share/url-shortener-ai-ops/normal-sequence-observations/s2-bf430200faba4e11aaa135773430301d/result.json`이다. 원문 로그와 응답 item ID는 이 폴더 안에만 남는다. 이것은 해당 아이템의 과거 실습 조회 순서를 검증한 것으로 전체 서비스 정상·자연 TTL 만료·현재 상태를 입증하지 않는다.

## 시험과 남은 연결

후속 [v3 exporter 검증](B6-normal-sequence-exporter.md)에서 이 저장 자료와 수집기 내부 원본 snapshot을 결합해 9항목 정제 입력을 생성했다. 아래 exporter 미연결 문장은 수집 완료 당시 기록이다. 현재 남은 것은 v3용 미리보기·입력별 승인/예산 경로와 실제 모델 비교다.

전체 Python **473개 시험**과 문법·변경 형식 검사가 통과했다. 신규 수집 경로·기존 시나리오·v3 계약 관련 36개 시험도 통과했다. 원격 CI·Docker 이미지 빌드·상시 배포 시험은 수행하지 않았다.

다음은 저장된 원본 조사 snapshot을 실습 수집기 안에서 다시 검증하고, 이번 private 조회 기록·로그 해시·같은 Pod 소유 관계를 결합해 **v3 정제 입력만 반출하는 exporter**를 연결하는 작업이다. 현재 `capture` 성공은 `ProbeRecord/SequenceProjection` 전체 생성 성공을 의미하지 않는다. 원본 snapshot을 임의 로컬 파일로 대체하지 않으며, v3 미리보기·입력별 승인·예산 경로를 준비한 뒤 실제 모델 비교는 별도로 수행한다. B-06 완료·C단계 진입은 계속 보류한다. 다음 작업 권장 추론 깊이 **GPT-6 SOL high**.
