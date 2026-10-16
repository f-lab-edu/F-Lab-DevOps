# A5 ChatGPT Plus 연동 가능성 검토: 비채택 기록

- 확인일: 2026-09-30
- 결론: **로컬 실습용 대안으로 조건부 가능**, 현재 A5 코드로는 실행 불가. 사용자의 Plus 계정에 실제 이용 권한이 있는지와 `gpt-6-sol` 제공 여부는 로그인 후 확인해야 한다. EKS 상시 운영에 적용할 수 있다고 판단하지 않는다.
- 이 문서는 설계 검토다. Plus 로그인·실제 모델 호출·요금 청구는 수행하지 않았다.
- 2026-10-01 결정: 사용자는 API 키 방식을 유지하고 Plus 구독 연동은 사용하지 않는다. 아래 내용은 과거 대안 검토 기록이며 현재 A5 구현·완료 조건에 포함되지 않는다.

## 공식 지원 범위

OpenAI의 [Sign in with ChatGPT 개요](https://developers.openai.com/siwc/token-sharing-open-source)는 오픈소스·로컬 호스팅 앱이 사용자의 동의를 받아 *적격* Responses 요청에 ChatGPT 플랜 사용 권한을 신청하는 흐름을 설명한다. API 키를 만들어 Plus 구독료로 결제하는 기능은 아니다. [오픈소스 등록 흐름](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)은 별도 파트너 API 키·클라이언트 비밀값 없이 시작할 수 있지만 앱이 OAuth 로그인, ID 토큰 검증, 사용 권한 검사를 구현해야 한다. [계정·세션 문서](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)에 따르면 Plus의 5시간 사용 한도는 이 방식을 쓰는 앱들에 공유되며 앱별 별도 할당량이 아니다. 따라서 무제한 무료 API 호출을 뜻하지 않는다. 공식 개요는 유료 또는 원격 호스팅 앱에는 별도 문의를 안내한다.

[모델·추론 문서](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)는 로그인 계정에 제공되는 모델 목록을 확인하고, OAuth 접근 토큰으로 Responses API를 호출하도록 안내한다. 문서에 예시 모델명이 있어도 사용자 계정의 `gpt-6-sol` 사용 가능성을 입증하지는 않는다. 모델 목록 조회와 실제 완료 응답으로 확인해야 한다.

[미리보기 제약](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)에 따르면 HTTP 요청은 `store: false`, `stream: true`가 필요하다. `max_output_tokens`는 지원되지 않고, 함수 도구는 namespace 또는 `additional_tools` 형태로 제공해야 한다. 일부 호스팅 도구와 `previous_response_id`도 사용할 수 없다. 따라서 기존 A5 전송 코드를 OAuth 토큰만으로 바꿔서는 동작하지 않는다.

## 현재 구현과의 차이

| 항목 | 현재 A5 API 키 경로 | Plus 경로에 필요한 작업 |
|---|---|---|
| 인증 | 개인 저장소에 대화형으로 입력한 키 또는 우선 적용되는 `OPENAI_API_KEY` 환경변수 | 브라우저 로그인·동의·권한 검사·토큰 갱신·개인 저장소 내 보호된 세션 |
| 전송 | 일반 JSON 응답을 한 번에 읽음 | `stream: true` SSE를 끝까지 읽고 `response.completed`만 성공으로 처리 |
| 도구 | 함수 여섯 개를 최상위 `tools`에 선언 | 같은 A4 읽기 전용 도구를 namespace/`additional_tools`로 재선언·호출 ID 연결 |
| 출력 한도 | `max_output_tokens: 1024`와 달러 예산 사전 계산 | 해당 필드 사용 불가. 전체 시간·입력 크기·조회 횟수·모델 호출 횟수는 유지하고 플랜 사용량 한도 오류를 처리 |
| 기록 | 토큰과 표준 API 요금 기준 `cost_usd` | 토큰·플랜 사용 여부·모델을 기록. API 요금 `$0`으로 단정하지 않고 비용 필드를 미확정으로 표시하도록 계약 변경 |
| 모델 | `gpt-6-sol` 요청값 고정 | 로그인 계정의 모델 목록과 실제 추론에서 제공 여부 검증 |

증거 정규화·마스킹·A4 도구의 대상/시간 제한·실제 제공된 증거 ID 인용 검증은 인증 방식과 무관하게 유지한다. Plus 경로를 추가할 때는 인증·전송 계층만 분리하고 조사 계약을 다시 사용한다.

## 당시 적용 판단과 선택하지 않은 검증 경로

1. **로컬 A5 실습:** 현재는 API 키 방식의 실제 합성 증거 호출을 먼저 검증한다. Plus 연동을 다시 선택할 경우, 공식 로그인 절차에 맞는 클라이언트 등록·사용자 동의·권한 검사를 구현한 뒤 계정별 모델 목록을 조회한다.
2. **합성 입력 시험:** `s1-cache-error` 한 건으로 여섯 도구 중 하나를 조회하고 AI 보고서·사용량·감사 기록을 생성한다. 로그인만 성공하거나 모델 목록에 이름만 보이는 것은 통과로 보지 않는다.
3. **한도·실패 시험:** 사용량 한도, 동의 거절, 토큰 만료, 스트림 중단, 금지 도구, 허위 증거 인용을 재현한다. 실제 사용량 상한은 Plus 플랜 정책과 앱 제한을 함께 확인한다.
4. **EKS 단계:** 공식 문서에는 [자체 호스팅 VM 안내](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)가 있지만 EKS 클러스터의 무인·상시 서비스 적용을 보증하지 않는다. 원격 호스팅 자격과 토큰 보관·갱신·사용자 연결 정책을 별도로 확인한 뒤 결정한다. 이 단계에서 API 키 방식이나 다른 제공사를 대안으로 다시 비교한다.

API 키 방식은 결제 후 개발용 합성 사례 다섯 건의 실제 보고서 생성에 성공했다. 자세한 점검 결과는 [A5 엔진 문서](A5-engine-decision.md)에 남겼다. Plus 경로는 선택하지 않았고 구현·실행할 계획이 없다. A5의 실패 사용량 기록·엔진 비교는 별도로 남아 있다.
