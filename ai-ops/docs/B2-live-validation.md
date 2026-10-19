# B-02 실제 수집·규칙 보고서 저장/재조회 검증

2026-10-19 KST **제한된 실제 수집→규칙 보고서 저장→재조회 연결 검증 완료**. 최종 수정 이미지에서 임시 정규화 없이 일반 수집 CLI를 실행했다. 보고서 내용은 자료가 부족한 **부분 결과**이며 원인 파악이나 정상 판정이 완료된 것은 아니다. B-02 전체는 이벤트/로그 시각 대조와 배포 실패·롤백 artifact 연결 등이 남아 진행 중이다.

최종 수정은 [PR #35](https://github.com/f-lab-edu/F-Lab-DevOps/pull/35), main `5d432f5e96a01357bf7efa711bb600197a13c95b`, [등록 실행 39227539145](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39227539145)다. 소스·manifest/config·artifact·실제 Pod 코드 해시 일치, ECR COMPLETE·전체 0건을 확인했다. 현재 digest는 `sha256:49d1775ba0ebf1c2500e8cb7dfe6f46b40b955e08293954c86073123983080c5`, Helm revision 3이다.

2026-10-19 18:12 KST 실제 수집→규칙 부분 보고서→PVC 저장→JSON/KST 재조회 연결 검증 완료. PR #34·#35 수정이 포함된 main 5d432f5 이미지로 일반 경로를 실행했고 Pod 2개 상태·설정, 배포/Argo·캐시 지표 등 7개 항목을 수집했다. 총 18개 중 available 7·invalid 2·missing 7·stale 2로, 자료 부족은 partial/unknown/insufficient_evidence로 유지했다. 이전 Pod의 저장 보고서도 새 Pod에서 해시 변화 없이 재조회했다. 외부 AI·키·예산 경로는 호출하지 않았고 기존 앱 UID/generation·Ready 2개는 유지됐다.

## 목적과 처리 흐름

등록 대상의 Kubernetes 상태·제한된 로그·관련 이벤트·Argo 배포 정보·고정 Prometheus 질의를 읽고, 정제된 증거 묶음과 감사를 규칙 보고서로 저장한다. `show`가 저장된 입력·근거·감사 해시를 다시 검사한 뒤 UTC JSON과 KST Markdown을 재생성하는지 확인한다.

대상은 production으로 보존한다. Pod의 수집 설정은 `llm_enabled=false`이며 provider·키 조회·예산 예약 진입을 검사 코드에서 실패시키는 경계도 추가했다. 실제 운영 자료는 외부 모델로 전송하지 않는다.

## 이번 검증의 자료 처리 범위

- 등록된 `urlshortener / url-shortener / url-shortener-api / url-shortener-svc / production`과 컨테이너 `api`만 수집한다.
- 기본 lookback 15분, Pod별 로그 200줄 및 기존 bytes/deadline 제한을 유지한다. 업무 요청이나 장애를 새로 발생시키지 않는다.
- `/items`와 `select_all`을 명시해 기존 지표 dimension을 사용한다. 없는 지표를 0으로 채우거나 트래픽을 만들어 채우지 않는다.
- 원시 응답은 메모리에서 정제하고 로그·이벤트는 허용 진단 표식만 남긴다. 자유 문장 제거로 진단 정보가 손실될 수 있다.
- 실제 정제 입력·보고서·감사는 암호화 PVC의 `/data/incidents`에만 보관한다. 디렉터리 0700·파일 0600을 검증하고 본문을 Git·로컬 사이트·외부 모델로 내보내지 않는다.
- 이번 검증 요청의 보관 기간은 생성 후 **7일**, 삭제 책임은 저장소 운영자다. `retention/<request-id>.json`의 UTC `delete_after`와 대상 범위를 먼저 기록한다. 자동 삭제 작업은 아직 구현하지 않았다.

삭제 시 해당 보관표의 request ID·사건 ID·run ID를 대조해 실행 디렉터리의 input/report/metadata/collection-audit와 해당 요청 index·보관표/검증 결과를 함께 정리한다. 원시 응답 파일은 만들지 않는다. 다른 실행이나 공용 fingerprint key·잠금 파일은 삭제 범위에 포함하지 않는다. 전체 EBS 정리는 인프라 정리 가이드의 자료 보관·PVC 삭제 절차를 따른다.

## 실환경에서 발견한 문제와 수정

1. 첫 실행은 설정 계약을 통과했으나 요청 index·보고서 저장 전에 중단됐다.
2. 함수·라인·계약 필드만 출력하는 제한된 진단으로 `collect_snapshot` 반환 성공과 `validate_local_bundle`의 시각 검사 중단을 구분했다. 운영 본문·원시 예외 메시지는 출력하지 않았다.
3. 원인은 수집기가 범위 밖 시각을 `stale/outside_window`로 분류하면서 분석용 `observed_at`에도 남긴 반면, 공통 저장 검증은 모든 관측 시각이 조사 구간 안에 있어야 한다고 요구한 것이었다.
4. 수정은 범위 밖 `observed_at`을 비우고 감사의 `observed_raw`·시간대는 보존한다. stale 상태·누락 사유·부분 보고서 원칙과 공통 시간 검증은 유지한다.

수정 전 회귀 검사가 같은 오류로 실패함을 확인했다. 수정 후 전체 Python **155건**을 통과했다. 회귀 검사는 오래된 항목의 부분 보고서 저장·재조회·감사 시각 보존과, 범위 밖 관측 시각을 다시 끼운 묶음의 거부를 포함한다. 실패한 실환경 시도는 성공 결과로 세지 않는다.

## 이번 연결 검증 완료 항목

- [x] 시각·목록 호환 수정을 main과 기존 보호된 수동 이미지 등록 경로로 반영.
- [x] 새 이미지 소스·digest·artifact·ECR 검사 대조 후 실제 진단 StatefulSet에 적용.
- [x] 일반 수집 CLI→규칙 부분 보고서 저장→show JSON/Markdown 재조회.
- [x] 내부 UTC·KST 표시·0700/0600 권한·파일 해시·감사/근거 연결 및 수집 불완전의 partial 표시.
- [x] 이전 Pod에서 저장한 보고서의 새 Pod 재조회·파일 해시 불변.
- [x] 키 조회·모델·비용 예약 경로 미호출, 외부 전송 정책 거부, 기존 앱 무변경 확인.

본문을 제외한 근거는 [검증 JSON](assets/B2-live-validation.json)에 기록했다. 보관 기한은 최종 실행 기준 **2026-10-26 18:12:10 KST**이며 자동 삭제는 활성화하지 않았다.

[운영자 검사 스크립트](../scripts/probe_live_investigation.py)는 CLI와 저장 결과를 메모리에서 검사하고 고정 상태 코드·건수·해시만 출력한다. 원문을 저장소 밖으로 내보내지 않는 검증 도구이며 AI 에이전트 도구로 연결하지 않았다.

## 두 번째 실환경 호환 문제와 사전 대조

실제 PodList·ReplicaSetList의 상위 kind와 selector는 등록과 맞았으나 items의 kind는 생략돼 있었다. 수집기는 이 항목을 namespace/UID 불일치와 같은 scope_mismatch로 제외했다. 검증한 상위 세 목록(PodList/ReplicaSetList/EventList)에서 생략된 kind만 복사본에 보완하도록 수정했다. 명시적으로 다른 kind, namespace·UID·선택자·소유 관계·목록 개수/페이지 경계는 계속 거부한다.

수정 전 실패 재현과 수정 후 전체 Python 156건을 통과했다. 배포 전 운영자 프로세스에서만 정규화를 임시 적용한 사전 대조에서는 Pod 2개의 상태·설정과 배포/Argo·캐시 지표 등 7개 항목을 수집했고 부분 보고서 저장·재조회를 통과했다. 이 결과는 최종 이미지의 일반 경로 검증으로 세지 않는다.

로그는 각 195줄에서 허용 진단 표식이 없어 privacy projection 정책이 제외한 것으로 확인했다. 본문은 내보내지 않았다. 최근 업무 지표의 no_traffic/empty_response, 관련 이벤트 없음, 오래된 배포 이력, 미연결 CI 이력은 자료 부족으로 남긴다. 이를 정상/0으로 채우거나 장애를 발생시켜 보충하지 않는다.

## 최종 이미지 반영과 일반 경로 결과

사용자가 환경을 승인한 뒤 등록 실행이 성공했고 새 digest의 ECR COMPLETE·전체 0건을 확인했다. Helm revision 3으로 교체한 Pod에서 실제 collection.py·live_collectors/common.py가 해당 Git 소스와 같음을 해시로 확인했다. 임시 정규화를 적용한 사전 대조와 구분해 일반 수집 경로에서도 Pod 2개의 상태·설정을 수집했고 scope_mismatch는 사라졌다.

| 증거 상태 | 건수 | 해석 |
|---|---:|---|
| available | 7 | Pod 두 개 상태·각 설정 요약·배포 객체·Argo·캐시 outcome |
| invalid | 2 | 로그의 허용 진단 필드가 없어 정책에 따라 제외됨. 앞선 제한 진단에서 확인 |
| missing | 7 | 관련 이벤트 없음, CI 이력 미연결, 빈 캐시 series·업무 트래픽 없음 등 |
| stale | 2 | 조사 15분 범위 밖의 배포 이력. 원래 시각은 감사에 보존 |

보고서의 상태는 partial, 현재 상태 unknown, 진단은 insufficient_evidence다. CLI 종료 코드 1은 이 부분 결과를 나타내며 보고서 저장 오류가 아니다. 운영자 검증 프로세스는 저장·재조회·권한·해시·시간 표시·외부 전송 차단을 확인해 통과했다.

## 남은 작업

실제 이벤트·허용 로그의 시각과 다른 출처의 사건 순서 대조, 앱 배포 실패·롤백 전후 artifact import는 아직 미완료다. 이번 연결 검증을 위해 앱 트래픽이나 장애를 만들지 않았으며 빈 자료를 0으로 채우지 않았다. B-03의 자체 로그·메트릭·중지/복구 절차도 남아 있다. 자동 경보·큐·worker·실자료 외부 AI·조치는 활성화하지 않았다.
