# B-06 정상 캐시 관측 한 건 — 2026-10-26 KST

판정: **격리 실습 S2의 정상 캐시 관측·원본 보존·모델용 정제 입력 생성 완료.** 관측·미리보기 준비 당시 외부 모델 호출은 0회였다. 이후 사용자가 해당 입력 한 건을 승인해 [일회 모델 비교와 자체 의미 검토](B6-normal-model-comparison-20261026.md)를 수행했다. 그 결과는 자료 부족에 맞는 유보이며 B-06 전체 완료나 서비스 전체 정상 판정은 아니다.

## 실행 범위와 절차

2026-10-26 15:26:33~15:28:07 KST에 현재 EKS `urlshortener`의 `ai-ops-lab/lab-api`에서 실행했다. 실습 Deployment 네 개가 각각 Ready 1/1, Argo `lab-target`이 Synced/Healthy이고 공유 Lease의 소유자가 없는 것을 확인했다. 수집기의 Prometheus 품질 사전 검사는 `up=1`, `scrape_health=1`, 최근 표본 10개를 반환했다. 이전 실습의 5분 지표 창이 지난 뒤 공유 잠금을 취득했다.

GitOps가 소유한 Deployment와 이미지는 변경하지 않았다. 실습 앱에 실행 ID가 고유한 아이템 **하나**를 생성하고, 그 아이템의 캐시 키만 지운 다음 cold miss→hit를 두 차례 확인했다. 각 조회는 HTTP 200이었다. 앱 자체 counter는 miss 1→2, hit 1→2로 증가했고 error·unavailable counter는 이 실행에서 출현하지 않았다. 첫 counter 부재를 숫자 0으로 저장하지 않았다. 두 번의 지표 대기 후 중립적인 질문으로 읽기 수집과 **규칙 보고서**를 저장했다. 마지막에 해당 아이템만 삭제하고 Redis PING·Ready, 운영 Deployment/AI 식별자 불변, 실습 네 Deployment의 UID·세대·이미지 불변, Argo Synced/Healthy를 확인한 뒤 공유 잠금을 해제했다.

| 산출물 | 확인 결과 |
|---|---|
| 규칙 보고서 | `partial / insufficient_evidence / unknown`, 사실 4개·원인 후보 0개. 정상 캐시 동작에 허위 장애 후보를 만들지 않았다. |
| 원본 수집 | 총 31항목 중 workload 1·설정 1·metric 4개 가용. metric 2개 누락; 로그·이벤트·배포 항목은 각각 누락/과거/제외 사유에 따라 구분한다. |
| 캐시 지표 | `cache_outcomes`에 hit·miss, `cache_hit_ratio`에 requests·ratio가 가용. Prometheus 5분 증가 추정은 hit·miss 각각 약 1.466으로 앱 직접 counter의 두 번과 측정 방식이 다르다. error·unavailable 표본은 **누락**이며 오류 0의 증거가 아니다. |
| 다른 지표 | `api_latency`와 `db_latency` 가용. 없는 다른 지표를 정상값으로 보충하지 않았다. |
| 모델용 정제 입력 | 현재 클러스터·Deployment UID·수집기 이미지·설정·감사 해시를 재대조한 exporter가 생성. `view_hash` `69c42cd606ff9dbf91642517bbca0d51a595d624ea2ff1d6816fb57e772e74bc`, 원본 bundle 해시 `b6f8e3ade2b6d14c6b7549197b01b0a722d68a48f7e8f5235d6fb2fa25813d57`. view에는 workload 1·설정 1·metric 가용 4/누락 2, 제외 항목 23개가 기록됐다. |
| 원복·보존 | 고유 아이템 삭제, 운영 및 실습 식별자 불변, Argo 정상, Lease 해제 모두 확인. 원본·감사·보고서·identity·정제 projection은 소유자 전용 폴더(0700)의 각 0600 파일에 보존했다. |

비공개 산출물은 `~/.local/share/url-shortener-ai-ops/normal-observations/s2-f3400403e890473f9f4ce7bfb9f7323b/`에 있다. Git에는 원본 snapshot·아이템 ID·전체 모델 입력을 넣지 않는다. 이번의 정답은 **고유 아이템과 해당 관측 창에서 캐시 miss→hit가 실제로 작동했다**는 제한된 사실이다. API 전체 요청 경로나 운영 앱의 현재 정상 상태는 확인하지 않았다.

## 일회 비교 미리보기 — 2026-10-26 KST

이 입력에 묶인 로컬 비교 `cmp-a221f415e489f08dcae0941d7586be9c`를 `prepared` 상태로 만들었다. 결합 SHA-256은 `a221f415e489f08dcae0941d7586be9cd1f6aa18dc2db2c41c5114290ab97ea1`이며, 정제 view 해시 `69c42cd606ff9dbf91642517bbca0d51a595d624ea2ff1d6816fb57e772e74bc`와 결합한다. [비공개 HTML 미리보기](/Users/jounghyeon/.local/share/url-shortener-ai-ops/model-comparisons/preview-cmp-a221f415e489f08dcae0941d7586be9c.html)는 토큰 계산 요청과 보고서 생성 요청의 **전체 본문**을 보여준다. 파일 권한은 0600이며 Git에 넣지 않는다. 요청 본문은 각각 7,934·8,017바이트이고 설정 상한 32,000바이트보다 작다. 미리보기의 원문 문자열 검사에서 API 키 표식·원본 실행 ID·내부 대상 이름은 발견되지 않았다.

계획은 `gpt-6-sol`의 `low` 추론, 생성 최대 1회, 건당 예약 $0.10·로컬 UTC 일일 예약 $1이다. [공식 모델 가격](https://developers.openai.com/api/docs/models/gpt-6-sol)은 입력 $2·출력 $10/백만 토큰이며 예약액은 실제 청구액이 아니다. 생성 요청은 `store=false`, `background=false`다. [공식 데이터 보관 안내](https://developers.openai.com/api/docs/guides/your-data)에 따르면 `store=false`여도 기본 악용 감시 로그에 입력·출력이 통상 최대 30일 보관될 수 있고 예외가 있다. 계정의 선택적 데이터 공유 설정은 코드에서 확인할 수 없다. **이 미리보기를 만든 시점에는** 승인·비용 예약·외부 요청 모두 0건이었다. HTML의 승인 전 안내도 당시 상태를 나타내며, 이후 결과는 [별도 기록](B6-normal-model-comparison-20261026.md)에 있다.

이것은 로컬의 **일회 비교**다. 현재 배포된 `ContinuousCIBridge`는 검증된 CI 이력만 자동 접수하므로 이 수동 S2 관측은 배포된 worker→모델 경로로 전달되지 않는다. 일회 비교에서 모델 보고서가 나와도 상시 정상 사례의 자동 처리 검증으로 계산하지 않는다. 자동 경로에 정상 사례를 넣으려면 별도의 연결 설계와 배포 검증이 필요하다.

## 전송 전 의미 평가 계획

[고정한 기준](B6-model-semantic-criteria-20261026.md)의 정상 행과 이 정제 view를 대조한다. 모델이 해당 범위의 miss→hit를 설명하고 없는 error/unavailable을 0으로 만들지 않는지, 전체 서비스 healthy·복구 완료를 주장하지 않는지 확인해야 한다. 준비 당시에는 모델 응답이 없어 **정상 모델 사례 미평가**였다. 이 로컬 미리보기로 외부 전송을 진행하려면 **위 비교 ID와 결합 해시를 지정한 별도 승인**, 공급자 보관 안내의 확인, API 계정의 선택적 데이터 공유가 꺼져 있다는 확인이 필요했다. 승인 후에도 30분 안에 실행해야 하며, 이 비교는 배포 이미지에 결합된 상시 캠페인을 대신하지 않는다. 본 수집과 미리보기 준비는 모델 승인·비용 예약·API 호출을 수행하지 않았다.

위 절은 **전송 전 준비 시점**의 기록이다. 이후 해당 입력의 한 건 승인·모델 전송과 [자체 의미 검토](B6-normal-model-comparison-20261026.md)를 마쳤다. 개별 miss→hit 순서는 정제 view에 없어 적극적인 정상 설명은 검증되지 않았다. 저장된 모델 상태는 `needs_review`이며 새 승인 없이 다시 호출하지 않는다.
