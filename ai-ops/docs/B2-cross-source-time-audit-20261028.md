# B-02 기존 실측의 출처별 시각·순서 대조 — 2026-10-28 KST

판정(17:13 KST): **B-02 원래 체크리스트의 남은 시각 대조와 배포 실패·자동 원복 자료 검증을 제한된 실측 범위에서 충족했다.** 13:22 KST 수집 사례에서 Event·허용 로그·지표·현재 Deployment 객체의 관측/수집 시각을 대조했고, **별개의 S10 사례**에서 원본 CI artifact의 실패·원복 기록과 저장된 CI·Event·Argo·Deployment 증거, 규칙 보고서를 한 조사 창에 연결했다. 서로 다른 사례를 한 사건으로 합치지 않는다. 실제 원본 offset은 모두 `Z`였고 `+09:00` 변환·모호한/미래 시각 거부는 모의 시험으로 확인했다. 시계 자체의 정확도, 모든 출처가 한 사건에서 동시에 가용한 경우, production 장애 원인과 복구는 검증하지 않았다.

## 자료와 검사 범위

| 자료 | 확인한 내용 | 시각 대조의 한계 |
|---|---|---|
| [B-02 production 실측 요약](assets/B2-live-validation.json)·[실행 설명](B2-live-validation.md) | 2026-10-19 18:12 KST의 최종 일반 수집은 18항목 중 available 7·invalid 2·missing 7·stale 2였다. 배포 객체·Argo·캐시 지표는 available, 허용 진단 표식이 없는 Pod 로그 2개는 invalid, 관련 Event와 CI 이력은 missing, 배포 이력 2개는 stale이었다. | 요약의 각 entry에는 `time_field`와 `observed_offset`은 있지만 `observed_raw`·`collected_at`은 없다. 당시 개인/클러스터 원본을 현재 확인하지 못했으므로 출처 간 초 단위 순서나 수집 지연을 재계산할 수 없다. |
| **현재 실습 수집기 PVC의 원본 감사 3건** | 2026-10-28 KST 현재 `ai-ops-lab/lab-collector`의 `/data/incidents`를 읽기 전용으로 조회했다. 3건 중 **13:22 KST 수집**은 가용 Event 10·허용 로그 1·지표 4·현재 Deployment 객체 1을 같은 창에 담았다. 저장된 감사/묶음 해시와 `validate_snapshot` 계약을 수집기 안에서 검증했다. | 이 사례는 격리 실습이다. Argo 객체는 `not_found`, CI 이력은 `missing_history`였고 Event 2개는 invalid, 1개는 stale이다. 보존된 정제 증거에는 Event 원본의 involved UID가 남지 않아 소유 관계를 감사 파일만으로 독립 재검증할 수 없다. |
| [S2 정상 실습의 원본 보관](B6-normal-observation-20261026.md) | 2026-10-26 15:27 KST 실습의 개인 보관 `input.json`·`collection-audit.json`·`metadata.json`을 읽어 증거 묶음/감사 해시와 `validate_snapshot` 전체 계약을 다시 확인했다. 같은 snapshot에서 Pod 상태·설정·Argo/배포·지표의 원본 시각·offset·수집 시각을 대조했다. | 12개 Event와 배포 이력 7개는 모두 15분 창 밖의 `stale`이다. 일반 로그는 `truncated`, CI 이력은 `missing_history`다. 대상도 production이 아닌 격리 실습이다. |
| [S10 실패·원복 이력](B6-ci-history-import.md) | 2026-10-22 운영자 가져오기에서 검증한 실습 CI 두 관측의 실패→원복 순서와 원본 UTC를 확인했다. | 두 관측은 가져오기 당시 15분 창 밖의 `stale`이다. production 일반 수집과 대상·시간창이 다른 별도 실습이며 기본 수집에는 CI 이력이 자동 포함되지 않는다. |
| [새 S10 CI 실행 #40630566031](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40630566031)과 현재 worker PVC | 2026-10-28 17:13 KST에 기존 CI artifact를 GitHub에서 다시 읽고, 같은 실행의 worker 큐·증거·감사·규칙 보고서를 읽기 전용으로 대조했다. 실패/원복 CI 관측 2개, Argo reconcile/이력, Event와 현재 Deployment 객체가 같은 S10 조사 창에 연결됐다. | 로그 1개와 지표 5개는 누락, Event 2개는 무효다. CI 원복 확인과 현재 앱 사용자 요청 경로 복구는 다른 주장이다. 이 사례는 격리 실습이며 13:22 KST의 다른 수집 사건과 합치지 않는다. |
| [격리 실습 조회 기록](B6-normal-sequence-capture.md)과 소유자 전용 `normal-sequence-observations/.../projection-v3.json` | 2026-10-26 같은 Pod에 대한 네 요청의 시작·종료·로그 시각을 원본 보존 결과에서 다시 계산했다. 정제 view에는 같은 snapshot의 상태·지표 관측/수집 시각이 있다. 원본 item ID·로그 문장·Pod UID는 문서와 명령 출력에 복사하지 않았다. | 이 자료는 실습 정상 캐시 사례다. model view의 `excluded`에는 배포 available 2·missing 1·stale 7, Event missing 1, 일반 로그 missing 1이 남아 있다. 정제 view는 원본 offset과 전체 collection audit를 반출하지 않으므로 원본 시간대까지 독립 대조할 수 없다. |

## 10월 28일 현재 실습 수집기 사건 한 건

현재 EKS endpoint를 AWS에서 다시 조회한 뒤 임시 kubeconfig로 수집기의 준비 수 **1/1**과 자료 PVC `lab-collector-source`의 **Bound**를 확인했다. 기본 kubeconfig는 변경하지 않았다. PVC의 감사 기록은 3건이었다. 이 중 아래 사례 한 건만 Event·로그·지표·Deployment 객체가 함께 가용했다. 수집기 내부에서 저장된 `EvidenceBundle`·`CollectionAudit`을 파싱하고 묶음 해시·감사 envelope 해시·**25개** 증거/감사 entry 연결을 `validate_snapshot`으로 검사했다. 원문·UID·토큰·질문은 출력하거나 복사하지 않았다.

| 항목 | 같은 사건에서 검증한 결과 |
|---|---|
| 조사 창 | **13:07:13.761–13:22:14.643 KST**. 실제 수집은 13:22:13.761부터 **0.882초** 걸렸다. 25개 중 available 18·missing 4·invalid 2·stale 1이다. |
| Event | 가용 10개의 원본 `eventTime`/`lastTimestamp`는 **13:11:32–13:11:41 KST**, 원본 관측→수집 지연은 **633.071–642.072초**다. 창 안의 이전 사건 시각일 수 있으나 현재 로그·지표의 원인이라고 확정할 근거는 없다. Event 2개는 `invalid_payload`, 1개는 `outside_window`다. |
| 허용 로그 | 가용 1개의 원본 `log_timestamp`는 **13:22:10.700 KST**, 수집 지연 **3.252초**다. 허용 진단 표식만 남는 정책이며 전체 로그를 입증하지 않는다. |
| 지표 | 가용 4개의 `evaluation_time`은 모두 **13:22:13.761 KST**, 평가→수집 지연은 **0.623–0.881초**다. 2개는 `empty_response`로 누락됐다. 한 시점 평가이므로 13:11 Event 전후 지표 변화는 재구성할 수 없다. |
| 현재 Deployment·Pod | Deployment 객체의 **API 응답 시각**은 13:22:13.786 KST(수집 지연 **0.289초**), Pod 상태/설정의 응답 시각은 13:22:13.870 KST다. 이들은 상태를 읽은 시각이지 이미지 교체·배포가 발생한 시각이 아니다. |
| 배포 이력·offset | Argo 객체는 `missing/not_found`, CI 이력은 `missing/missing_history`다. 원본 시각이 있는 21개 entry의 offset은 모두 `Z`, 미래 시각 허용 표식은 0개였다. +09:00 등 **서로 다른 원본 offset의 동시 정규화**는 이 사례로 실측하지 못했다. |

수집기는 Event 조회 때 현재 Deployment·ReplicaSet·Pod의 UID/이름을 Kubernetes 응답과 대조한다. 그러나 저장된 정제 Event payload에는 원본 `involved_uid`가 남지 않는다. 이 감사는 **수집 시 코드가 소유 관계를 검사했다는 경계**와 저장된 해시/시간 계약을 확인한 것이며, 저장 파일만으로 Event별 원본 소유 UID를 다시 증명한 것은 아니다. 또한 Event 발생→로그→지표 평가의 **시각 순서**를 확인했을 뿐 인과관계나 서비스 장애 원인을 판정하지 않았다.

## 10월 28일 별도 S10 실패·자동 원복의 원본/저장 대조

기존 [격리 실습 CI #40630566031](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40630566031)과 worker의 **13:12 KST 수집**을 대조했다. 전달기의 기존 읽기 전용 GitHub 자격으로 해당 실행의 원본 artifact를 재조회해 실행/시도/소스 revision, artifact ID·ZIP digest, 세 단계 기록이 worker 큐의 고정 입력과 모두 일치함을 확인했다. worker에서는 SQLite `mode=ro`·`query_only=ON`으로 같은 실행의 작업 한 건을 읽었다. 큐 HMAC, 저장된 묶음·감사 계약, CI 출처 결합, 규칙 보고서 계약과 저장 Markdown을 검증했다. 저장 파일 5개의 전후 해시는 동일했다. 원본 본문·UID·토큰·비공개 해시는 출력하거나 로컬로 복사하지 않았다.

| 원본 CI 단계 | 시작 (KST) | 관측 (KST) | 검증된 결과 |
|---|---|---|---|
| 실패한 이미지 적용 | 13:11:29.718313 | **13:11:36.384724** | `failure` |
| 기존 이미지로 자동 원복 | **13:11:36.621164** | **13:11:51.090489** | `success` |
| 최종 실행 결과 | 13:11:55.133611 | 13:11:55.133621 | `recovered_after_failure` |

두 관측 사이 **14.706초**는 CI 기록의 간격이며 서비스 MTTR이 아니다. 실패 관측→원복 시작→원복 관측→최종 결과→수집 시작의 순서는 모두 성립했다. 조사 창은 **12:57:27.396487–13:12:27.946680 KST**, 실제 수집은 **0.550초**였다. 실패 관측에서 수집 시작까지 **51.012초**, 원복 관측에서 수집 시작까지 **36.306초**다.

| 저장된 출처 | 가용 시각·수집 지연 | 해석 |
|---|---|---|
| CI artifact 관측 2개 | **13:11:36.385–13:11:51.090 KST**, 36.740–51.445초 | 실패와 원복의 원본 시각을 같은 실행의 정제 증거로 보존했다. |
| Kubernetes Event 11개 | **13:01:45–13:11:41 KST**, 46.779–642.782초 | 15분 창 안의 Event다. 오래된 Event까지 모두 이번 CI 실패 때문이라고 단정하지 않는다. |
| Argo 현재 reconcile 1개·배포 이력 3개 | reconcile **13:11:38**, 이력 **13:03:34–13:11:38 KST**; 49.825초·49.829–533.826초 | 실제 `reconciledAt`/`deployedAt`과 읽은 시각을 구분했다. |
| 현재 Deployment 객체·Pod 상태·설정 | API 응답 **13:12:27.456–13:12:27.575 KST**, 수집 지연 0.022–0.327초 | 현재 객체의 조회 시각이며 이미지 배포가 발생한 시각으로 해석하지 않는다. |
| 빠진 자료 | 로그 `no_diagnostic_fields` 1개, 지표 `scrape_unavailable` 5개, Event `invalid_payload` 2개 | 정상 값이나 0으로 채우지 않았다. |

원본 시각을 가진 저장 항목 **23개**의 offset은 모두 `Z`였다. 각 원본을 UTC로 다시 변환한 값이 증거 `observed_at`과 일치했고 가용 항목의 관측 시각은 수집 시각 이전이었다. 저장된 규칙 보고서는 `partial / suspected_cause / unknown`이며 사실 4개·가설 1개다. 원복 기록을 포함하지만 **실제 사용자 요청 경로 복구, 장애 원인 확정, AI가 조치를 실행했다는 결론은 아니다.**

시각 누락·모호한 offset은 `invalid_time`, 미래 관측은 `future_observation`, 창 밖 기록은 `outside_window`로 내용 없이 표시하는 기존 계약을 확인했다. 이번 집중 시험 4개는 `+09:00` Event·로그·지표·Argo 입력의 UTC 정규화, KST 출력, 누락/미래 시각 거부를 통과했다. 이는 **모의 입력의 코드 검증**이며 이번 실제 원본의 offset 다양성이나 서로 다른 장비 시계의 정확도를 실측한 것은 아니다. 수집 지연은 위처럼 원본 관측과 `collected_at`의 차이로 계산했고, 미래 시각 허용 범위 표식은 감사에 보존한다.

## 10월 26일 S2 원본 감사 재검사

이 기록은 아래 네 번 조회 사례와 **다른 실습 사건**이다. 개인 보관소에 있는 S2의 원본 JSON만 읽었고 본문·UID·로그 문장을 반출하지 않았다. `EvidenceBundle` 해시, 감사 envelope와 저장 metadata의 해시, 증거 31개와 감사 entry 31개의 ID·출처·시각 일치를 `validate_snapshot`으로 재검증했다.

| 항목 | 재검사 결과 |
|---|---|
| 수집 창과 소요 | **15:12:56.316–15:27:57.228 KST**, 실제 수집 **0.912초** |
| 상태 | 31개 중 available 8·missing 4·stale 19. Pod 3·Event 12·배포 10·지표 6개 항목 |
| 원본 시각·offset | 원본 시각이 있는 27개 entry의 offset은 모두 `Z`. available 8개는 원본을 UTC로 정규화한 값이 증거의 `observed_at`과 일치했다. stale 19개는 모두 원본 시각이 15분 창 시작 전이었다. 누락 4개에는 원본 시각이 없다. 서로 다른 offset을 가진 출처의 변환 정확도까지 실측한 것은 아니다. |
| 수집 지연 | available 8개의 원본 관측→수집 지연은 **0.027–147.649초**. 큰 값은 Argo의 이전 `reconciledAt`을 읽은 지연이며 수집기 실행 시간이 아니다. available 지표 4개는 평가→수집 **0.636–0.911초**였다. |
| Event와 배포 이력 | Event 12개의 원본 시각은 **14:59:28–14:59:37 KST**로 모두 stale이다. 배포 이력에도 **14:59:28·14:59:35 KST** 항목이 있어 시각상 겹친다. 내용·소유 관계·현재 시간창이 유효하다는 뜻은 아니므로 원인 관계를 추론하지 않는다. |
| 결정적 빈자리 | 일반 Pod 로그는 `missing/truncated`, CI 이력은 `missing/missing_history`, 지표 2개는 `missing/empty_response`. 정상이나 0으로 채우지 않는다. |

원본이 `Z`인 27개를 확인했지만 한국 시간 표시는 독립적인 UI 변환이다. **UTC 내부 기록·KST 화면**의 규칙은 유지하며, 서로 다른 시계·원본 offset이 동시에 들어온 사례의 교차 검증은 여전히 필요하다.

## 10월 26일 별도 실습 순서 재계산

아래 시각은 보존 결과의 UTC `Z` 값을 KST로 변환해 표시했다. 각 로그 시각은 **해당 HTTP 요청의 시작과 종료 사이**였고, 네 요청의 시작·종료가 서로 겹치지 않았다. 같은 Pod·컨테이너 고정과 로그 해시 대조는 [원래 수집 검증](B6-normal-sequence-capture.md)에 기록돼 있다.

| 순서 | HTTP 요청 구간 (KST) | 대응 로그 (KST) | 확인 범위 |
|---:|---|---|---|
| 1 | 16:21:39.329–39.368 | 16:21:39.349 | 첫 miss, HTTP 200 |
| 2 | 16:21:44.400–44.454 | 16:21:44.444 | 첫 hit, HTTP 200 |
| 3 | 16:22:24.445–24.518 | 16:22:24.503 | 두 번째 miss, HTTP 200 |
| 4 | 16:22:29.466–29.549 | 16:22:29.538 | 두 번째 hit, HTTP 200 |

정제 view의 수집 창은 **16:08:11.641–16:23:12.654 KST**다. 사용 가능한 네 지표 항목의 평가 시각은 모두 **16:23:11.641 KST**로 마지막 HTTP 종료 약 **42.092초 후**다. 지표 항목의 수집 지연은 약 **0.710–1.012초**, Pod 상태는 **0.024초**, 설정 요약은 **0.107초**였다. 별도 `cache_probe_sequence` 항목은 마지막 로그 시각부터 최종 묶음 생성까지 약 **43.116초**다. 지표 2개는 `empty_response`로 빠져 있다. 따라서 같은 조사 창의 시각 정렬은 확인할 수 있지만, 이 **한 시점의 지표만으로** 네 요청 각각의 miss/hit 전후 변화를 독립 재구성할 수는 없다.

`Z`는 보존 자료의 정규화된 UTC 표기다. 원천 시스템이 원래 어떤 offset을 보냈는지는 원본 `CollectionAudit.entries[*].observed_raw/observed_offset`을 함께 대조해야 한다. 정제 view의 UTC 값만으로 모든 출처의 원본 시계가 정확했다고 주장하지 않는다.

## 판정 범위와 후속 확인

원래 B-02 목록의 실제 출처 시각·UTC 정규화·누락/지연 표시·사건 순서, 배포 실패와 자동 원복 전후 artifact 수집/검증은 위 **두 별도 실습 사례**와 production의 제한된 일반 수집으로 충족했다. S10의 현재 자동 CI 전달 경로는 앞선 2026-10-22 수동 가져오기와 구분한다. production 일반 수집에 CI 이력이 항상 있다는 뜻은 아니다.

- production에서 Event·허용 로그·지표·실제 배포 이력이 같은 장애 창에 모두 가용한 사례, 서로 다른 **실제** 원본 offset/장비 시계의 교차 검증, 저장 후 Event 원본 UID의 독립 재검증은 아직 없다. 이는 향후 증거 품질 개선이며 이번 체크리스트에 없는 신규 장애 주입을 완료 필수 조건으로 추가하지 않는다.
- CI 원복 성공은 실습 앱의 제한된 자동 복구 기록이다. 서비스 사용자 요청 경로의 정상 동작이나 AI의 원인 진단 적중률·조치 실행 성과를 뜻하지 않는다. 빠진 로그/지표와 부분 보고서는 그대로 남긴다.

이번 재검사는 **기존** GitHub artifact, worker PVC, 로컬 보존 JSON과 기존 수집기 PVC만 읽었다. 새 CI·장애·수집·모델 호출·클러스터 변경·비용 예약은 없었다. 다음 작업 권장 추론 깊이: **GPT-6 SOL high**.
