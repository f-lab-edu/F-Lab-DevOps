# B-06-M7 CI 실패·원복 v2 캠페인 연결 구현

상태: **2026-10-25 KST v2 캠페인 승인·격리 CI 한 건의 실제 모델 보고서 생성·조기 중지 완료. B-06 전체는 진행 중이며, 독립 의미 평가는 사용자 선택으로 생략.** [v2 오프라인 투영](B6-model-ci-projection-v2-implementation.md)의 정제 결과를 기존 worker/gateway 장부에 버전별로 연결한다. v1 보고서·장부와 실습 앱은 보존했다.

## 동작 흐름과 버전 경계

1. `ContinuousModelStore.prepare_live_campaign(policy=...)`은 정책 v1/v2에 맞는 프롬프트 버전·구현 해시·이미지 digest·대상 UID·config hash·cluster fingerprint·샘플 view hash·API 키 지문·기간/건수/비용을 새 캠페인 binding에 묶는다. 알 수 없는 정책 버전은 거부하고 기본 v1로 낮추지 않는다.
2. `build_review_packet()`은 binding의 정책에 맞는 view 계약을 다시 검사하고 토큰 계산·생성 요청 **두 본문 전체**, 각 해시·바이트 수·비용·보관/공유 확인 사항을 준비 상태로 보여준다. 이 검토안 생성은 승인이 아니며 API도 호출하지 않는다.
3. 별도 명시 승인이 있다면 `build_activation_patches()`가 worker의 `AI_MODEL_POLICY`를 binding 정책으로 지정한다. worker의 `ContinuousCIBridge`는 큐 HMAC·CI 출처·규칙 보고서·snapshot을 확인한 뒤 v1은 기존 투영, v2는 검증된 CI 실패·원복 쌍의 전용 투영을 선택한다. v1 캠페인은 기존 입력을 계속 쓰며 v2 입력으로 자동 전환되지 않는다.
4. worker의 `ModelGatewayClient`는 v1 `ModelSubmission` 또는 v2 `CIModelSubmission`을 선택해 같은 서비스 토큰으로 정규 JSON에 서명한다. gateway는 **서명·schema 버전·view 계약·정규 JSON**을 확인한다. 장부는 캠페인 정책/프롬프트/구현 해시와 제출 view 정책을 다시 대조한 뒤에만 outbox를 만든다. v1↔v2 교차 제출은 예약이나 외부 요청 이전에 거부한다.
5. `ModelGatewayRuntime`과 장부는 각 처리 직전 승인 기간·현재 대상/자격·이미지·정책을 확인한다. 승인된 v2 outbox만 v2 프롬프트로 토큰 계산과 생성을 할 수 있다. 기존 건당 $0.10·UTC 하루 $1 예약, 10건, 중복 방지, 불명 전송 재시도 금지, `needs_review` 저장을 유지한다. 아래에 한 번의 실제 처리 결과를 기록했다.

## 검증과 남은 조건

`tests/test_model_ci_connection.py`는 저장 S10 이력에서 만든 정제 입력으로 v2 worker→outbox, 서명/버전 계약, 전체 요청을 담은 준비 검토안, 활성 패치의 정책값, 모의 모델 처리와 교차 정책 거부를 확인한다. 새 검사와 관련 v1 회귀 **39건**은 로컬에서 통과했다. 로컬 포트를 쓰는 기존 gateway 경계 **8건**도 외부 API 호출 없이 통과했다. 문법 검사와 `git diff --check`가 통과했으며, 잠금 의존성 기준 원격 CI 결과는 PR에서 확인한다.

v1 캠페인은 승인 기간 만료 전에 조기 중지했고 worker bridge 해제, gateway/proxy 복제수 0, 관련 Secret 삭제, 규칙 worker·전달기와 실습 앱 정상 상태를 [실측](B6-model-campaign-first-live-run.md#2026-10-25-kst-조기-중지-실측)했다. 실제 만료 시점의 자동 동작은 실측하지 않았다. [v2 첫 이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40174117827)은 main `c011d618b5581c3b5110ccb16a9dae69f627ef3a`를 digest `sha256:c33b7bfb3cbe82e58eabc5a285e5a58a6d5ba610eee98d166c1fda582dc09f2e`로 ECR에 등록했고 검사 상태 `COMPLETE`, 심각도별 발견 0건이었다. 이 단락의 검증 시점에는 클러스터에 배포하지 않았다.

새 캠페인 승인 전에는 실제 검증 CI의 v2 정제 샘플·두 요청 본문·[공식 `gpt-6-sol` 가격](https://developers.openai.com/api/docs/models/gpt-6-sol)·API 계정 공유 설정·보관 조건과 새 이미지/코드 해시를 함께 검토했다. 기존 v1 승인을 전용하지 않고 v2 활성화를 별도로 승인받았다.

2026-10-25 KST에는 [이미 복구가 확인된 격리 CI 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40170463375)의 저장 snapshot과 큐 HMAC 이력을 재검증해 **과거 관측 시각 그대로** v2 샘플을 만들었다. 허용 항목 9개 중 CI 실패·원복 유한 상태 2개가 포함됐고 나머지 26개 자료는 제외로 집계했다. `view_hash`는 `a8b6f471a6fc1d1bca4e822d01339234fe12da37096a552c7058eed40eb10c3b`이다. 새 장애 실행 40174316757은 중복 시험을 피하려고 승인 전 취소했다. 새 CI가 실제로 v2 outbox를 만드는지는 별도 승인 후 한 번 확인해야 한다.

비공개 검토안은 `/private/tmp/m7-v2-review-20261025/review.html`에 두 외부 요청 본문 전체와 예산·보관·자동 입력 범위를 표시했다. 승인 당시 `campaign-e97f582859c04d329331a7e205a8852f`의 결합 해시는 `c74a534c25cfe19b7d1705875a1f05f24c04e5336bf758fbe62dc1b9256ae91d`, 승인 만료 예정은 2026-10-26 16:57:23 KST였다. 가격은 2026-10-25 UTC [OpenAI 공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-6-sol)의 100만 토큰당 입력 $2, 캐시 입력 $0.20, 캐시 쓰기 $2.50, 출력 $10을 기준으로 묶었다. 비공개 입력·자격 원문·전체 전송 본문은 Git에 넣지 않는다.

## 2026-10-25 KST 실제 v2 처리와 중지

사용자는 위 새 캠페인과 API 계정의 선택적 데이터 공유 해제 상태를 별도로 승인했다. 승인 범위에서 gateway는 v2 이미지 `sha256:c33b7bfb3cbe82e58eabc5a285e5a58a6d5ba610eee98d166c1fda582dc09f2e`로 실행했고, 실습 [CI 실행 40178333818](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40178333818)은 격리 앱의 `ErrImagePull`·Ready 0을 기록한 뒤 기존 CI가 기준 revision으로 자동 원복했다. 최초 실패를 보존하므로 GitHub의 최종 표시는 `failure`이고, artifact의 결과는 `recovered_after_failure`다. 운영 앱과 사용자 요청 경로의 복구를 검증한 실행은 아니다.

전달기가 처음에는 이 기록을 거부했다. Pod 실패 증거가 있음에도 Argo CD 집계 상태가 이전 `Healthy`를 표시하고 동기화 동작을 `Succeeded`로 기록한 조합을 계약이 금지했기 때문이다. [PR #84](https://github.com/f-lab-edu/F-Lab-DevOps/pull/84)는 새 generation·실패 이미지·`ErrImagePull`·Ready 0·실패 revision 조건을 유지하며 이 지연 상태를 허용했다. 실제 artifact 3개와 회귀 검사 11개, 원격 계약·이미지 검사가 통과했다. [수정 이미지 등록](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40178791980)의 digest `sha256:9e45a94834c9a61dfcf3e0464db991121056798d0a8f8ee2ee301c3dafd7596e`를 전달기와 worker에 적용했다. 전달기는 같은 CI 기록을 **새 실습 실행 없이** 한 번 자동 접수했다. 이전 worker가 멈춘 `integrity_error` 작업은 새 worker에서 큐 HMAC과 원본 3단계를 재검증한 뒤 기존 시도 기록을 보존해 한 건만 다시 처리했다.

규칙 작업은 `partial`, 모델 outbox는 한 건의 `needs_review / semantic_review_required`로 끝났다. 실제 정제 입력 10항목 중 `ev-view-009`는 이미지 취득 실패·Ready 0, `ev-view-010`은 기존 CI 원복 검증 성공·Ready 1을 담았다. 모델 보고서는 `partial / unknown / suspected_cause`로 이미지 취득 실패를 최초 배포 실패의 **원인 후보**로 설명했고, CI 원복과 사용자 요청 경로 복구 검증을 구분했다. 원본 이미지·근본 원인은 알 수 없다고 남겼으며 제안 2건은 모두 `not_executed`다. 이는 구현자의 자체 의미 검토이고 독립 평가가 아니다. 모델이 앱을 고치거나 현재 서비스 정상 상태를 확정한 결과도 아니다.

입력 1,729·출력 471·캐시 쓰기 1,726토큰, 사용량 기반 추정 비용은 **$0.009031**이다. UTC 2026-10-25 장부 예약 누계는 기존 두 건을 포함해 **$0.30**이며 실제 청구 확정액과 다르다. 정제 입력과 전체 모델 보고서, CI 실패·원복·결과 JSON은 사용자 전용 로컬 `~/.local/share/url-shortener-ai-ops/model-campaigns/campaign-e97f582859c04d329331a7e205a8852f/`에 0600으로 보존했다. 저장소에는 원문을 복사하지 않았다.

전송 중·준비 중인 outbox가 0건임을 확인한 뒤 worker의 bridge와 캠페인·토큰 참조를 제거하고 v2 캠페인을 `stopped`로 기록했다. gateway·프록시는 복제수 0, 모델 Pod 0개이며 클러스터의 API 키·client 토큰 Secret 3개를 삭제했다. 전달기·worker는 각각 Ready 1/1, 실습 Argo 앱은 기준 revision에서 `Synced/Healthy`다. 로컬 개인 API 키, PVC 장부, 규칙/모델 보고서는 보존했다. 예정 만료 시각의 자동 동작은 실측하지 않았다.

모델 보고서는 계약·인용 검사 후에도 `needs_review`다. 이미지 취득 실패를 과거 원인 후보로 설명하는지, **CI의 원복**과 사용자 경로 복구·현재 정상 상태를 구분하는지 사람의 의미 검토가 필요하다. 한 S10 실행으로 전체 장애 진단 품질을 입증하지 않는다. B 완료·C단계의 변경 권한은 보류한다.

2026-10-26 KST에 보존한 `deployment.json`·`rollback.json`·`result.json`, GitHub 실행 40178333818의 완료/실패 상태와 모델 보고서의 각 주장을 **같은 구현 조력자가 1차로 대조**했다. 실패·Ready 0·이미지 취득 실패, 기준 버전 원복·Ready 1, 사용자 요청 경로 미검증, 미실행 조치가 근거와 일치했고 현재 정상 상태나 근본 원인을 확정하지 않았다. 다만 “이미지·Git SHA·CI 결론을 알 수 없다”는 문구는 **모델에 준 정제 입력에 없었다**는 뜻이다. 원본 CI artifact에는 이미지·revision, GitHub에는 최종 `failure`가 있으므로 보고서 표시에서 범위를 병기해야 한다. 본문의 08:53 시각도 UTC임을 명시하거나 KST로 변환해야 한다. 상세 자체 검토는 위 개인 캠페인 폴더의 `semantic-review-20261026.md`에 0600으로 보존했다. 독립 의미 평가는 여전히 미완료이며 outbox의 `needs_review`는 유지한다.

같은 날 Claude 독립 평가용으로 이전 자체 판단을 뺀 비식별 `independent-review-prompt-20261026.md`와 JSON packet을 개인 캠페인 폴더에 0600으로 준비했다. AWS 계정·저장소·자격·Webhook 원문은 포함하지 않았다. Claude 데스크톱 앱은 주간 사용 한도에 도달해 **2026-10-27 23:00 KST** 초기화 전 새 메시지를 받을 수 없으므로 자료를 전송하거나 평가 결과를 만들지 않았다. 추가 사용량 구매도 켜지 않았다. 이 시점까지 독립 평가는 미완료다. 새 클러스터용 별도 캠페인 검토안은 그보다 먼저 만료되므로 나중에 활성화를 원하면 새 검토안을 다시 만들어야 한다.

사용자는 2026-10-26 KST에 Claude 토큰 부족을 이유로 이번 독립 검토를 **생략**하기로 했다. 준비한 자료는 전송하지 않았고, 독립 평가 결과도 없다. 이 선택은 기존 모델 보고서의 `needs_review`를 통과 처리하거나 B-06 완료·운영 조치 권한을 부여하지 않는다. 앞의 자체 대조와 원본 CI 근거를 구분해 보존하고 다음 구현 작업을 진행한다.

같은 날 보고서 **표시 계층**에 `projected_ci_report_display.py`와 `render_ci_model_report.py`를 추가했다. 저장된 `live-result.json`을 덮어쓰지 않고, 계약·인용을 다시 검증한 뒤 개인 폴더에 권한 0600의 `live-result-display.html`을 새로 만든다. 화면 상단과 한계 절에 “제공되지 않음”은 **모델에 전달된 정제 입력 기준**임을 명시한다. 모델 문장의 시각은 인용한 CI 관측의 UTC 시각과 초까지 일치할 때만 KST로 표시하며 원문 UTC 시각도 병기한다. 일치하지 않으면 임의 변환하지 않고 시간대 확인 필요로 표시한다. 저장 원문·캠페인 결합·outbox 상태는 바꾸지 않는다. 보존한 실제 v2 보고서에서 08:53:38/08:53:50 UTC가 17:53:38/17:53:50 KST로 표시됨을 확인했고, 근거 없는 시각·HTML 삽입·알 수 없는 인용의 로컬 검사 3건이 통과했다. 모델 API 호출은 없었다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
