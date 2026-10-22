# B-06 S10-05 CI 보고서 해석 검증

상태: **CI 전용 규칙 보고서·저장/재조회 구현 및 오프라인 검증 완료**. 같은 실행의 유효한 두 관측은 모의 API/시계로, 실제 기록의 제외와 한계 설명은 S10-04 저장 snapshot으로 확인했다. 새로운 실환경 장애 실험이나 최신 CI 두 관측의 실시간 보고서 검증을 수행한 것은 아니다.

## 무엇을 구현한 단계인가

[S10-03](B6-ci-experiment.md)이 실제 이미지 오류와 CI 자동 원복 기록을 만들었고, [S10-04](B6-ci-history-import.md)가 그 출처와 현재 실습 대상의 연결을 검증했다. S10-05는 보고서가 **최초 실패, 자동 원복 확인, CI 최종 failure, 사용자 요청 경로 복구 여부**를 구분해 설명하도록 하는 단계다.

처리 흐름은 **운영자가 CI 출처·대상 검증 → snapshot과 단계별 수집 감사 생성 → CI 입력이면 전용 규칙 선택 → 같은 run/attempt·artifact·소스·UID·revision·시간 순서 대조 → 관측 사실과 한계 작성 → 보고서 근거 검사 → 개인 저장소에 UTC JSON과 KST Markdown 저장 → 재조회 시 해당 규칙 버전으로 해석 재검사**다. CI 입력이 없는 기존 worker 조사는 계속 기존 v3 규칙을 사용한다.

`run/attempt`는 CI 실행 번호와 재시도 번호, `artifact`는 그 실행이 보관한 결과 ZIP이다. `관측 사실`은 확인한 자료를 증거 ID와 함께 설명한 내용이고, `원인 후보`는 추가 확인이 필요한 해석이다. `자동 원복`은 CI가 이전 이미지/revision으로 되돌린 단계이며 이 보고서 규칙이 새로 실행한 조치가 아니다. `current_state=unknown`은 현재 전체 서비스의 복구 여부를 확인하지 못했다는 뜻이다.

## 입력에 따른 설명

| 입력 | 보고서 |
|---|---|
| 같은 실행의 유효한 실패·원복 관측 | 최초 배포 failure, 같은 CI의 자동 원복 success, 최초 실패 보존으로 인한 최종 failure를 각각 설명 |
| 한 단계만 유효 | 단일 관측만 설명하고 전후 흐름이나 복구를 확정하지 않음 |
| 실행·artifact·해시·소스·UID 불일치, 시각 역전 또는 같은 revision | 두 관측을 하나의 실패/복구 흐름으로 연결하지 않음 |
| 현재 Pod Running/Ready | 수집 당시의 부분 관측으로 설명. 과거 실패를 부정하거나 전체 서비스 복구로 확대하지 않음 |
| CI 자료 stale | 원인/복구 판단에서 제외. 감사의 원래 관측 시각을 KST로 설명하며 유효 증거처럼 인용하지 않음 |
| 단계/출처 감사 누락 | 새 의미를 추정해 채우지 않음. 과거에 저장한 v1/v2/v3 보고서는 해당 버전으로 재조회 |

CI 보고서의 현재 상태는 `unknown`을 유지한다. Argo/Ready와 원복 단계 성공만으로 HTTP·DB·Redis 복구를 확인했다고 말하지 않는다. 원본 `user_path_recovery_verified=false`를 성공으로 바꾸지 않으며 해당 필드가 없는 과거 감사에도 false 값을 새로 넣지 않는다. 이 규칙은 CI 배포 이력에 초점을 맞추며 다른 경보 원인이나 전체 서비스 건강을 종합 평가하는 새 분석기가 아니다.

## 파일별 역할

| 파일 | 역할 |
|---|---|
| `src/ai_ops/ci_rules.py` | `rules-captured-ci-v1` 전용 해석, 실행·대상·시각 대조, CI 결과·원복 범위·stale 설명 |
| `src/ai_ops/collection.py` | workflow, 최종 conclusion, 단계, 단계별 결과, 사용자 경로 확인 값의 선택적 감사 필드 |
| `src/ai_ops/live_collectors/deployment.py` | 검증을 통과한 원본 관측의 단계와 확인 범위를 감사에 전달 |
| `src/ai_ops/rules.py` | CI 입력만 전용 규칙으로 연결. 기존 v3 본문을 별도 함수로 보존 |
| `src/ai_ops/incidents.py` | 새 버전 저장/재조회, 해석과 누락 이유의 재작성 거부 |
| `scripts/import_lab_ci_history.py` | `--store` 선택 시 같은 검증 snapshot으로 보고서 저장 |
| `tests/test_ci_report.py` | 신규 11개 시험: 실행 연결·누락·충돌·stale·현재 Ready·KST·CLI·과거 버전 보존 |

공개 EvidenceBundle/AnalysisReport 스키마와 900초 수집 창은 바꾸지 않았다. 감사 필드는 기본값 None인 선택 필드여서 과거 자료를 읽을 수 있다. 기존 보고서의 버전이나 내용을 자동 교체하지 않는다. 외부 모델 전송 정책·worker 권한도 유지한다.

## 운영자 실행

[S10-04 연결 준비](B6-ci-history-import.md#운영자-실행-방법) 후 기존 명령에 `--store`를 추가한다. 개인 폴더·읽기 권한·인증서 대조·원본 해시 검증은 그대로 필요하다.

```sh
PYTHONPATH=ai-ops/src python ai-ops/scripts/import_lab_ci_history.py \
  --run-id 39719907612 \
  --expected-head b0a667fd77484eb126985b3dd6ea811e7831fd7c \
  --bindings "$AI_OPS_FILES_ROOT/bindings.json" \
  --runtime "$AI_OPS_FILES_ROOT/runtime.json" \
  --metrics "$AI_OPS_FILES_ROOT/metric-queries.json" \
  --output "$AI_OPS_FILES_ROOT/import-with-report.json" \
  --store "$AI_OPS_FILES_ROOT/ci-reports"
```

출력 JSON의 `investigation`에는 사건 ID·조사 실행 ID·상태·규칙 버전이 있다. 사건은 조사 요청 한 건이며 조사 실행 ID는 GitHub CI 실행 번호와 다르다. 개인 저장소의 `<사건 ID>/<조사 실행 ID>/report.json`, `report.md`, `collection-audit.json`을 함께 확인한다. `show`는 저장된 증거/감사와 해석을 다시 대조하며 네트워크나 모델을 호출하지 않는다.

`--store` 생략 시 S10-04처럼 snapshot만 저장한다. 지정해도 오래된 관측을 최신으로 바꾸지 않는다. 원래 snapshot을 보존하고 별도 실행에 보고서를 만든다. 자동 worker 이력 입력과 클러스터 이미지 배포는 이번 범위에 없다.

## 검증 결과

[검증 JSON](assets/B6-s10-report-validation.json)에 실제와 모의를 분리했다.

- **실제 보존 자료:** CI `39719907612`·artifact `12401091372`를 검증해 13:19 KST에 수집했던 snapshot을 오프라인 재분석했다. 원본 파일 해시는 그대로다. CI 두 관측은 원래 13:02:13/13:02:33 KST의 stale로 제외했다. Pod 부분 관측 1개·가설 0개·`partial / insufficient_evidence / unknown`을 저장하고 같은 내용으로 재조회했다. [실제 stale 보고서](assets/B6-s10-stale-ci-report.md)
- **모의 유효 구간:** API와 시계를 모의한 구간에서 최초 실패·자동 원복·최종 failure를 구분하고 사용자 경로 복구를 주장하지 않았다. `complete / suspected_cause / unknown`, UTC JSON·KST Markdown과 재조회를 확인했다. 샘플 식별자/시각은 회귀 시험 자료를 재사용했으며 실제 최신 실행의 성공 근거가 아니다. [모의 보고서](assets/B6-s10-simulated-ci-report.md)
- **회귀 검사:** 신규 11개 포함 Python 354개 통과. 기존 SQLite ResourceWarning은 앞 단계와 같은 로컬 Python 3.13 경고 필터로 분리했다. CI 해석/누락 이유 변경 거부와 기존 v3 파일 바이트 보존을 확인했다.

이번 새 장애 주입·AWS/Kubernetes 조회·외부 모델 호출은 0회다. 실제 최신 CI 쌍의 실시간 보고서 연결, worker 자동 입력, 라이브 모델 분석, runner 강제 종료·원복 실패는 여전히 미검증이다.

S10-05의 전용 규칙·저장/재조회와 위 두 입력 범위의 검증을 완료했다. **S10 전체 실시간 경로·B-06 전체 완료·C단계 진입은 보류**한다. 후속은 평가표의 미확인 항목과 완료 조건을 먼저 대조해 필요한 검증만 선택한다.
