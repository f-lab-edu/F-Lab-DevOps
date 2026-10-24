# B-06-M2 정제 입력·승인·모의 모델 비교 구현

상태: **2026-10-23 KST 로컬 구현과 모의 검증 완료**. 실제 snapshot 반출·API 호출·과금·클러스터 변경은 실행하지 않았다. [M1 정책](B6-model-data-policy.md)을 구현했으며 다음은 B-06-M3의 실제 입력 준비와 제한 비교다.

## 무엇을 구현했고 어떻게 처리하는가

실습 snapshot 검증 → 필드 선별·별칭 → 전용 비공개 저장소 → 실제 요청 내용 미리보기 → 입력/코드/모델/한도에 결합한 일회 승인 → 모의 예산 예약 → 토큰 계산/생성의 모의 호출 → 근거와 보고서 계약 검사 → 같은 입력의 규칙 요약과 별도 저장 → 기한별 정리 순서다.

운영 worker·기존 규칙 보고서와 synthetic 전용 모델 경로는 바꾸지 않았다. CLI의 `run-mock`은 실제 API를 호출하지 않고 규칙 요약을 정형 모델 응답처럼 돌려주는 **모의 실행**이다. 모델 품질이나 실제 청구 비용을 측정하는 기능이 아니다. 실행 계획의 `execution_mode`도 `mock`으로 고정했고, 일반 HTTP transport와 API 키 옵션은 제공하지 않는다. M3에서는 이 mock 승인을 실제 전송 승인으로 재사용할 수 없으며 새 전송 구현·계획·미리보기·승인이 필요하다.

## 파일별 역할

파일 경로는 `ai-ops/` 기준이다.

| 파일 | 역할 |
|---|---|
| `src/ai_ops/model_projection.py` | 정확한 practice/ai-ops-lab/lab-api 대상·snapshot 감사·설정 hash·Deployment/Pod 소유 관계 검증. 상태·설정 여부·등록 지표만 별도 타입으로 생성하고 원본↔view ID/hash mapping을 결합한다. |
| `scripts/prepare_model_projection.py` | 고정 AWS/EKS context·Endpoint/CA fingerprint·현재 대상 UID·수집기 고정 digest/Pod/ReplicaSet 소유 관계를 조회한다. 검토된 선별 코드를 실습 Pod의 Python 메모리에서 실행해 기존 저장 조사 한 건을 읽고, 정제 결과만 반출한다. 읽기 전후 식별자를 비교하며 파일·이미지·배포 설정은 변경하지 않는다. |
| `src/ai_ops/projected_model_engine.py` | 프롬프트·모델/effort·정형 출력·요청 크기·출력/시간 상한을 고정한다. 토큰 계산도 전송 요청으로 미리보기에 포함한다. 임의 도구 호출·근거 외 인용·healthy/recovered·실행 완료를 거부한다. 기본 네트워크 transport는 없다. |
| `src/ai_ops/model_comparison.py` | 같은 view의 규칙 요약, 0700/0600 저장, HMAC 무결성, 잠금·일회 승인·모의 UTC 예산, 중단/재실행 처리, 만료 자료 연계 정리. |
| `scripts/compare_model_projection.py` | prepare·preview·approve·run-mock·show·purge 명령을 제공한다. 모의 결과에는 `transport_kind=mock`, `external_model_calls=0`을 기록한다. |
| `tests/test_model_projection.py`, `tests/test_model_comparison.py` | 출처/필드·시간·식별자·변조·승인/예산·허위 인용·병렬/중단·보관·CLI의 실제 연결 금지를 확인한다. |

## 원본과 정제 입력의 차이

원본 evidence의 이미지·실제 namespace·source_ref·이벤트 문장·배포/CI 원문은 모델 요청에 넣지 않는다. 새 view에는 `service-1`·`cluster-1`·`pod-1`과 새 증거 ID만 사용한다. source bundle/audit hash와 원본 ID/hash mapping, 현재 대상 UID 등은 local manifest에만 보관한다. 정제 출처는 `captured_projection`이며 원본을 synthetic으로 바꾸지 않는다.

없는 설정 값은 `null` 또는 `unknown`으로 남긴다. missing/invalid/stale 자료를 0·정상으로 보정하지 않는다. 제외된 로그·이벤트·배포는 원래 자료 상태별 개수로 표시한다. 현재 snapshot에는 scrape 품질의 상세 표본 수가 별도로 보관되지 않으므로 `scrape_quality=not_retained_in_source_snapshot`과 한계를 명시한다. 이 값에서 품질 통과를 추정하지 않는다.

원본을 모델 조회 도구에 제공하지 않으며 첫 비교에는 함수·shell·MCP·검색·런북 도구가 없다. 새 규칙 요약도 모델과 같은 view만 읽는다. 기존 전체 snapshot의 규칙 보고서를 동일 입력 비교군으로 사용하지 않는다.

## 승인·예산·중단 처리

미리보기는 `/v1/responses/input_tokens`와 `/v1/responses`에 전달할 본문 전체 및 가격/시간/크기 계획을 포함한다. 생성 요청의 `store=false`·`background=false`, tools 부재, model `gpt-6-sol`·effort `low`를 고정한다. 공급자 보관 한계는 [M1 정책](B6-model-data-policy.md#api-호출비용보관-조건)에 기록했다.

승인은 source/view/mapping·코드·두 요청 template·모델·한도에 결합하고 30분 후 만료한다. 코드나 입력 교체는 새 준비/승인이 필요하다. 동시 실행은 같은 잠금으로 직렬화하고, 전송 시작을 영속 저장한 뒤에만 mock transport를 호출한다. 중단된 `sending`을 다시 실행하면 `needs_review`로 바꾸고 재전송하지 않는다. 응답 없는 모의 생성도 사용량 불명으로 남기며 예외 원문은 저장하지 않는다.

건당 $0.10·UTC 일일 $1은 **모의 예약 장부**로 검사한다. 계정 label이나 새 case를 만들어도 같은 저장소의 일일 합계가 적용된다. $0.10 예약은 실제 청구액이 아니며 실패해도 자동 환급하지 않는다. 가격은 계획 파일에 기준일·공식 출처·입출력 단가를 명시해야 하고, 승인/실행 시 7일 이내인지 검사한다. M2는 그 가격을 공식 사이트에서 검증했다고 주장하지 않는다. M3 전에 계정·최신 단가·실제 사용량 계약을 확인하고 실사용 장부와 모의 예약을 구분해야 한다.

정형 계약을 통과한 모의 결과도 `needs_review/semantic_review_required`다. 문장 의미의 정확성은 형식 검사만으로 확정하지 않는다. 실패한 초안·원문 API 오류는 저장하지 않고 고정 사유와 알려진 사용량만 기록한다. 기존 규칙 보고서를 AI 성공 결과로 바꾸지 않는다.

## 운영자 명령과 저장 위치

기본 저장소는 Git 밖 `~/.local/share/url-shortener-ai-ops/model-comparisons`다. CLI에는 임의 `--store` 옵션이 없으며 비교 입력·승인·규칙/모의 결과가 한 고정 저장소에 모인다. HMAC 키와 당일 예산도 그 안에 둔다. 같은 OS 사용자/관리자까지 막는 sandbox나 별도 인증 서비스는 아니다.

아래는 **사용 순서**이며 실제 자료·모델을 이번에 실행한 기록이 아니다. `CMP_ID`, `BINDING_HASH`는 미리보기에서 확인한 값이다. plan JSON은 `CallPlan` 계약의 account_label·execution_mode=mock·기준일/출처/입출력 단가를 포함한다. 예시 단가를 최신 가격으로 제공하지 않는다.

```bash
export PYTHONPATH=ai-ops/src
python ai-ops/scripts/compare_model_projection.py --help

# 이미 정제된 오프라인 시험 입력으로 준비한다. 원본 snapshot 파일을 넣는 명령이 아니다.
python ai-ops/scripts/compare_model_projection.py prepare --projection /REGISTERED_PRIVATE_DIR/projection.json --plan /REGISTERED_PRIVATE_DIR/plan.json
python ai-ops/scripts/compare_model_projection.py preview --id CMP_ID
python ai-ops/scripts/compare_model_projection.py approve --id CMP_ID --binding-hash BINDING_HASH --actor OPERATOR
python ai-ops/scripts/compare_model_projection.py run-mock --id CMP_ID
python ai-ops/scripts/compare_model_projection.py show --id CMP_ID
python ai-ops/scripts/compare_model_projection.py purge
python ai-ops/scripts/compare_model_projection.py purge --apply
```

실습의 실제 자료 준비는 `prepare_model_projection.py --identity ... --collector-image ... --plan ...`로 분리했다. identity는 검토한 exporter 두 파일의 hash, cluster fingerprint, Deployment UID, 설정 hash, 저장 incident/run ID를 명시한다. 수집기 내부 검증을 거친 정제 결과는 별도 임시 JSON을 만들지 않고 위 비교 저장소에 바로 저장한다. **이번 단계에서는 이 실제 Kubernetes 반출 명령을 실행하지 않았다.**

입력 파일은 기존 경로 정책에 등록된 디렉터리의 JSON만 허용한다. 위 `REGISTERED_PRIVATE_DIR`은 실제 경로로 그대로 실행할 수 있는 값이 아니다. 독립 시험은 임시 0700 폴더를 운영자 환경 설정 `AI_OPS_FILES_ROOT`에 등록한다. 오프라인 prepare에서 운영자가 별도로 제공한 projection/plan 파일은 복사본이므로, 그 원본 파일의 정리는 운영자 책임이다. 실습 반출 경로는 이러한 추가 본문 파일을 만들지 않는다.

관리 자료는 생성 시점 7일 후 purge 대상이 된다. projection·mapping·승인·규칙/모의 결과가 들어 있는 본문을 함께 제거하고 재사용 방지용 ID/hash tombstone은 남긴다. 원자 저장 중 종료돼 남은 `.pending-*` 본문도 7일 후 정리한다. 본문 없는 모의 예산 장부는 90일을 유지하며 당일 예약을 삭제하지 않는다. 예약 실행을 새로 설치한 것은 아니므로 자동으로 기한에 삭제된다고 주장하지 않는다.

## 검증과 남은 범위

- 신규 **24개**, 최신 main 기반 전체 **398개** Python 시험 통과. 문법 검사와 기존 Helm/읽기 권한·통신·저장·기존 알림 경계 검사 통과.
- 미승인·승인 만료·코드/입력 변조·production/다른 UID·알 수 없는 필드·민감 필드 sentinel·NaN/임의 차원·허위 인용·실행/정상 주장·요청 크기·비용 초과를 거부했다.
- 병렬 두 실행의 생성 1회·예약 1회, 프로세스 중단 뒤 재전송 없음, 일일 한도와 timeout의 불명 비용, 본문/임시 파일·장부 정리를 모의로 확인했다.
- 새 exporter의 출처 전후 검사는 모의 실행기로 확인했다. 실제 클러스터의 새 exporter 실행·실제 정제 view 생성·공급자 호환성·사용량·모델 의미 품질은 미검증이다.
- 전체 시험에서는 기존 SQLite ResourceWarning의 로그 캡처 간섭을 경고 필터로 분리했다. 기존 연결 정리 문제를 이번에 해결한 것으로 표시하지 않는다.

실제 API 키나 개인 gh 인증을 읽지 않았고, AWS/Kubernetes 변경·새 이미지 등록·배포·장애 주입·모델 호출은 없다. 로컬 설명 사이트는 이번 구현 작업에서 갱신하지 않았다.

다음 **B-06-M3**에서는 최신 main에 변경을 검토·반영할 준비를 마친 뒤 새 실습 읽기 조사 한 건의 exporter 호환성과 정제 미리보기를 확인한다. 실제 외부 HTTP transport·계정/보관/가격·새 승인·실사용 장부를 준비하고, 그 구체적인 입력에 대해 전송 조건을 확인한 뒤 한 번만 비교한다. 기존 통과 시나리오를 전면 재실행하지 않는다. B 완료·C 진입은 계속 보류한다. 권장 추론 깊이 **GPT-6 SOL high**: 실제 출처·전송/비용 승인·실제 모델 결과의 의미를 함께 검증해야 하기 때문이다.


## M3 후속 준비 상태 — 2026-10-23

위 내용은 M2 당시 모의 전용 구현 기록이다. 이후 실제 exporter·고정 입력 미리보기·live 전송/분리 예산을 준비했다. 실제 전송은 입력별 승인 대기이며 아직 실행하지 않았다. [M3 준비와 검증 범위](B6-model-projection-live-comparison.md)를 현재 상태로 참고한다.


M3 후속 실행에서는 입력별 진행 승인과 계정 공유 꺼짐 확인 뒤 토큰 계산/모델 생성 각 1회를 완료했다. 같은 view 내용 자체 검토와 원본 보존을 확인했으며 상세 결과는 [M3 기록](B6-model-projection-live-comparison.md)을 참고한다.
