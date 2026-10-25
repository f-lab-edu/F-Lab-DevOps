# B-06-M7 첫 클러스터 모델 캠페인 실측

상태: **2026-10-25 KST 첫 격리 실습 CI의 자동 접수·실제 모델 보고서 생성·자체 내용 검토 완료. M7과 B단계 전체는 진행 중.** 이 기록은 실습 환경 한 건의 결과이며 운영 장애 진단 정확도나 자동 복구를 입증하지 않는다. 원문 입력·API 키·내부 토큰·전체 보고서는 Git에 넣지 않는다.

## 범위와 처리 흐름

대상은 `ai-ops-lab/lab-api` 한 개다. 사용자가 선택적 데이터 공유 해제를 확인하고, 새 이미지 digest·대상 UID·CI 설정·정제 샘플·`gpt-6-sol`/low·24시간/최대 10건/건당 $0.10 예약/UTC 일일 $1 예약에 결합된 캠페인 `campaign-6f85e38e42294e34b2efed00365e1d85`를 승인했다. 결합 해시는 `63863b0c6789862688e98bc8cb4775c355612913a42e60b92cd9e6d4041d67b0`, 이미지 digest는 `sha256:7d3d769b0d161e4737b925542500a271f4a9abbf0a50cbc2a8ec3ae865cabe56`이다. `store=false`여도 제공자의 기본 악용 감시 보관 조건은 별도로 인지했다. 승인 범위는 **2026-10-26 15:38:43 KST**에 만료된다.

GitHub 실습 CI → 최초 이미지 실패·기존 CI의 자동 원복 → artifact → CI 전달기 → 영속 큐·규칙 보고서 → 허용 필드만 담은 정제 view → 모델 gateway 장부·예산 예약 → OpenAI 토큰 계산·생성 → 별도 `needs_review` 보고서 순서로 동작했다. AI가 앱을 복구한 것이 아니다. 기존 CI가 복구했고 AI는 사후 자료를 읽었다.

| 확인 항목 | 실측 결과 |
|---|---|
| [격리 CI 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40170463375) | 최종 `failure`는 최초 실패 보존을 위한 의도된 표시. `b6-lab-observation-40170463375-1`의 `result.json`은 `recovered_after_failure`, `rollback.json`은 `success` |
| 복구 상태 | 실습 `lab-api` Ready 1/1, Argo CD `lab-target`은 `Synced/Healthy` |
| 자동 접수 | 전달기 `submitted=1`, 규칙 작업 `job-0c0d99253ce347d2bcd4f38218293179`의 GitHub 실행 ID가 `40170463375`와 일치 |
| 별도 모델 장부 | 같은 조사 실행 `run-da083fcfe3194fd2a11171804a7be7d8`의 outbox 한 건, `needs_review / semantic_review_required`; 중복 전송 없음 |
| 사용량 | 입력 1,417·출력 444토큰, 그중 cache write 1,414토큰. 사용량 기반 **추정 $0.007981**이며 청구 확정액이 아님 |
| 예산 | UTC 2026-10-25 장부 예약 누계 $0.20. 첫 실패 시도 $0.10과 이번 성공 시도 $0.10을 포함하며 실제 청구액과 구분 |
| 실행된 조치 | 없음. 보고서의 후속 제안 2개도 `not_executed` |

소유자 전용 검토안과 정제 결과는 로컬 `~/.local/share/url-shortener-ai-ops/model-campaigns/campaign-6f85e38e42294e34b2efed00365e1d85/`에 0600으로 보존했다. 이 경로와 GitHub artifact는 확인 근거이며 저장소에 원문을 복사하지 않는다.

## 전송 경계 수정과 첫 실패 보존

첫 캠페인 `campaign-35c768959d8642ea902b9dc39d340bf0`의 [실습 CI](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40168679847)는 복구됐지만 모델 토큰 계산이 `projection_token_count_unavailable`로 끝났다. 이미지의 Python 3.11이 `HTTP/1.0 CONNECT`를 보낸 반면 프록시가 `HTTP/1.1`만 허용해 403을 반환했다. 이 요청은 프록시에서 막혀 OpenAI에 도달하지 않았다. 결과를 덮거나 재전송하지 않고 해당 캠페인을 `stopped`, outbox를 `needs_review`로 남겼다.

[PR #78](https://github.com/f-lab-edu/F-Lab-DevOps/pull/78)에서 고정 목적지·Host 헤더 검사를 유지하며 HTTP/1.0을 허용했다. 전체 AI 시험 443개와 [수정 이미지 등록](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40169522299)이 통과했고, 실제 Python 3.11 컨테이너에서 키·본문 없이 CONNECT/TLS 1.3 연결을 확인했다. 이미지가 바뀌어 첫 캠페인 승인을 재사용하지 않고 새 결합 해시로 다시 승인받았다.

## 보고서 의미 검토

정제 view는 7항목 중 Pod 상태·설정 요약 2개만 유효했다. 지표 5개는 `scrape_unavailable`이고 로그·이벤트·배포·CI 자료는 외부 전송 정책상 제외됐다. 모델은 `ev-view-001`의 Running/Ready·재시작 0과 `ev-view-002`의 Redis URL 설정 여부를 각각 사실로 인용했다. 입력 해시와 허용된 근거 ID가 일치했다. 보고서는 `partial / unknown / insufficient_evidence`, 원인 가설 0개, 한계 4개, 미실행 제안 2개다. 누락 지표를 0으로 취급하거나 Ready를 서비스 정상·복구 완료로 바꾸지 않았다.

같은 실행의 **규칙 보고서**는 내부에 보존한 CI 배포·원복 증거를 인용해 `suspected_cause`로 이미지 실패 후 자동 원복 흐름을 설명했다. 모델은 그 자료를 받지 못했으므로 이번 사건의 원인을 설명하지 못했다. 이 내용 검토는 구현한 에이전트의 자체 점검이며 독립 평가자의 정확도 채점이 아니다. 모델 장부의 `needs_review`를 완료 상태로 바꾸지 않는다.

## 캠페인 만료 시 확인·정리

2026-10-25 16:05 KST 확인 시 gateway·proxy·CI worker·전달기는 각각 Ready 1/1, worker 모델 연결은 켜져 있고 캠페인은 `approved`였다. 만료 전에는 승인 범위 안의 새 실습 CI 입력이 자동 전송될 수 있다. 만료 시각이 지나도 Deployment가 자동으로 복제수 0이 되거나 Secret이 삭제되는 것은 아니다. gateway 프로세스는 만료를 감지해 종료하지만 Deployment가 다시 시작할 수 있으므로 **만료 후 운영자 정리가 필요하다**.

1. UTC 장부의 캠페인 상태·outbox 상태별 건수·예약액과 `sending`/`prepared` 유무를 먼저 기록한다. 전송 중이거나 결과가 불명확하면 자동 재시도·완료 선언을 하지 않는다.
2. `ai-ci/ci-worker`의 모델 bridge를 끄고 rollout을 확인한다. 공유 PVC를 쓰는 `ci-relay`와 worker의 교체가 겹치지 않도록 전달기를 잠시 0으로 내렸다가 worker 준비 후 1로 복구한다. 규칙 조사와 전달기는 계속 동작하는지 확인한다.
3. 장부의 **이 캠페인 ID만** `stopped`로 기록한 뒤 `ai-model/model-gateway`와 `model-egress-proxy`를 복제수 0으로 내린다. 첫 실패 장부와 이번 결과·규칙 보고서·기존 AI 자료는 보존한다.
4. `ai-ci/model-client-auth`, `ai-model/model-client-auth`, `model-openai-key` Secret은 Deployment의 참조·마운트를 먼저 제거하거나 기본 비활성 정의를 재적용한 뒤 삭제한다. Secret만 먼저 삭제해 worker 재시작을 막지 않는다. API 키의 개인 원본은 AWS 리소스 정리와 별개다.
5. worker·전달기 Ready, 모델 Pod 0, Secret 부재, 추가 outbox/예약 없음과 실습 앱 `Synced/Healthy`를 재조회한다. **2026-10-26 15:38 KST 이후의 실제 확인 결과는 아직 없다.**

## 남은 완료 조건

- 정상 관측과 제한 장애 관측의 비교, 중복·예산·전송 실패·중지/만료 경계, 독립 의미 검토를 필요한 범위로 마친다. 이미 통과한 전체 회귀를 이유 없이 반복하거나 이 한 건을 일반 진단 정확도로 환산하지 않는다.
- 원인 설명에 필요한 [CI 실패·원복 정제 입력 v2 설계안](B6-model-ci-projection-v2-design.md), [오프라인 투영](B6-model-ci-projection-v2-implementation.md), [worker/gateway 연결 코드](B6-model-ci-projection-v2-connection.md)를 작성했다. 연결 코드는 배포하지 않았고 v2 외부 전송은 아직 하지 않았다. 정책·구현·이미지·입력 범위가 바뀌면 이 캠페인 승인을 재사용하지 않고 새 검토안과 승인을 받는다.
- [B-06 평가](B6-operational-evaluation.md)와 AI 작업 리스트의 B 종료 기준에 현재 실측과 미검증 범위를 반영한 뒤 C 진입 여부를 별도 판정한다. C단계의 변경 실행 권한은 아직 없다.

다음 작업 권장 추론 깊이는 **GPT-6 SOL high**다.
