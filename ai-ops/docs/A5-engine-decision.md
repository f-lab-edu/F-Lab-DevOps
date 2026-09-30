# A5 엔진 선정과 실제 AI 연결: 진행 중

- 확인일: 2026-09-30
- 범위: 로컬 합성 증거를 대상으로 한 읽기 전용 조사. 라이브 EKS 조회·운영 조치·프로덕션 데이터 전송은 포함하지 않는다.
- 상태: Responses 어댑터와 모의 응답 검증 완료. **실제 유료 호출과 HolmesGPT 4~6건 실행 비교는 미완료**다.

사용자가 별도 API 과금보다 Plus 활용을 우선 검토하기로 하여 [ChatGPT Plus 연동 가능성](A5-plus-feasibility.md)을 추가했다. 공식 문서상 로컬 오픈소스 앱의 적격 요청에는 경로가 있으나 현재 API 키 어댑터와 요청 형식이 달라 미구현이다. 계정 권한·모델 제공 여부를 확인하기 전에는 Plus 사용 가능이나 A5 완료로 표시하지 않는다. 아래 API 요금·예산 설정은 **기존 API 키 경로의 설정**이며 Plus 경로의 결제·사용량 조건으로 간주하지 않는다.

## 엔진 결정 기록

HolmesGPT를 우선 조사했다. [공식 Python SDK](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/reference/python-sdk.md)는 `additional_toolsets`로 사용자 Python 도구를 주입하고 `max_steps`, 태그 필터, 자동 도구 활성화 여부를 설정할 수 있다. 따라서 현재 자료만으로 HolmesGPT가 요구를 충족하지 못한다고 단정하지 않는다. 다만 A4의 정확한 여섯 조회 함수·입력 경계·실제 전달 증거 ID·전체 시간·건당 비용을 기존 실행기에서 일관되게 강제하는 방식은 설치 버전으로 검증되지 않았다. [공식 사용자 도구 문서](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/data-sources/custom-toolsets.md)의 YAML 도구는 셸 명령을 사용하므로 이 프로젝트의 읽기 전용 경계에는 그대로 연결하지 않는다.

현 로컬 단계에서는 작은 **OpenAI Responses 어댑터** 하나를 채택한다. [Responses 함수 호출 문서](https://developers.openai.com/api/docs/guides/function-calling)에 따라 모델에는 A4 함수 여섯 개만 공개하고, 실행은 A4의 `query_bundle`과 `run_replay`가 담당한다. 모델에 임의 shell·Kubernetes 쓰기·호스팅 도구를 제공하지 않는다. 이 선택은 현재 코드 경계를 단순하게 검증할 수 있다는 설계 판단이며 HolmesGPT의 기능 부재를 입증한 결과가 아니다. B 단계에서 엔진을 클러스터에 배포하기 전 HolmesGPT 설치 버전과 다섯 개발 입력(`s1-cache-error`, `s2-normal-miss`, `s3-image-tag`, `s4-db-latency`, `s5-insufficient-data`)을 동일한 기준으로 다시 비교한다. 고정 최종 평가 입력은 이 결정에 사용하지 않는다.

## 기존 API 키 경로의 로컬 설정

| 항목 | 설정 |
|---|---|
| 제공사·API | OpenAI Responses API, `store: false` |
| 모델 | `gpt-6-sol`, 낮은 추론 깊이. 실제 응답의 모델 문자열도 기록 |
| 계정 | 사용자 API 계정 제공 예정. 실행 시 `--ai-account` 별칭과 `OPENAI_API_KEY` 환경변수 필요 |
| 전송 범위 | `practice` 대상의 `synthetic` 증거만. 질문은 기존 A2 마스킹 후 전송 |
| 가격 가정 | 2026-09-30 [공식 모델 페이지](https://developers.openai.com/api/docs/models/gpt-6-sol)의 표준 텍스트 요금: 입력 $2/백만 토큰, 출력 $10/백만 토큰. 실제 청구액과 보고서 추정액은 다를 수 있음 |
| 예산 | 사용자 지정: 건당 최대 $0.10, 로컬 저장소 기준 UTC 하루 최대 $1. 새 실행마다 건당 상한액을 선예약하고 실패 시에도 돌려주지 않음 |
| 호출 제한 | 요청 본문 16,000바이트, 응답 출력 최대 1,024토큰, 모델 응답 최대 5회, 전체 120초, 조회 도구 10회·개별 10초, 일시적 모델 오류 재시도 1회 |

입력 바이트 수와 출력 토큰 상한을 사용한 호출 전 비용 계산은 보수적인 **추정**이다. 토큰화·캐시·요금 변경으로 절대적인 청구 상한을 보장하지는 않는다. 응답 usage를 읽어 같은 표준 단가로 추정 비용을 기록하며 usage가 없으면 성공으로 처리하지 않는다. 일일 예산 파일 `ai-budget.json`은 개인 `--store` 안에 0600으로 둔다. 같은 저장소에서 계정 별칭을 바꿔도 UTC 날짜별 한도가 공유된다.

## 처리 흐름과 검증

`investigate --mode ai`는 등록 대상·묶음 hash·질문을 검증하고 일일 예산을 선예약한다. 모델은 사건 질문과 증거 카탈로그를 먼저 받는다. 함수 호출을 선택하면 A4가 범위·인자·런북 hash를 확인해 반환하며, 이후 호출에서 실제 제공된 증거와 런북만 모델이 본다. 최종 JSON은 `AnalysisReport` 계약으로 검증한다. 관측 사실, 원인 가설, 반대 근거, 누락 자료, 추가 확인, 미실행 제안을 구분하고, 모델이 실제 전달받지 않은 증거 ID를 인용하면 실패시킨다. JSON 내부 시각은 UTC, 사용자용 Markdown 표시는 KST다.

성공 시 개인 저장소에 `input.json`, `tool-audit.json`, `report.json`, `report.md`, `metadata.json`을 남긴다. 보고서에는 모델·프롬프트·도구·런북 버전, 계정 별칭, 가격표 날짜, 입력·출력 토큰과 추정 비용을 포함한다. 실패 시에는 사유와 확보한 조회 감사 기록을 남기며, 모델 응답 원문이나 API 키를 저장하지 않는다.

```sh
# 개인 저장소 경로는 저장소 바깥에 지정한다. 키 값은 파일·명령행 인자로 남기지 않는다.
PYTHONPATH=ai-ops/src python3 -m ai_ops investigate \
  --mode ai --ai-account personal-openai \
  --bundle ai-ops/evals/fixtures/dev/s1-cache-error \
  --registry ai-ops/config/targets.example.json \
  --store /개인/비공개/ai-ops-store \
  --question "캐시 오류를 조사해줘" --request-id a5-s1-first
```

현재 API 키가 없어 실제 호출은 실행하지 않았다. 모의 응답으로 도구 조회→실제 전달 ID 인용→보고서 재조회, 허위 인용 거부, 건당·일일 예산의 호출 전 거부를 확인했다. 이 시험은 모델 품질이나 외부 API 호환성의 증거가 아니다. Plus 로컬 경로의 계정 자격·요청 형식 검증을 우선 진행한다. API 키 유료 호출은 사용자가 해당 경로를 선택하기 전까지 보류한다. 이어 다섯 개발 입력으로 HolmesGPT의 제한 설정을 검증한 뒤 엔진 결정을 확정한다.
