# B-06 독립 실습 환경과 시나리오 준비

상태: **실습 Pod 4개 설치·인가·실제 수집·Prometheus 표본 준비 검증 완료, 장애 주입 미실행**. [본문 없는 실측](assets/B6-isolated-lab-validation.json)을 참고한다.

이번 작업은 **장애를 넣어도 운영 앱·운영 Redis·RDS를 바꾸지 않는 실습 대상과 평가 기준을 준비하는 단계**다. `ai-ops-lab` namespace에 앱·PostgreSQL·Redis·수집기를 각각 한 개씩 배포한다. 실습 준비와 장애 진단 성공은 별개로 기록한다.

## 동작 흐름과 구성

검증한 이미지 digest·API /32 주소 확인 → 실습 전용 암호 생성 → 고정 리소스 경계 검사 → 별도 namespace 설치 → Pod 4개 준비 → 권한·연결 검사 → 실습 대상 실제 자료 수집 → 규칙 v2 보고서 저장·재조회 순서다.

운영 앱과 같은 이미지의 복사본이 별도 Redis·PostgreSQL만 사용한다. 운영 DB 연결값이나 개인 API 키는 복사하지 않는다. 실습 API의 지연·캐시 우회 옵션만 켜며 최대 지연은 1초다. 수집기는 실습 namespace의 읽기 Role만 가지고 Secret·exec·patch·운영 namespace 읽기 권한은 없다. 운영자가 실습 Pod에서 검사할 때 사용하는 exec 권한과 수집기 권한을 구분한다.

모든 앱·DB·수집 입력은 `emptyDir`다. 새 RDS·EBS·LoadBalancer·Ingress·Route53·ECR·Terraform state를 생성하지 않는다. Pod 재생성이나 namespace 삭제 시 실습 DB와 보고서가 없어질 수 있으므로 유효한 실험 결과는 삭제 전에 승인된 비공개 저장소로 보관해야 한다. Git에는 본문·암호 대신 시각·상태·해시만 남긴다. 기존 노드 CPU 350m·메모리 576Mi를 요청하며 기존 AWS 비용과 공유한다. 추가 노드 생성 여부는 별도 관측해야 하므로 추가 비용이 항상 0이라고 단정하지 않는다.

NetworkPolicy는 실습 내부 앱→DB/Redis, 앱·수집기의 DNS, Prometheus→앱 지표, 수집기→지정 Kubernetes API /32·Prometheus만 허용한다. NetworkPolicy 선언·인가 검사는 실제 모든 네트워크 차단 경로 시험을 대신하지 않는다. 현재 CNI standard 모드의 Pod 초기 허용 구간 한계는 유지한다.

## 파일별 역할

| 파일 | 역할 |
|---|---|
| `src/ai_ops/b6_lab.py` | 고정 실습 리소스 생성과 경계 검사. 운영 namespace·외부 노출·쓰기 권한 등 정의 변경을 거부한다. |
| `scripts/prepare_b6_lab.py` | 검증한 이미지와 API 주소로 비공개 manifest를 생성한다. 실습 암호가 포함되므로 Git에 올리지 않는다. |
| `scripts/deploy_b6_lab.py` | 기본은 정의 검사만 한다. `--apply`에서 context·소유 label·같은 manifest 해시를 확인한 뒤 설치하고 운영 앱·AI StatefulSet·PVC 식별자 불변을 확인한다. |
| `scripts/probe_b6_lab.py` | 실제 인가·앱/Redis/DB·Prometheus·수집/규칙 보고서 재조회를 검사한다. 조사 본문을 외부로 출력하지 않는다. |
| `evals/rubrics/b6-live/scenarios.json` | 주입 의도·기대 관측·오판 금지·복구 기준. 실제 정답 기록 전에는 계획표이며 AI 조사 입력에 넣지 않는다. |
| `tests/test_b6_lab.py` | 운영 연결·권한·외부 노출 변조 거부, 다른 context·다른 소유 namespace의 변경 차단을 검사한다. |

## 준비·설치·연결 확인

현재 계정·클러스터 전용 도구다. 다른 계정·클러스터에는 자동 적용하지 않는다. 새 설치 전 앱·AI image digest는 승인된 현재 실행값으로, PostgreSQL·Redis는 공식 저장소의 확인한 linux/amd64 digest로 고정한다. API /32는 재설치마다 새로 확인한다. `inputs.json`의 키는 `app_image`, `collector_image`, `postgres_image`, `redis_image`, `api_server_cidrs`다. mutable tag는 입력할 수 없다.

```bash
# 아래 경로는 이번 실행의 비공개 폴더다. 재설치에서는 새 검증값으로 준비한다.
export AI_OPS_FILES_ROOT=/private/tmp/ai-b6-lab-20261021
export PYTHONPATH=ai-ops/src
python ai-ops/scripts/prepare_b6_lab.py --input "$AI_OPS_FILES_ROOT/inputs.json" --output "$AI_OPS_FILES_ROOT/manifest.json"
python ai-ops/scripts/deploy_b6_lab.py --manifest "$AI_OPS_FILES_ROOT/manifest.json" --output "$AI_OPS_FILES_ROOT/definition-check.json"
python ai-ops/scripts/deploy_b6_lab.py --manifest "$AI_OPS_FILES_ROOT/manifest.json" --output "$AI_OPS_FILES_ROOT/deployment-check.json" --apply
python ai-ops/scripts/probe_b6_lab.py --output "$AI_OPS_FILES_ROOT/readiness-check.json"
```

출력 파일은 덮어쓰지 않으므로 이미 실행했다면 새 파일명을 사용한다. 같은 namespace에 다른 manifest를 적용하는 것은 거부한다. 암호를 다시 생성한 manifest로 기존 환경을 업데이트하지 않는다. 실패하면 비공개 결과 파일과 실습 Pod 상태를 확인한다. 자동으로 운영 리소스를 재시작하거나 namespace를 삭제하지 않는다.

## 장애 실험 순서와 검토 방법

첫 실행은 S2 cold miss로 정상 동작을 확보하고, S1 Redis 접근 실패 → S4 제한된 DB 세션 지연 → S3 이미지 취득 실패 순서로 수행한다. S9·S11 하위 사례는 그다음이다. 각 사례의 시작 전에 **실제 주입 상태와 복구용 값**을 별도 기록하며 조사 질문에 시나리오 ID·정답·주입 명령을 넣지 않는다. 질문은 중립적인 문장으로 고정한다.

1. 실습 Pod Ready·Redis PING·DB 역할·HTTP 기준선과 앱 이미지/replicas/env를 기록한다.
2. 고정 namespace의 해당 자원만 제한된 값으로 변경한다. 요청은 최대 20회다. 변경 실행기는 운영자 도구이며 에이전트에게 쓰기 권한을 주지 않는다.
3. 실제 관측 시각·주입 성공 여부를 기록한다. 주입이 실패하면 진단 성공으로 채점하지 않는다.
4. Prometheus 30초 간격·최소 8개 표본과 5분 창을 고려한다. 표본이 부족하거나 이전 사건이 창에 남으면 점수를 유보한다. 고정 sleep만으로 표본 확보를 단정하지 않는다.
5. 실습 수집기로 조사 한 건을 만들고 근거 ID·관측 사실·원인 후보·판단 유보를 주입 기록과 대조한다. 규칙 보고서와 라이브 AI 보고서를 구분한다. 현재 외부 모델은 비활성이고 captured 전송 차단도 유지한다.
6. 성공·실패와 관계없이 finally 복구를 수행하고 Ready·Redis·HTTP를 확인한다. 원복 실패 시 다음 시나리오를 시작하지 않는다. 반복 전 이전 5분 지표 창의 영향이 해소됐는지도 검사한다.

초기 준비 묶음에는 자동 장애 실행기를 포함하지 않았다. 후속 [S2·S1 실행기](B6-cache-scenarios.md)에서 제한값·finally 복구·시나리오별 실측 파일을 구현한다. 준비 완료를 S1~S11 통과로 표시하지 않고 실제 실행 결과와 구분한다.

## 반드시 구분할 한계

- 단일 PostgreSQL에 write/read URL을 같이 연결한다. 앱 로그의 논리적 `db_route=replica`가 실제 Replica 서버를 뜻하지 않는다. `/_db`의 두 역할은 모두 Primary다. S9의 marker 우회는 시험할 수 있지만 복제 지연·Replica 전환은 별도 환경이 필요하다.
- Argo Application `ai-ops-lab/lab-target`은 존재하지 않는다. 가짜 Healthy 객체나 이력을 만들지 않고 수집 시 missing을 남긴다. S10에는 실습 전용 CI·자동 롤백·검증한 이력 import가 추가로 필요하다.
- 앱의 Redis 최초 접속 실패는 `unavailable`, 이미 연결된 뒤 실패는 `error`가 될 수 있다. 둘을 같은 값으로 강제하지 않는다. 존재하지 않는 시계열은 0이 아니다.
- DB 지연 헤더는 앱 세션의 `pg_sleep`이다. RDS 서버 문제의 재현으로 서술하지 않는다. 실습 Secret·원문·정답표는 모델에 넘기지 않는다.

## 종료·전체 인프라 정리 연결

프로젝트 전체 AWS 정리 시 실습 namespace도 EKS 삭제 전에 정리한다. 목적 label과 context를 확인한 뒤 `kubectl delete namespace ai-ops-lab --wait=true --timeout=180s`를 실행한다. 이 명령은 실습 DB·보고서·Secret을 삭제하므로 필요한 결과를 먼저 보관한다. 다른 namespace를 순회 삭제하지 않는다. PVC·PV·LB를 만들지 않았으므로 실습 전용 EBS나 state는 없다. 실행 실패 시 finalizer를 강제로 제거하지 않고 남은 자원을 조사한다.

재설치에서는 운영 앱·Prometheus·AI 이미지 준비 후 새 digest·API /32로 manifest를 다시 생성한다. 실습은 운영 설치의 필수 구성 요소가 아니므로 운영 설치 완료 여부와 별도 표시한다. 임시 폴더의 Secret manifest를 Git·공유 사이트·CI artifact에 업로드하지 않는다.

## 2026-10-21 실행 결과

13:42 KST 설치 완료 시 앱·DB·Redis·수집기 4개가 Ready였으며 운영 앱·AI StatefulSet·기존 PVC의 UID/generation은 같았다. 이후 실습 앱 /readyz·/items·/items/_db는 200, Redis PING 성공, 두 DB 세션 모두 단일 Primary임을 확인했다. 수집 identity는 실습 대상 읽기를 허용하고 Secret·patch·exec·운영 namespace 조회는 거부됐다.

첫 읽기 조사에서는 Prometheus up=1이었지만 표본 품질 조건 미충족으로 지표가 scrape_unavailable이었다. 기준을 낮추지 않고 충분한 표본이 쌓인 뒤 확인했다. 후속 실제 값은 최근 5분 표본 10개·up=1·scrape_health=1이며 최소 8개 조건을 충족했다. 새 captured 조사 결과는 available 13·missing 8, 규칙 v2 사실 3개·가설 0개·partial/insufficient_evidence/unknown이다. 없는 Argo Application과 CI 이력·트래픽 부족은 계속 누락으로 남겼다. 두 검사는 실습 연결 준비 확인이며 정상 진단·장애 정확도 표본으로 채점하지 않는다.

새 경계 시험 7개와 전체 Python 253개가 통과했다. 추가 읽기 지표 품질 확인까지 실환경에서 실행했다. 앱의 설치 초기 재시작 2회가 관측됐으며 이를 의도적 장애 주입 성공으로 계산하지 않았다. 실제 S1~S11 장애 시나리오 실행은 0회, 외부 모델 호출도 0회다. 다음은 S2 cold miss 기준선과 S1 Redis 실패의 제한된 주입·복구 실행기 구현이며 추천 추론 깊이는 GPT-6 SOL high다.

## 2026-10-22 실제 인계 완료

[현재 실측](B6-gitops-adoption.md)에서 PR #53 병합 SHA 고정·독립 실습 재설치·정책/권한 실제 검사·Argo 수동 인계와 수집을 확인했다. 운영 식별자·실습 Pod는 유지했고 임시 토큰은 폐기했다. 위 10월 21일 자료와 구분하며 CI 자동 롤백·S10 전체 완료를 의미하지 않는다.
