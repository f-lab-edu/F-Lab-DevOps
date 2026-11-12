# C-04 복구 결과 검증과 실패 처리

[준비 완료 후 승인 시작](C4-ready-start-local-20261112.md)과 [장애 주입 소유권 인계](C4-failure-handoff.md)를 사용한다. 실제 C-03 동기화·정상 결과를 인계받은 뒤 새 관찰 창을 수집한다. 현재 정상 상태나 과거 자료만으로 복구 성공을 만들지 않는다.

동작 흐름은 **C-03 결과 연결 → 대상 UID·revision·이미지 대조 → 같은 관찰 창의 런타임·사용자 요청·지표·경보 검사 → 해결/미해결/검증불가 보고 → 실패 시 중지 또는 후속 범위 비교**다. 자료의 hash는 동일성을 확인하며 수집 출처나 승인을 인증하지 않는다.

## 파일과 책임

| 파일 | 구현한 내용 |
|---|---|
| `src/ai_ops/c_verification.py` | 입력 계약, 관찰 창·UID·지표 조회식/경보 규칙 hash 검사, 세 가지 판정, 실패 시 후속 범위 비교 |
| `src/ai_ops/c_verification_archive.py` | 보존한 C-03 제안·동기화·런타임·HTTP 자료의 동일성 검사와 C-04 입력 연결 |
| `scripts/verify_c4_recovery.py` | 등록된 로컬 폴더만 읽는 CLI, JSON과 그 안의 한글 Markdown 보고서, KST 표시 |
| `src/ai_ops/c_verification_collect.py` | C-03 결합·수집 계획, 소유 관계와 수집 전후 대상 대조, 실제 관찰 창, HTTP 계약·Prometheus 신선도·경보 검사 |
| `src/ai_ops/c_verification_transport.py` | 고정 EKS 문맥의 kubectl GET, 임시 127.0.0.1 포트 전달, 크기·기한 제한 HTTP GET과 프로세스 정리 |
| `scripts/collect_c4_recovery.py` | 기본 대상 대조 또는 명시한 시간 관찰을 수행하고 검증 입력·감사·판정 보고서를 함께 저장 |
| `config/c4-collection-plan.example.json` | 합성 UID·해시, 실행 시각 null·경보 미설정인 수집 계획 예시. 그대로 실수집할 수 없음 |
| `config/c4-verification.example.json` | 합성 정상 관찰 표본. 실제 실습이나 운영 성공 근거로 사용하지 않음 |
| `config/c4-verification-policy.example.json` | 보존 자료 연결용 예시 기준. 시각은 null이며 실제 수집 없이 채우지 않음 |
| `schemas/c4-*.schema.json`·`scripts/export_schemas.py` | C-04 검증 입력과 후속 범위의 JSON Schema 생성 |
| `tests/test_c_verification.py` | 정상/불일치/누락/수집 실패/시간/권한 범위/CLI 검증 |
| `tests/test_c_verification_collect.py` | 수집기·UID 변경·시각·자료 누락 검증 |
| `src/ai_ops/c_verification_handoff.py` | C-03 실제 검증 함수를 호출한 뒤 시각·결과/스냅샷/연결 hash를 묶는 인계 함수 |
| `src/ai_ops/c_verification_prepare.py`·`scripts/prepare_c4_collection.py` | 실제 경보 정의 고정, 현재 대상 대조, 실행 이후 자료의 준비 대기, 수집 계획 생성 |
| `schemas/c03-c04-handoff.schema.json`·`tests/test_c_verification_handoff.py` | 인계 계약·실행 거부·기한·대상 교체·준비 대기 검증 |

## 판정 기준

| 판정 | 의미 |
|---|---|
| `resolved` · 해결 | 모든 필수 항목이 정해진 대상·관찰 창에서 통과함 |
| `unresolved` · 미해결 | 올바른 대상·시각에 회복 조건을 충족하지 못한 표본이 있음. 다른 자료가 부족해도 확인된 실패를 숨기지 않음 |
| `inconclusive` · 검증불가 | 확인된 실패는 없지만 필수 자료·시각·표본·정체성 등을 확인할 수 없음 |

확인 대상은 같은 `ai-ops-lab/lab-api` 이름뿐 아니라 Deployment와 Application UID까지 연결한다. 런타임은 목표 revision·Argo 작업의 적용 revision·Synced/Healthy/Succeeded·최신 observedGeneration·실습 Pod 한 개의 준비 상태와 실제 이미지 digest가 모두 일치해야 한다. HTTP는 `/readyz`와 `/items`의 200 및 응답 계약 통과를 요구한다. 기존 보존 HTTP 200에는 응답 계약 검사 결과가 없어 이를 임의로 true로 보충하지 않는다.

지표는 명시한 조회식·대상 조건의 hash와 상한/하한을 대조한다. 값 없음은 0이 아니다. 경보는 규칙 존재, 정상 평가, 최신 평가 시각과 활성 경보 0을 함께 요구한다. 빈 ALERTS 결과나 조회 실패만으로 경보가 해제됐다고 판단하지 않는다. 예시의 `error_rate <= 0.05`와 `WorkloadUnavailable`은 **합성 시험용**이며 현재 클러스터의 실제 SLO·경보 설정을 뜻하지 않는다. 실시간 연결 전 실제 기준과 조회식을 고정해야 한다.

기본 관찰 기준은 최소 120초·표본 4개·최대 표본 간격 45초다. 관찰 창은 최대 15분이고 완료 후 120초를 넘긴 자료는 새 판정에 사용하지 않는다. 최초/마지막 표본의 창 경계 접근, 실제 표본의 전체 시간 폭, 중복·역순·관측 공백을 함께 검사한다. 이는 **표본으로 확인한 관찰 창**이며 두 표본 사이의 연속 가용성을 입증하지 않는다. 미래 시각·실행 검증 전 표본·다른 창의 자료·수집 실패·계획에 없는 항목은 해결 판정을 막는다.

## 실패 후 처리

기본 동작은 `stop_and_report`다. `plan_follow_up()`은 제공된 기존 범위의 제안 ID/hash·C-03 결과 hash·대상 UID·revision·digest·유효기간·취소 여부·1회 제한을 대조한다.

- 검증불가 또는 필수 자료 확인 불가가 남으면 자료 수집을 우선하며 변경 후보를 허용하지 않는다.
- 다른 대상·이미지·revision이거나 범위가 없고 만료·취소된 경우 `request_new_approval`로 기록한다.
- 최신 미해결 결과와 같은 변경이 남은 유효 범위에 일치하면 `same_change_candidate`로 기록한다.
- 모든 결과에서 **`execution_allowed=false`**다. 후보 판정은 실제 승인 원본 인증·Lease 취득·실행 직전 재검증을 대체하지 않는다. 쓰기 adapter와 자동 재시도는 구현하지 않았다. `attempts_used`도 로컬 비교 입력이므로 승인 저장소의 진위 확인과 실행 횟수의 원자적 차감은 실시간 실행기 연결 때 필요하다.

## 로컬 실행

의존성은 기존 `requirements.lock`을 사용한다. `AI_OPS_FILES_ROOT`에는 저장소와 분리된 사용자 전용 0700 폴더를 지정한다. 기존 출력은 덮어쓰지 않는다.

```sh
mkdir -m 700 /private/tmp/c04-review
export AI_OPS_FILES_ROOT=/private/tmp/c04-review PYTHONPATH=ai-ops/src
python ai-ops/scripts/verify_c4_recovery.py \
  --input ai-ops/config/c4-verification.example.json \
  --output /private/tmp/c04-review/synthetic.json

python ai-ops/scripts/verify_c4_recovery.py \
  --c03-archive "$HOME/.local/share/url-shortener-ai-ops/cleanup-20261110/c03" \
  --policy ai-ops/config/c4-verification-policy.example.json \
  --output /private/tmp/c04-review/archived.json
```

종료 코드는 해결 0, 미해결/검증불가 1, 입력·경로 거부 2다. 한글 보고서는 결과 JSON의 `report_markdown`이다. 내부 시각·hash는 UTC, 사람이 보는 보고서 시각은 KST로 표시한다. 입력에 포함된 `evaluated_at`은 재현 시각이며 CLI가 실시간 관측을 수행했다는 뜻이 아니다.

## 로컬 검증

전체 시험은 저장소 소스에서 직접 실행한다. 합성 표본의 해결 판정은 실제 복구 성공을 뜻하지 않는다. 실환경에서는 새 C-03 인계, 실제 대상과 규칙 대조, 120초 이상 관찰과 누락/오류 검증을 별도로 통과해야 한다.

## 읽기 수집기 연결

**실행 영수증 입력 → Application·Deployment UID/출처 대조 → 소유한 Pod의 런타임·실제 이미지 확인 → 임시 포트 전달로 HTTP·Prometheus 조회 → 수집 전후 정체성 재확인 → C-04 판정 보고** 순서다. 기본 실행은 첫 대상 대조까지만 하며 `--collect`를 명시해야 반복 관찰을 수행한다. 기존 운영자 kubeconfig의 신뢰와 권한을 사용하며 새 RBAC나 자격을 만들지 않는다. 로컬 영수증 파일은 인증된 C-03 원본을 전달받는 경계이며 해시만으로 실행·승인을 인증하지 않는다.

- Kubernetes GET은 새 계정 `764428911194`의 서울 EKS `urlshortener`, `argocd/lab-target`, `ai-ops-lab/lab-api`, 해당 selector의 ReplicaSet·Pod와 고정 Prometheus Pod에 한정한다. Deployment → ReplicaSet → Pod의 controller UID를 대조하며 잘린 목록은 거부한다. Application의 저장소·`ai-ops/lab` 경로·destination도 고정한다.
- 대상 이름이 같아도 UID가 바뀌면 중지한다. 관찰 도중 소스·Deployment generation/template·Pod UID·컨테이너 ID·재시작 수가 바뀌면 그 창의 앞선 성공 표본도 무효화한다. Prometheus 재생성·재시작도 지표·경보 수집 실패로 남긴다.
- 앱의 외부 수집 경로가 없으므로 기존 `pods/portforward` 권한으로 **소유 관계를 확인한 Pod**에만 임시 루프백 포트 전달을 연다. 앱 8000, 고정 Prometheus Pod 9090 외에는 허용하지 않는다. 완료·예외 시 프로세스를 정리하고 리다이렉트·환경 HTTP 프록시·압축 응답을 사용하지 않는다. 응답 크기는 2MiB, 개별 GET/포트 전달은 12~15초 이내, 전체 관찰 예산은 설정 시간+120초다. 종료 정리에는 수 초가 추가될 수 있다.
- `/readyz`는 HTTP 200과 `{"status":"ready"}`, `/items`는 HTTP 200과 아이템 목록의 응답 구조를 검사한다. 빈 목록도 유효하다. 본문·아이템 내용은 결과에 저장하지 않는다. GET도 캐시를 채우거나 조회·요청 지표를 증가시킬 수 있으며 이를 업무 데이터 쓰기나 캐시 무변경으로 표현하지 않는다. POST·DELETE·exec·장애 헤더는 사용하지 않는다.
- 지표는 `up{namespace="ai-ops-lab",service="lab-api"}`와 `kube_deployment_status_replicas_available{namespace="ai-ops-lab",deployment="lab-api"}`의 값이 각각 1 이상인지 확인한다. up의 Pod label은 선택한 Pod 이름과 같아야 한다. 두 조회식과 대상 조건을 해시로 고정한다. **오류율 SLO 검증은 현재 범위가 아니다.**
- 각 지표에 `timestamp(동일 조회식)`을 함께 요청해 원래 수집 시각을 확인한다. 평가 시각만 최신인 오래된 값·C-03 검증 전 값·빈 결과·중복 series·다른 label·NaN/Infinity·warning/info 응답은 성공으로 바꾸지 않는다.
- 경보는 실제 `KubeDeploymentReplicasMismatch` 규칙의 `query/labels/duration/keepFiringFor/group/file`을 계획의 `alert`에 명시해야 한다. 규칙 존재를 가정하거나 자동 생성하지 않는다. 원본 정의가 변경되거나 조회가 부분 응답이면 검증불가다. 최신 정상 평가·해당 대상의 pending/firing 0개 외에도 spec replicas와 generation/observed generation의 실제 KSM 지표를 확인해, 자료가 없어 경보 목록이 빈 경우를 통과시키지 않는다. 실행 전 평가도 사용하지 않는다.
- 기본 최소 120초·30초 간격·4개 이상 표본, 최대 관측 공백 90초·원래 지표/경보 평가의 최대 나이 60초다. API 지연을 고려해 **각 종류의 실제 표본 시간 폭**이 120초 이상이어야 종료한다. 누락이 한 번이라도 있으면 해당 자료 종류를 수집 실패로 유지한다. 수집 예산 초과나 대상 교체 시 해결로 판정하지 않는다.

이 수집기는 운영자 Mac용이다. 클러스터에 배포한 worker의 자동 실행 경로나 제한된 C-02 조회 ServiceAccount의 권한을 확대하지 않았다. 이미지는 등록하지 않았고 쓰기 실행기는 연결하지 않았다.

### 수집 계획과 실행 방법

실제 C-03 결과의 `execution`을 사용하고 실제 규칙 정의를 고정한 뒤 개인 폴더에 계획을 저장한다. `verified_at`은 C-03 실행기가 동기화를 확인한 실제 UTC 시각이어야 한다. 과거 기록의 null을 현재 시각으로 채우거나 새 UID로 고쳐서는 안 된다. `alert=null`인 공개 예시는 사전 검토용이며 실제 관찰을 차단한다.

```sh
export AI_OPS_FILES_ROOT=/private/tmp/c04-review PYTHONPATH=ai-ops/src
# 기존 설치 때 연결한 운영자 AWS 프로필·kubeconfig를 사용한다. 새 자격을 만들지 않는다.
python ai-ops/scripts/collect_c4_recovery.py \
  --plan /private/tmp/c04-review/collection-plan.json \
  --output /private/tmp/c04-review/preflight.json

# 대상 대조와 실제 규칙 고정 후에만 반복 관찰한다. 같은 출력은 덮어쓰지 않는다.
python ai-ops/scripts/collect_c4_recovery.py \
  --plan /private/tmp/c04-review/collection-plan.json --collect \
  --output /private/tmp/c04-review/collected.json
```

출력에는 `input`, `collection`, `verification`, `report_markdown`이 있으며 UTC 내부 기록과 KST 보고 표시를 유지한다. 종료 코드 0/1/2의 의미는 로컬 검증기와 같다. 읽기 거부·만료 자격·시간/정체성 불일치는 원본 오류나 비밀값을 출력하지 않고 감사 사유와 검증불가로 남긴다.

## C-03 시각 포함 인계와 실제 경보 고정

### 실행기가 인계 기록을 만드는 시점

`verify_c03_handoff()`는 기존 C-03의 `verify_sync_result()`를 직접 호출한다. GitHub 병합 SHA·고정 Argo revision·Deployment 이미지·현재 Lease 검증이 성공한 뒤 실제 완료 UTC 시각을 `verified_at`으로 기록한다. 검증 전후 시계가 역행하거나 스냅샷 관측 후 15초를 넘기면 인계를 거부한다. `service_resolved`는 계속 false다. C-04가 서비스 회복을 별도로 판단한다.

인계에는 proposal ID/hash·대상 UID·merge SHA·digest와 C-03 결과·스냅샷·PR 연결 hash를 묶는다. 시각과 실행 내용도 결합 hash에 포함하므로 다른 SHA/대상/시각을 실수로 섞으면 거부한다. **이 hash는 전자서명이나 GitHub 승인이 아니다.** 신뢰 경계는 기존 실행기 프로세스와 사용자 전용 파일이다. 기존 보존 JSON에 현재 시각을 채우는 CLI는 제공하지 않는다.

기존 실행기에서 동기화 결과를 검증하는 위치에 다음 연결점을 사용한다. 아래 인자는 기존 C-03 실행기가 확보한 원본이며 파일의 주장으로 대신하지 않는다.

```python
from ai_ops.c_verification_handoff import verify_c03_handoff
from ai_ops.path_policy import write_private_json

handoff = verify_c03_handoff(
    reader=reader, snapshot=snapshot, locked=locked,
    proposal=proposal, pr_link=pr_link, merge_response=merge_response,
)
write_private_json(handoff_path, handoff.model_dump(mode="json"))
# 이후 기존 실행기의 잠금 해제 절차를 마친 다음 수집 준비·관찰로 넘어간다.
```

인계가 호출하는 C-03 승인·CI·병합·정확한 동기화 모듈을 이 저장소에 함께 포함했다. 필요한 모듈이 없으면 통합 시험을 건너뛰지 않고 실패하도록 한다.

### 현재 대상과 경보를 확인하는 준비 명령

```sh
# 인계가 없어도 현재 UID·실제 경보 정의를 확인한다. 수집 계획은 생성하지 않는다.
python ai-ops/scripts/prepare_c4_collection.py \
  --output /private/tmp/c04-review/preparation-only.json

# 실행기가 새로 만든 인계가 있을 때만 현재 대상과 대조해 수집 계획을 만든다.
python ai-ops/scripts/prepare_c4_collection.py \
  --handoff /private/tmp/c04-review/c03-handoff.json \
  --output /private/tmp/c04-review/prepared.json

python ai-ops/scripts/collect_c4_recovery.py \
  --plan /private/tmp/c04-review/prepared.json --collect \
  --output /private/tmp/c04-review/recovery-observed.json
```

준비 상태는 `ready`, `waiting_for_c03_handoff`, `blocked`다. 인계가 없으면 `collection_plan=null`이고 `collect_c4_recovery.py`도 이 결과로 수집을 시작하지 않는다. 인계가 있으면 UID·저장소·source/applied revision·이미지를 현재 상태와 대조한다. 새 scrape와 경보 평가가 인계 시각 이후 자료로 바뀔 때까지 최대 약 90초간 읽기 대기하며, 준비 대기는 C-04 관찰 표본이나 회복 시간 폭으로 세지 않는다. 누락·정의 불명·평가 오류를 정상으로 채우지 않는다. 이후 새 관찰 창의 최소 120초는 별도로 확보해야 한다.

실제 규칙의 조회식은 `spec_replicas > available_replicas`와 최근 10분 `changes(updated_replicas)==0`을 결합하며 `for=900`초다. 따라서 available·spec·updated와 generation 계열의 최신 원래 표본 및 updated의 최소 두 이력 표본을 확인하도록 경보 검사도 보완했다. 경보가 없다는 사실 하나로 15분 안정성이나 오류율 SLO를 입증하지 않는다.

새 실행의 결과는 그 실행의 실제 인계·관찰 기록으로 판단한다. 과거 실습의 시각·UID·리뷰를 재사용하지 않는다.
