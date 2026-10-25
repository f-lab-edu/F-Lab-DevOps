# B-06-M7 CI 실패·원복 정제 입력 v2 설계

상태: **2026-10-25 KST 설계·v2 오프라인 투영·worker/gateway 연결과 격리 CI 한 건의 실측 완료.** [투영 구현](B6-model-ci-projection-v2-implementation.md)과 [연결 구현·실측](B6-model-ci-projection-v2-connection.md)에 실제 반영 범위를 기록했다. v1 캠페인의 승인 범위를 전용하지 않고 별도 v2 승인을 받았으며, 실측 뒤 두 캠페인을 중지했다. 이 문서는 [첫 클러스터 모델 보고서](B6-model-campaign-first-live-run.md)가 원인을 설명하지 못한 이유를 좁혀 다음 구현 범위를 정했다. B단계 완료나 C단계의 변경 실행 승인이 아니다.

## 문제와 목표

[격리 CI 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40170463375)의 기존 규칙 보고서는 내부에 보존된 CI 실패·원복 증거 두 개를 인용해 이미지 변경 배포가 실패하고 원래 revision으로 자동 원복된 흐름을 설명했다. 같은 실행의 모델 입력에는 Pod 상태·설정 요약 2개만 유효했고 지표 5개는 누락됐다. `project_snapshot()`이 배포 종류를 모두 제외하므로 모델은 CI·배포 사실을 보지 못했다. 이 입력으로 원인을 맞히도록 요구하거나 규칙 보고서의 결론을 모델 입력에 복사해서는 안 된다.

v2의 목표는 **검증된 S10 실습 CI 한 실행**에 한해 최초 배포 실패와 자동 원복 검증의 유한 상태를 별도 정제 증거로 전달하는 것이다. 모델은 과거 관측의 순서를 설명하고 이미지 취득 실패를 *원인 후보*로 제시할 수 있는지 평가한다. 사용자 요청 경로 복구, 현재 전체 서비스 정상, 운영 장애 원인 확정, 자동 조치는 계속 범위 밖이다.

## 신뢰 경계와 선택 기준

1. 입구는 기존 `CIHandoff` → 큐 HMAC 확인 → `VerifiedLabHistory` → `check_snapshot()` → `validate_snapshot()` 순서를 유지한다. 외부 JSON이나 자유 텍스트가 `VerifiedLabHistory` 타입을 주장하는 것만으로 통과시키지 않는다. 모델 입력에는 규칙 보고서의 가설이나 정답 표식을 넣지 않는다.
2. `ai-ops-lab/lab-api`의 현재 Deployment UID와 실습 binding/config hash를 확인한다. 실패·원복 기록은 같은 GitHub run/attempt, artifact digest, source revision, Argo Application UID, Deployment UID여야 하며 `deployment` → `rollback` 시각 순서와 서로 다른 실패/기준 revision을 만족해야 한다. 이 실제 식별자·해시는 **로컬 결합과 감사에만** 남긴다.
3. 두 `deployment-ci-*` 증거가 모두 `available`이고, 각 `CollectionEntry`의 CI 단계·실행 출처·관측 시각·내용 해시가 검증된 기록과 일치하며 현재 조사 window 안에 있을 때만 두 항목을 포함한다. 단계 하나라도 누락·stale·invalid·권한 거부이거나 순서/대상 결합이 어긋나면 쌍을 원인 후보 근거로 승격하지 않는다. 실제 원본 상태와 제외 사유를 구분해 남긴다.
4. 이 기준은 **실습 S10 이미지 취득 실패**에만 적용한다. 운영 namespace, 일반 Argo 이력, 임의 CI 실행, 수동 JSON, 다른 실패 종류로 자동 확대하지 않는다. 원본 로그·이벤트·배포 객체·artifact와 source-to-view 대응표는 gateway나 OpenAI로 보내지 않는다.

## 외부 전달 필드 제안

기존 `ModelView.items`에 `kind=ci_deployment_state`인 정형 항목 두 개를 추가한다. 각각 별도 `ev-view-NNN`을 부여하고 기존 `ViewItem`의 `observed_at`·`collected_at`·`source_status`·`reason` 규칙을 따른다. payload는 아래의 필드만 갖는다. 값은 검증된 `LabRecord`와 짝을 이룬 수집 감사에서 **서버 코드가 계산**하며 입력 문자열을 그대로 복사하지 않는다.

| 필드 | 허용 값과 의미 | 반출하지 않는 값 |
|---|---|---|
| `stage` | `failed_deployment` 또는 `rollback_verification` | 실제 workflow 이름, run/attempt ID, branch |
| `verification_result` | 앞 단계 `failure`, 뒤 단계 `success` | CI 로그, 종료 코드 원문, 오류 메시지 |
| `revision_role` | `candidate` 또는 `baseline` | 두 Git SHA, Git URL, 이미지 태그/digest, registry |
| `ready_replicas` | 검증된 실습 앱의 `0` 또는 `1` | Pod 이름·UID·IP, 원시 Deployment 객체 |
| `image_pull_failure_observed` | 앞 단계 `true`, 뒤 단계 `false`. 앞 단계의 `ErrImagePull`/`ImagePullBackOff` 유한 목록에서 도출 | 이벤트 자유 문장, 임의 컨테이너 상태 문자열 |
| `user_path_recovery_verified` | 현재 계약에서는 `false`만. 자동 원복 검증과 사용자 경로 복구를 분리 | 사용자 URL, 요청/응답 본문 |

두 항목의 UTC `observed_at`을 원래 관측 시각 그대로 유지한다. `collected_at`은 이후 수집 시각이며 둘을 바꾸지 않는다. 모델이 볼 대상은 `cluster-1`/`service-1` 같은 별칭뿐이다. 최종 CI 표시 `failure`와 `result.json`은 현재 snapshot의 두 증거 항목에 일대일 대응하지 않으므로 **v2 첫 범위에서는 외부 전송하지 않는다**. 모델이 최종 CI 표시까지 설명했다고 평가하지 않는다.

예시는 형식 설명용이며 실제 사건·승인 입력이 아니다.

```json
[
  {"evidence_id":"ev-view-008","kind":"ci_deployment_state","source_status":"available",
   "reason":"collected","observed_at":"2026-01-01T00:00:00Z",
   "collected_at":"2026-01-01T00:02:00Z","payload":{"stage":"failed_deployment",
   "verification_result":"failure","revision_role":"candidate","ready_replicas":0,
   "image_pull_failure_observed":true,"user_path_recovery_verified":false}},
  {"evidence_id":"ev-view-009","kind":"ci_deployment_state","source_status":"available",
   "reason":"collected","observed_at":"2026-01-01T00:01:00Z",
   "collected_at":"2026-01-01T00:02:00Z","payload":{"stage":"rollback_verification",
   "verification_result":"success","revision_role":"baseline","ready_replicas":1,
   "image_pull_failure_observed":false,"user_path_recovery_verified":false}}
]
```

실제 v2 객체에는 기존 계약의 window·mapping/hash도 필수다. 예시의 시각은 가상 값이며 서비스의 현재 시각을 뜻하지 않는다.

## 계약 변경과 기존 자료 보존

- 새 `ModelView`의 `schema_version=2`, `policy_version=captured-practice-projection-v2`를 명확히 올리고 `CIStageSummary`를 엄격한 enum·boolean·0/1 범위로 정의한다. 검증된 두 CI 항목만 기존 제외 집계에서 포함 항목으로 옮긴다. `source_count = 포함 항목 수 + 제외 항목 개수 합계`와 view/source mapping·내용 해시 불변식을 유지한다. 다른 배포·로그·이벤트는 제외 집계에 남긴다.
- 쌍이 불완전하면 실제 source status를 `missing`으로 덮지 않는다. v2 제외 사유에 `ci_pair_unverified`처럼 정책상 미승격 상태를 별도로 둔다. available 단일 관측을 원복 성공 근거로 사용하거나 stale 자료를 현재 사실로 인용하지 않는다.
- `project_snapshot()`의 범용 v1 경로를 무조건 확대하지 않는다. CI 전용 투영 함수에 검증된 `VerifiedLabHistory`를 명시적으로 전달한다. 현재는 오프라인 함수만 구현했으며, 향후 worker bridge 연결 시에만 호출 경로를 연다. 수동 M3와 이미 저장한 v1 view·outbox·보고서는 원본 그대로 해석할 수 있게 분리한다. 새 함수 파일은 `implementation_hash()` 대상에 추가했다.
- 프롬프트·질문·정형 출력의 버전을 함께 올려 **과거 CI 관측만** 설명하게 한다. `image_pull_failure_observed=true`는 이미지 취득 실패의 근거이지 사용자 영향·복구 완료의 증거가 아니다. `current_state=unknown`, `analysis_status=partial`, 모든 조치 `not_executed`를 유지한다. 사실·가설은 포함된 `available` view ID만 인용해야 한다.
- `/v1/responses/input_tokens`와 `/v1/responses` 두 요청을 같은 정제 view로 구성하고 32,000바이트 제한을 각각 검사한다. 요청 본문 전체, UID/설정·정책/프롬프트/구현 해시, 새 이미지 digest, 기간·비용·키 지문을 **새 검토안**에 고정한다.

## 구현 순서와 검증 문턱

1. 실제 전송 없이 v2 계약과 CI 전용 투영을 구현한다. 기존 S10 저장 snapshot과 별도 합성 변형으로 동일 쌍·역순·다른 run/UID/artifact·SHA 불일치·stale/누락·서명/감사 불일치·크기 초과를 검사한다. 정상 쌍은 유한 필드 두 개만 포함하고 원문 image/URL/SHA/Secret 표식은 두 요청 모두에 없어야 한다.
2. 같은 v2 view를 받은 규칙 요약과 모의 모델 보고서에서 인용 ID·시각·원인 후보 표현을 비교한다. 불완전한 쌍은 판단 유보로 남기고, CI 자동 원복을 사용자 경로 복구로 바꾸는 답변은 거부한다. 계약 검사는 자연어 의미 평가를 대신하지 않으므로 별도 사람 검토 기준을 마련한다.
3. 현재 v1 캠페인의 만료·중지와 worker/gateway 정리를 확인한 뒤 새 코드를 이미지로 등록한다. 새 정책·프롬프트·이미지/입력은 기존 `campaign-6f85e38e42294e34b2efed00365e1d85` 승인 범위가 아니다. 실제 v2 정제 샘플과 두 전송 본문·가격/보관/공유 상태를 검토받고 **새 캠페인의 별도 명시 승인** 전에는 외부 전송을 켜지 않는다.
4. 승인 후에도 제한된 실습 사례에서만 규칙/모델의 동일 근거를 자체 검토하고 필요하면 독립 평가한다. 이미지 취득 실패를 과거 원인 후보로 설명하면서 자동 원복·사용자 경로·현재 상태를 분리하는지 확인한다. 비용·중복·불명 전송 보류와 기존 알림/규칙 흐름은 유지한다. 이 한 사례를 전체 장애 진단 정확도로 환산하지 않는다.

현재 캠페인에 새 입력을 보내거나 코드를 배포하지 않았다. 다음 만료·중지 확인과 새 캠페인 검토 준비 권장 추론 깊이는 **GPT-6 SOL high**다.
