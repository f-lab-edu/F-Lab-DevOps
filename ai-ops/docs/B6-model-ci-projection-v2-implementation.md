# B-06-M7 CI 실패·원복 정제 입력 v2 오프라인 구현

상태: **2026-10-25 KST 계약·투영·요청·모의 보고서 검증 완료.** 후속 [worker/gateway 연결 코드](B6-model-ci-projection-v2-connection.md)는 별도로 구현했다. 배포·실제 모델 전송은 미실행이다. [설계 기준](B6-model-ci-projection-v2-design.md)의 구현 순서 1~2 중 로컬에서 확인 가능한 부분이다. 현재 승인된 v1 캠페인의 정책과 이미지·Secret·장부를 바꾸지 않았다.

## 어떤 입력이 어떻게 바뀌는가

`model_ci_projection.py`의 `project_ci_snapshot()`은 기존 v1 `project_snapshot()`으로 대상·해시·수집 감사를 검증하고, `VerifiedLabHistory`를 CI 전달 계약과 저장 snapshot에 다시 대조한다. CI 단계의 source ID·source_ref, 실행/attempt·artifact·revision·대상 UID·관측 시각·payload가 맞는지 확인한다. 두 단계가 모두 `available`일 때만 `CIStageSummary` 두 개를 별칭 view에 추가한다. 실제 image/SHA/run ID/원본 배포 객체는 대응표를 포함한 로컬 출처 결합에만 남는다. 오래된 단계와 유효한 단계가 섞이면 두 단계 모두 제외하며 원래 source status와 `ci_pair_unverified` 사유를 구분한다. 전달 계약 자체가 불완전하거나 감사가 변조됐으면 결과를 만들지 않고 실패한다.

v2의 `schema_version=2`, `policy_version=captured-practice-projection-v2`는 별도 `CIModelView`/`CIProjection`으로 검사한다. 기존 v1 `ModelView`와 gateway 제출 계약은 v2를 거부한다. v1 원본 snapshot·정제 view·저장된 outbox·보고서를 변환하거나 덮어쓰지 않는다. `implementation_hash()`에는 v2 투영·프롬프트 모듈을 포함해 향후 이미지의 승인 결합 대상이 되도록 했다.

`projected_ci_model_engine.py`는 두 API 요청의 정제 view·지시문을 동일하게 구성하고 각각 32,000바이트를 검사한다. 프롬프트는 관측된 이미지 취득 실패를 **과거 원인 후보**로만 다루고, 자동 원복을 수행한 주체가 기존 CI임을 명시한다. 출력 검증은 포함된 `available` 근거 ID와 실패 단계의 인용을 요구한다. `current_state=unknown`, `analysis_status=partial`, 제안 조치 `not_executed` 계약을 재사용한다. 이 검증은 자연어의 실제 사실 여부를 판정하지 않으므로 의미 검토가 필요하다.

## 저장 기록과 모의 검증

`tests/test_model_ci_projection.py`는 저장소의 실제 S10 검증 기록 `docs/assets/B6-s10-ci-validation.json`을 기존 검증기·수집기에 통과시켜 만든 snapshot을 사용한다. 새 클러스터나 GitHub API를 호출하지 않는다. 원본 증거는 수정되지 않으며 정상 쌍의 실패 Ready 0·이미지 취득 실패와 원복 Ready 1이 각각 별도 view 근거로 정제된다. 두 요청 본문에서 원본 이미지·source SHA·run ID·artifact digest·Pod/Deployment 식별자·비공개 표식이 빠진 것을 검사했다.

오래된 실패/유효 원복 혼합, 누락·무효 CI 쌍, 다른 실행·artifact·UID·역순 기록, 수집 감사 변조, 원문 필드 삽입, boolean을 Ready 수로 위장, 사용자 경로 복구 `true`, 한 단계만 있는 view, v1/v2 버전 혼동, 요청 크기 초과를 거부했다. 같은 v2 view로 규칙 요약과 모의 모델 응답을 검사했고, 실패 근거를 인용하지 않은 원인 후보·`healthy` 현재 상태를 거부했다. 관련 v1 모델 투영·캠페인·gateway 경계 시험도 함께 통과했다.

검증 명령과 결과: `PYTHONPATH=ai-ops/src:ai-ops/tests <venv-python> -m unittest ai-ops/tests/test_model_ci_projection.py ai-ops/tests/test_model_projection.py ai-ops/tests/test_continuous_ci_bridge.py ai-ops/tests/test_continuous_model.py ai-ops/tests/test_continuous_model_live.py ai-ops/tests/test_model_gateway_contract.py ai-ops/tests/test_model_gateway_boundary.py ai-ops/tests/test_model_campaign_review.py -q`에서 40건 중 39건이 먼저 통과했다. 나머지 gateway 검사는 작업 환경의 로컬 포트 바인딩 거부로 실행되지 못했고 같은 검사만 바인딩 가능한 권한에서 재실행해 **통과**했다. 문법 검사와 `git diff --check`도 통과했다. 로컬 가상환경 Pydantic은 2.13.3이므로 잠금 의존성 2.13.5를 사용하는 원격 CI 결과는 별도로 확인한다.

## 아직 연결하지 않은 경계

- 이 문서에서 기록한 오프라인 구현 시점에는 worker bridge·gateway·장부가 v1만 사용했다. 후속 연결 코드가 main에 반영돼도 배포 전에는 현재 클러스터가 계속 v1이며, 새 캠페인 승인 전 v2 전송은 금지한다.
- 최초 캠페인은 2026-10-26 15:38:43 KST까지 승인된 v1 입력 범위다. 새 프롬프트·정책·이미지는 그 승인으로 활성화할 수 없다. 새 연결 전에 v1 캠페인 만료·중지와 worker/gateway 자격·Pod 정리를 실제 확인한다.
- 후속 [v2 연결 구현](B6-model-ci-projection-v2-connection.md)은 worker/gateway 결합·미리보기·새 캠페인 승인 경로를 코드로 마련했다. 실제 전송 본문·키 지문·비용·보관의 새 검토와 승인 전에는 토큰 계산을 포함한 외부 모델 요청을 하지 않는다.
- 스키마 인용 검사는 AI가 사용자 경로 복구나 현재 서비스 정상이라고 **자연어로** 거짓 주장하는 것을 완전히 판별하지 못한다. 실제 모델 결과는 `needs_review`에서 사람의 의미 검토를 거친다. S10 한 건과 변형 시험은 전체 장애 진단 품질의 증거가 아니다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
