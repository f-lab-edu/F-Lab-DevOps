# B-06 조회 순서와 내부 snapshot의 v3 정제 반출

상태: **2026-10-26 KST exporter 구현·실제 저장 자료 한 건 반출 검증 완료.** [실제 조회 수집](B6-normal-sequence-capture.md)의 miss→hit 두 쌍을 실습 수집기 안의 원본 snapshot과 대조해 `captured-practice-projection-v3` 입력을 만들었다. 새 실습·모델 호출·API 예약은 0건이다. 전송 미리보기·v3 일회 승인/예산 저장 경로와 상시 자동 접수는 아직 연결하지 않았다.

## 처리 흐름과 파일

`scripts/prepare_sequence_projection.py`는 원복·공유 잠금 해제가 확인된 capture JSON의 네 조회와 로그 해시를 재검증한다. 현재 EKS 연결·실습 Deployment UID·수집기 Pod 소유 관계·이미지 digest를 읽고, 앱의 고정 Pod UID·컨테이너 실행 ID가 capture 당시와 같은지 확인한다. 그 뒤 검토한 v1/v3 모듈을 **수집기 메모리에서만** 실행하고 저장된 IncidentStore의 bundle/audit를 읽는다. 원본 snapshot 전체를 출력하거나 로컬 입력으로 대체하지 않는다.

수집기 안에서 기존 v1 정제를 재생성한 뒤 조회 기록의 item·순서·시각·counter·로그와 결합한다. source bundle/audit/config·Deployment UID·Pod UID·이미지·코드 해시가 맞아야 한다. 반출된 `SequenceProjection`은 로컬에서 다시 파싱해 동일 capture·현재 identity·읽기 전후 Pod 불변을 검사하고 소유자 전용 새 파일에 저장한다. 이 private 객체에는 원본 대응을 위한 probe ID·선별 로그가 있으므로 **객체 전체를 모델에 보내지 않는다.** 모델 요청은 `result.view`만 사용한다.

| 파일 (`ai-ops/` 기준) | 역할 |
|---|---|
| `scripts/prepare_sequence_projection.py` | 완료 capture 검사, 수집기 내부 exporter, 현재/전후 identity 검사, v3 비공개 저장 CLI |
| `src/ai_ops/model_sequence_projection.py` | 원본 snapshot의 소유 Deployment 이미지와 Pod 소유 UID 대조. runtime 태그와 desired digest의 표시 차이를 구분 |
| `tests/test_sequence_exporter.py` | 유효 반출, 미복구·변조·허용 밖 이미지 거부, 읽기 중 수집기·앱 교체 거부 3개 시험 |
| `tests/test_model_sequence_projection.py` | Pod status.image의 runtime 태그와 Deployment digest 차이를 허용하되 다른 Deployment 이미지 해시는 거부하는 회귀 시험 추가 |

## 실제 결과

원본은 [16:21~16:23 KST 수집](B6-normal-sequence-capture.md)의 한 건이다. 같은 수집기 내부 원본으로 생성한 결과는 다음과 같다.

| 항목 | 결과 |
|---|---|
| 정책·항목 | `captured-practice-projection-v3`, 총 9개 항목. 마지막 `cache_probe_sequence`는 같은 아이템의 miss→hit→miss→hit, HTTP 200 네 건 |
| 원본 bundle | `9c6db79c20a6facce4ed6cd5e1028330f69d79e1a3b3d02bc4b4da076231e5d9` |
| view SHA-256 | `082b29757099cdf763201b11bf0e27249b5ae79732093c74b60d90641bc0fe32` |
| 요청 구성 | 토큰 계산 본문 8,142바이트·생성 본문 8,225바이트, 각각 상한 32,000바이트 이내. 코드에서 오프라인 구성만 확인했으며 외부 전송 없음 |
| 모델 본문 제외 | 구조 검사에서 item ID 필드·Pod/Deployment UID·로그 문장·원본 대응표 제외. `/items/{item_id}`는 실제 ID가 아닌 기존 route 템플릿이며 그대로 허용 |
| 저장 | `~/.local/share/url-shortener-ai-ops/normal-sequence-observations/s2-bf430200faba4e11aaa135773430301d/projection-v3.json`, 파일 0600 |
| 모의 보고서 | 같은 view의 순서 근거를 인용한 사실 1개, partial/unknown. 고정 규칙 결과이며 실제 모델 의미 평가가 아님 |

첫 실행은 `probe pod or image ownership rejected`로 중단됐다. snapshot의 Pod status.image가 runtime 태그이고 capture의 Deployment 이미지는 digest여서 문자열 해시가 달랐다. 같은 Pod·Deployment UID가 일치하는 것을 확인한 뒤 검증을 수정했다. snapshot의 **소유 Deployment desired 이미지 해시**와 capture digest를 대조하고, 현재 앱의 spec digest·runtime imageID 일치는 기존 pin 검사에서 계속 확인한다. 검사를 생략하거나 임의 이미지를 허용하지 않았으며 새 실제 조회를 반복하지 않았다.

## 검증과 다음 작업

후속 [v3 일회 비교 준비](B6-normal-sequence-comparison-preparation.md)에서 별도 비공개 저장·전체 미리보기·입력별 승인·기존 로컬 예산 장부 공유를 구현했다. 아래 미연결 표시는 exporter 완료 당시 기록이며, 현재 새 입력은 `prepared`로 사용자 승인 전이다.

전체 Python **477개 시험**이 통과했다. exporter·v3 관련 11개 시험, 비공개 실제 view 재파싱·요청 크기·원본 노출 검사와 변경 형식 검사도 통과했다. 원격 CI·이미지 빌드·상시 배포·실제 모델 생성은 이번 범위가 아니다.

다음은 **v3용 비공개 일회 비교 저장소·미리보기·승인·예산 경로**를 연결하는 작업이다. 기존 v1 일회 비교 코드와 보존 보고서를 교체하지 않고, 실제 전송 시에는 새 v3 입력의 전체 요청 본문·코드 해시·비용·보관 조건을 결합한 별도 승인이 필요하다. 이전 S2 일회 승인은 재사용하지 않는다. B-06 완료·C 진입은 계속 보류하며 다음 작업 권장 추론 깊이는 **GPT-6 SOL high**다.
