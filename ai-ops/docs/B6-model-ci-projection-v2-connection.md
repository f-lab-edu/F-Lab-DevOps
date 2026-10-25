# B-06-M7 CI 실패·원복 v2 캠페인 연결 구현

상태: **2026-10-25 KST 코드 경로·모의 검증과 v1 조기 중지 완료, v2 이미지 등록 완료. v2 배포·새 캠페인 승인·실제 전송 미실행.** [v2 오프라인 투영](B6-model-ci-projection-v2-implementation.md)의 정제 결과를 기존 worker/gateway 장부에 버전별로 연결한다. v1 보고서·장부와 실습 앱은 보존했다.

## 동작 흐름과 버전 경계

1. `ContinuousModelStore.prepare_live_campaign(policy=...)`은 정책 v1/v2에 맞는 프롬프트 버전·구현 해시·이미지 digest·대상 UID·config hash·cluster fingerprint·샘플 view hash·API 키 지문·기간/건수/비용을 새 캠페인 binding에 묶는다. 알 수 없는 정책 버전은 거부하고 기본 v1로 낮추지 않는다.
2. `build_review_packet()`은 binding의 정책에 맞는 view 계약을 다시 검사하고 토큰 계산·생성 요청 **두 본문 전체**, 각 해시·바이트 수·비용·보관/공유 확인 사항을 준비 상태로 보여준다. 이 검토안 생성은 승인이 아니며 API도 호출하지 않는다.
3. 별도 명시 승인이 있다면 `build_activation_patches()`가 worker의 `AI_MODEL_POLICY`를 binding 정책으로 지정한다. worker의 `ContinuousCIBridge`는 큐 HMAC·CI 출처·규칙 보고서·snapshot을 확인한 뒤 v1은 기존 투영, v2는 검증된 CI 실패·원복 쌍의 전용 투영을 선택한다. v1 캠페인은 기존 입력을 계속 쓰며 v2 입력으로 자동 전환되지 않는다.
4. worker의 `ModelGatewayClient`는 v1 `ModelSubmission` 또는 v2 `CIModelSubmission`을 선택해 같은 서비스 토큰으로 정규 JSON에 서명한다. gateway는 **서명·schema 버전·view 계약·정규 JSON**을 확인한다. 장부는 캠페인 정책/프롬프트/구현 해시와 제출 view 정책을 다시 대조한 뒤에만 outbox를 만든다. v1↔v2 교차 제출은 예약이나 외부 요청 이전에 거부한다.
5. `ModelGatewayRuntime`과 장부는 각 처리 직전 승인 기간·현재 대상/자격·이미지·정책을 확인한다. 승인된 v2 outbox만 v2 프롬프트로 토큰 계산과 생성을 할 수 있다. 기존 건당 $0.10·UTC 하루 $1 예약, 10건, 중복 방지, 불명 전송 재시도 금지, `needs_review` 저장을 유지한다. 이 문서는 실제 모델 호출 성공을 주장하지 않는다.

## 검증과 남은 조건

`tests/test_model_ci_connection.py`는 저장 S10 이력에서 만든 정제 입력으로 v2 worker→outbox, 서명/버전 계약, 전체 요청을 담은 준비 검토안, 활성 패치의 정책값, 모의 모델 처리와 교차 정책 거부를 확인한다. 새 검사와 관련 v1 회귀 **39건**은 로컬에서 통과했다. 로컬 포트를 쓰는 기존 gateway 경계 **8건**도 외부 API 호출 없이 통과했다. 문법 검사와 `git diff --check`가 통과했으며, 잠금 의존성 기준 원격 CI 결과는 PR에서 확인한다.

v1 캠페인은 승인 기간 만료 전에 조기 중지했고 worker bridge 해제, gateway/proxy 복제수 0, 관련 Secret 삭제, 규칙 worker·전달기와 실습 앱 정상 상태를 [실측](B6-model-campaign-first-live-run.md#2026-10-25-kst-조기-중지-실측)했다. 실제 만료 시점의 자동 동작은 실측하지 않았다. [새 이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40174117827)은 main `c011d618b5581c3b5110ccb16a9dae69f627ef3a`를 digest `sha256:c33b7bfb3cbe82e58eabc5a285e5a58a6d5ba610eee98d166c1fda582dc09f2e`로 ECR에 등록했고 검사 상태 `COMPLETE`, 심각도별 발견 0건이었다. **클러스터에는 배포하지 않았다.**

새 캠페인은 실제 검증 CI의 v2 정제 샘플·두 요청 본문·[공식 `gpt-6-sol` 가격](https://developers.openai.com/api/docs/models/gpt-6-sol)·API 계정 공유 설정·보관 조건과 새 이미지/코드 해시를 함께 검토한다. 사용자에게 새 캠페인 활성화를 별도로 승인받기 전에는 v2 Secret 마운트, worker 연결, 토큰 계산, 모델 생성을 시작하지 않는다. 기존 v1 승인과 이번 구현 지시는 새 자료의 자동 전송 승인이 아니다.

2026-10-25 KST에는 [이미 복구가 확인된 격리 CI 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40170463375)의 저장 snapshot과 큐 HMAC 이력을 재검증해 **과거 관측 시각 그대로** v2 샘플을 만들었다. 허용 항목 9개 중 CI 실패·원복 유한 상태 2개가 포함됐고 나머지 26개 자료는 제외로 집계했다. `view_hash`는 `a8b6f471a6fc1d1bca4e822d01339234fe12da37096a552c7058eed40eb10c3b`이다. 새 장애 실행 40174316757은 중복 시험을 피하려고 승인 전 취소했다. 새 CI가 실제로 v2 outbox를 만드는지는 별도 승인 후 한 번 확인해야 한다.

비공개 검토안은 `/private/tmp/m7-v2-review-20261025/review.html`에 두 외부 요청 본문 전체와 예산·보관·자동 입력 범위를 표시했다. 로컬 장부의 `campaign-e97f582859c04d329331a7e205a8852f`는 **prepared**이며 결합 해시는 `c74a534c25cfe19b7d1705875a1f05f24c04e5336bf758fbe62dc1b9256ae91d`, 만료는 2026-10-26 16:57:23 KST다. 이는 승인이나 클러스터 장부 등록이 아니다. 로컬 준비 행을 기존 v1 장부에 같은 ID·해시로 추가하는 절차는 빈 로컬 장부에서 검증했고, 실제 가져오기는 새 캠페인 승인 후에만 한다. 가격은 2026-10-25 UTC [OpenAI 공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-6-sol)의 100만 토큰당 입력 $2, 캐시 입력 $0.20, 캐시 쓰기 $2.50, 출력 $10을 기준으로 묶었다. 비공개 입력·자격 원문·전체 전송 본문은 Git에 넣지 않는다.

모델 보고서는 계약·인용 검사 후에도 `needs_review`다. 이미지 취득 실패를 과거 원인 후보로 설명하는지, **CI의 원복**과 사용자 경로 복구·현재 정상 상태를 구분하는지 사람의 의미 검토가 필요하다. 한 S10 실행으로 전체 장애 진단 품질을 입증하지 않는다. B 완료·C단계의 변경 권한은 보류한다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
