# AI 운영 조사 기능

현재 **A0~A4와 A6 로컬 검증을 완료**했다. A5의 합성 증거 전용 OpenAI Responses 어댑터는 실제 AI 보고서 생성·근거 인용·감사 기록을 확인했고 현재 엔진으로 사용한다. 실패 실행의 알려진 부분 사용량과 S2 정상 캐시 miss 재검증도 마쳤다. 다만 원래 계획한 HolmesGPT와 동등 조건의 4~6건 품질 비교는 아직 수행하지 않아 **A5 전체 상태는 검증 중**이다. 그 판단과 한계는 [A5 문서](docs/A5-engine-decision.md)에 있다. A6는 현재 어댑터의 보고서 계약·실패 시 규칙 부분 결과·보안 경계를 모의 응답으로 검증했다. **A7은 새 v3 합성 입력에서 수정 고정 증거 AI와 도구 탐색 AI를 각각 48/48회 완료**했다. 기대 진단 자동 채점은 각각 45/48, 36/48이다. 첫 반복의 Codex 예비 검토와 Claude 외부 AI 교차검토, [AI 전용 원격 CI 통과](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/37116993482), [앱 CI 원격 검증](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/37118365182), [두 workflow의 PR 이벤트 검증](https://github.com/f-lab-edu/F-Lab-DevOps/pull/16)을 확인했다. 익명 A/B 검토 화면과 원본 증거 전용 수동 분석 화면·빈 기록표를 준비했지만 사람의 독립 의미 채점과 수동 분석 기준선은 남았다. B-02 읽기 수집기는 모의 API로 검증했으며 실클러스터 연결과 변경 실행은 후속 구현 범위다.

- [A0 코드 기준선과 확인 결과](docs/A0-baseline.md)
- [데이터 출처·보관 기준](docs/data-policy.md)
- [런북 목록과 검증 메타데이터](runbooks/index.yaml)
- [A1 계약·검증 범위](docs/A1-contracts.md)
- [A2 저장 증거 처리·합성 재현 자료](docs/A2-evidence.md)
- [A3 로컬 사건 조사·CLI·보고서](docs/A3-local-investigation.md)
- [A4 저장 증거 조회 도구·제한·감사 기록](docs/A4-replay-tools.md)
- [A5 엔진 결정·AI 연결·남은 실제 검증](docs/A5-engine-decision.md)
- [A5 ChatGPT Plus 비채택 검토 기록](docs/A5-plus-feasibility.md)
- [A6 보고서·실패·보안 검증](docs/A6-report-failure-validation.md)
- [A7 고정 평가 진행 기록](docs/A7-fixed-evaluation.md)
- [A7 독립 의미 검토 화면 템플릿](scripts/review_template.html) — 로컬 생성 화면 오른쪽 패널에 검토 절차와 결과 반영 안내 포함
- [A0·A1 파일별 처리 흐름 이미지](docs/assets/ai-ops-a0-a1-flow.png)
- [캐시 런북](runbooks/cache.md)
- [DB 런북](runbooks/database.md)
- [경보·배포·롤백 런북](runbooks/deployment.md)

런북은 코드에서 확인한 동작과 사건별 판단 절차를 제공한다. 실환경 정상 동작이나 특정 사건의 원인을 입증하는 자료는 별도로 수집해야 한다. `index.yaml`에 등록된 문서만 향후 AI 입력 후보로 사용한다. 기준선 기록·데이터 정책·평가 정답은 자동으로 모델에 제공하지 않는다.

`src/ai_ops/contracts.py`가 요청·증거·보고서의 Pydantic 계약을 정의한다. `schemas/`는 같은 모델에서 생성한 JSON Schema다. 등록 대상은 `config/targets.example.json` 형식으로 관리한다. 예제 cluster ID는 가상 값이다.

B-02의 첫 묶음은 `b_contracts.py`의 대상·수집 설정과 `collection.py`의 읽기 경계·captured 증거 정규화를 구현했다. `config/targets.practice.example.json`은 조사 대상과 연결 별칭·컨테이너·유한 query dimension을 등록하고, `b-runtime.example.json`은 시간·응답 크기·로그·예산 상한을 정한다. 예제 연결 주소는 `null`, 모델 사용은 `false`이며 실제 환경값으로 확정한 설정이 아니다. `metric-queries.json`은 내용 해시를 확인하는 다섯 query ID의 5분 instant PromQL과 scrape 품질 식을 담는다. 해시는 변경 추적용이며 설정 작성자의 신뢰를 대신하지 않는다.

`SnapshotBuilder`는 주입된 읽기 transport와 clock을 사용해 대상·GET 경로·query·시간·크기를 검사하고, 수집 후보를 기존 `EvidenceBundle`과 별도 `CollectionAudit`으로 고정한다. 내부 시각은 UTC, 원본 offset은 증거에 보존하며 화면 표시는 기존 KST 함수를 재사용한다. 403·timeout·미래/모호한 시각·잘못된 값은 내용 없는 누락·거부·무효 항목으로 남긴다. captured 로그·이벤트는 허용된 진단 표식만 보존해 사용자 이름·item ID·URL·자유 문장을 버리므로 정보 손실이 있다. B-02 두 번째 묶음에서 `live_collectors/`의 API 응답 추출·owner UID 검증·scrape 품질 판정·GET transport를 구현했다. 세 번째 묶음에서 조사 CLI·공통 captured 기본 거부 정책·규칙 보고서 연결을 구현했다. captured AI 엔진 활성은 자료 검토 이후의 별도 작업이다. 현재 모의 시험은 실제 EKS 연결 검증을 뜻하지 않는다.

B-02 두 번째 묶음의 [snapshot.py](src/ai_ops/live_collectors/snapshot.py)는 `collect_snapshot(settings, target, transport=..., clock=..., route=..., operation=...)`로 세 수집기를 연결해 메모리의 bundle/audit를 반환한다. 기본 예제의 주소가 비어 있으면 실제 연결을 거부한다. 모의 transport를 전달하면 서버·API 키 없이 실행할 수 있다.

- [kubernetes.py](src/ai_ops/live_collectors/kubernetes.py): Deployment·Service selector와 Deployment→ReplicaSet→Pod의 controller UID를 확인한다. 목록은 최대 12개와 `continue`를 검사하고 초과 시 페이지를 계속 읽지 않고 truncated로 남긴다. Pod별 현재 상태·구성·현재 로그와 재시작 시 이전 로그를 읽으며, Pod 이름 재사용·수집 중 Deployment 변경을 확인한다. 로그는 Pod별 현재/이전 합계 200줄·전체 원시 64KiB를 제한하고 이벤트는 owner UID와 원본 시각으로 최대 20건을 선별한다. 읽은 bytes는 버린 응답도 예산에 포함한다. UID·resourceVersion·누락·잘림은 감사에 기록한다.
- [prometheus.py](src/ai_ops/live_collectors/prometheus.py): 고정 instant query의 t0·vector·label·유한 값을 확인한다. `up`, 최근 5분 `min_over_time(up)`, scrape 표본 수(기본 최소 8), 마지막 scrape 시각(기본 60초)을 같은 series 집합에서 확인한다. warning/info·범위 응답·NaN은 거부한다. 캐시 결과 일부가 없으면 확보한 증가량과 누락 outcome을 함께 남기고 누락을 0으로 채우지 않는다. 비율·지연은 요청 수부터 읽고 0건이면 no_traffic으로 기록한다. 실제 시계열 전체 범위와 scrape 주기는 B-03에서 대조해야 한다.
- [deployment.py](src/ai_ops/live_collectors/deployment.py): 등록 Argo Application의 repo/path/destination을 대조하고 전체 Git revision·reconciledAt·최근 최대 10개 배포 이력의 revision/deployedAt만 선별한다. 현재 Healthy나 성공 이력으로 실패·롤백·복구를 만들지 않는다. CI 전후 artifact는 아직 연결하지 않아 missing_history로 남긴다. secretKeyRef의 존재는 실제 URL 값·연결 성공·DB 역할로 해석하지 않는다.
- [transport.py](src/ai_ops/live_collectors/transport.py): 표준 라이브러리로 신뢰 origin에 GET을 보낸다. 환경 proxy·redirect·압축을 끄고 TLS CA 검증·파일 Bearer token·읽기 바이트 상한을 적용한다. token은 매 요청 파일에서 다시 읽으며 오류 본문·credential을 감사나 증거에 저장하지 않는다. 새 SDK 의존성은 추가하지 않았다. socket timeout·경과 시간·늦은 응답 거부를 적용하지만 OS DNS 등 동기 I/O를 별도 프로세스로 강제 종료하는 기능은 없으며 실환경 기한 검증이 남았다.

모의 API와 HTTP opener 대체 시험은 `tests/test_live_collectors.py`에 있다. 실제 클러스터·모델 호출은 하지 않았다. collection 완료와 진단 완료는 구분하며, 세 번째 묶음에서 기대 Pod 수·UID 소유 관계·전체 Pod Ready·필수 출처·수집 완료를 함께 검사하도록 규칙 보고서를 연결했다. 불완전한 수집은 partial로 저장한다.

B-02 세 번째 묶음의 `IncidentStore.investigate_bundle`은 파일과 메모리 입력이 공유하는 해시·대상·시간·멱등성·저장 진입점이다. `investigate_snapshot`은 B 설정과 수집 감사를 추가 검사하고 `rules-captured-v1`로 조사한다. 요청 source는 기존 파일 입력 `manual_replay`, 새 수집 입력 `manual_snapshot`으로 구분하며 JSON Schema도 갱신했다. `<store>/<incident>/<run>/collection-audit.json`에 bundle hash와 수집 감사를 0600으로 저장하고 metadata의 감사 hash와 연결한다. `show`는 입력·감사·보고서의 정합성을 다시 검사한다.

`data_policy.py`는 두 AI 어댑터·조사 진입점·재생 실행기의 모델 입력을 `practice` synthetic으로 제한한다. captured·혼합·production 자료는 API 키·토큰 계산·모델 transport·비용 예약 전에 거부한다. 검토된 captured 자료를 허용하는 flag는 아직 없다. 이 정책은 출처 표기의 진실성을 자동 판별하는 기능이 아니므로 실제 자료를 synthetic으로 바꿔 전송하면 안 된다. B CLI의 모델 사용은 비활성 상태이며 수집 후 규칙 분석만 지원한다.

실환경 대상·권한·주소를 B-03에서 확인한 뒤 사용할 명령은 다음과 같다. **현재 예제 주소는 null이므로 예제 설정 그대로는 실제 수집이 시작되지 않는다.** 연결 설정·질문에 Secret을 넣지 않고 별도 신뢰 파일과 마운트 경로를 사용한다.

```sh
PYTHONPATH=ai-ops/src python3 -m ai_ops collect-investigate \
  --bindings /path/to/trusted-targets.json \
  --runtime /path/to/trusted-b-runtime.json \
  --metric-queries ai-ops/config/metric-queries.json \
  --target url-shortener-practice \
  --store /private/tmp/aiops-b-practice \
  --mode rules --request-id practice-snapshot-001 \
  --question '수집 시점의 장애 징후와 부족한 근거를 정리해줘'
```

`--route`·`--operation`은 등록 목록의 dimension으로만 제한할 수 있다. 실제 재수집은 시각·bundle hash가 달라지므로 새 request ID를 사용한다. 같은 snapshot·감사·질문·request ID의 내부 재전송은 기존 run을 반환한다. 반환 코드는 보고서 완료 0, 수집 불완전 또는 AI fallback 부분 결과 1, 입력·정책·설정 오류 2, 사용자 취소 130이다. 수집 불완전은 `partial` / `collection_incomplete`이며 AI 오류로 표시하지 않는다. 전 출처 누락이면 규칙상 증거 부족·최신 상태 unknown을 저장하고 모델은 호출하지 않는다.

captured의 정상 상태는 기대 Pod 수와 중복 없는 UID가 맞고, 전체 Pod가 Running/Ready이며 필수 종류·다섯 query와 수집 완료가 확인돼야 허용한다. 캐시 error/unavailable·RedisError·API P95 1초/DB P99 0.5초 경보 기준과 충돌하면 정상으로 확대하지 않는다. 이 지연 기준은 현 앱 chart와 맞춘 고정 규칙이며 chart 변경 시 재대조해야 한다. 진단 결론과 최신 상태는 별도이며 정상 cache miss가 보여도 전체 건강은 unknown일 수 있다. CI 과거 artifact의 누락도 현재는 partial로 다루므로 보수적인 판정이다. 누락은 보고서에서 최대 30건, 전체 입력·감사에는 모두 남긴다.

세 번째 묶음은 Python 3.12.14/Pydantic 2.13.5에서 전체 125건(신규 조사·정책·CLI 경계 14건 포함)을 통과했다. 기존 A fixture 56개의 규칙 보고서 JSON은 수정 전과 동일했고 A7 유료 평가를 재실행하지 않았다. 다음은 B-03의 image·chart·읽기 권한·통신·PVC 구성이다. 실환경 시각/권한·CI 전후 artifact·captured 자료 검토와 모델 활성은 남아 있다.

B-03 첫 묶음은 [배포 진단 안내](docs/B3-deployment.md)에 기록했다. 독립 Dockerfile·기본 비활성 Helm chart·읽기 RBAC·DNS/API/Prometheus 통신 정책·PVC·수동 비활성 GitOps 정의와 네트워크 없는 로컬 진단 모드를 구현했다. 전체 Python 시험 130건과 Helm/권한 정적 검사를 통과했다. 후속 로컬 검증에서 linux/amd64 이미지 실제 빌드·통신 없는 실행·합성 규칙 조사/재조회·배포 로컬 진단을 통과했다. 원격 CI 결과는 배포 진단 안내의 Actions 링크에서 확인한다. B-03 전체는 진행 중이며 다음은 독립 ECR/이미지 등록 경로와 CI 롤백 전후 artifact 보존이다. 실제 환경·경보·모델 활성은 여전히 대기 중이다.

B-03 두 번째 묶음은 [독립 이미지 등록·배포 관측 보존](docs/B3-image-and-observations.md)을 구현했다. ECR/등록 전용 OIDC 역할은 Terraform 기본 비활성이며 수동 workflow도 publish=false가 기본이다. 검증한 같은 이미지의 digest·소스/lock/runbook hash를 기록하고 GitOps는 수정하지 않는다. 앱 CI에는 배포 검증 직후·롤백 검증 뒤 허용 필드만 남기는 artifact 보존을 추가했으며 수집 실패가 복구를 막거나 원래 실패를 성공으로 바꾸지 않는다. Python 142건·Terraform validate/변경 파일 fmt·Helm/셸 검사를 통과했다. 실제 AWS·이미지 등록·artifact 업로드·B 입력 import는 아직 미검증/미연결이다.

Python 3.11 이상과 `requirements.lock`의 고정 의존성을 사용한다. 저장소 루트에서 로컬 검증:

```sh
python3 -m venv /tmp/ai-ops-venv
/tmp/ai-ops-venv/bin/python -m pip install --require-hashes --only-binary=:all: -r ai-ops/requirements.lock
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python -m unittest discover -s ai-ops/tests -p 'test_*.py' -v
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/build_holdout_fixtures.py --check
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/build_holdout_v2_fixtures.py --check
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/build_holdout_v3_fixtures.py --check
PYTHONPATH=ai-ops/src /tmp/ai-ops-venv/bin/python ai-ops/scripts/export_schemas.py
git diff --exit-code -- ai-ops/schemas
```

`requirements.lock`은 현재 애플리케이션 잠금 파일의 동일한 Pydantic 2.13.5 계열 해시를 독립적으로 복사했다. 개발 중 계약을 바꾸면 schema를 다시 생성하고 함께 검토한다. JSON Schema는 필드 구조를 공유하지만 대상 등록·hash 비교·시간 관계 같은 교차 검증은 Python 코드에서 수행한다.

저장 증거는 `python -m ai_ops validate-bundle`로 검사하고, `investigate --mode rules`로 사건 보고서를 만든 뒤 `show`로 다시 볼 수 있다. 명령별 `--bundle`, `--registry`, `--store` 예시와 저장 위치 기준은 [A3 문서](docs/A3-local-investigation.md)에 있다.

`investigate --mode ai`는 현재 `practice` 합성 증거와 **별도 과금되는 API 키 경로**만 지원한다. 키를 처음 등록하거나 교체할 때는 저장소 루트에서 아래 명령을 실행하고 `OpenAI API 키:` 입력창에 붙여넣으면 된다. 입력 문자는 화면에 표시되지 않는다. 이 설정 명령은 API를 호출하거나 비용을 발생시키지 않는다.

```sh
./ai-ops/scripts/aiops-local configure-api-key
./ai-ops/scripts/aiops-local api-key-status
```

키는 Git 저장소 밖 `~/.local/share/url-shortener-ai-ops/.openai-api-key`에 사용자 전용 권한(0600)으로 보관된다. 기본 저장소를 바꾸려면 설정·조사 명령에 같은 `--store` 경로를 지정한다. 기존 `OPENAI_API_KEY` 환경변수가 설정되어 있으면 저장된 키보다 우선한다. 키 값은 채팅·명령행 인자·프로젝트 파일에 입력하지 않는다. 이 컴퓨터의 Python 실행 환경은 `aiops-local`이 자동으로 찾는다. 다른 환경에서는 Python 3.11 이상에 `requirements.lock` 의존성을 설치하고 `AI_OPS_PYTHON`으로 경로를 지정하면 된다. 비용·출처 제한, 실행 예시 및 실제 점검 결과는 [A5 문서](docs/A5-engine-decision.md)에 있다. Plus 플랜 사용 경로는 선택하지 않았으며 [검토 기록](docs/A5-plus-feasibility.md)만 보관한다.

AI 호출이 인증·네트워크·시간·예산 문제로 실패하면 검증된 저장 증거의 규칙 분석을 `partial`로 저장한다. CLI는 실패 이유를 보여주고 종료 코드 1을 반환한다. 보고서 형식·근거·보안 검증을 통과하지 못한 모델 초안은 부분 결과로 위장하지 않고 실패 상태로 남긴다. 자세한 조건은 [A6 검증 기록](docs/A6-report-failure-validation.md)을 참고한다.

B-03의 [AWS 적용 전 점검·비교 계획](docs/B3-aws-preflight.md) 후 별도 AI state를 선택했다. [AI 등록 전용 Terraform](../terraform/ai-ops-registry/README.md)으로 ECR/IAM을 분리하고 기존 OIDC는 조회만 한다. AWS 읽기 plan에서 전용 root는 생성 4개·수정/삭제 없음, AI 정의를 제거한 기존 production root는 변경 없음이었다. 2026-10-17 후속 작업에서 별도 state에 4개를 실제 생성하고 GitHub 등록 환경·변수를 설정했다. 적용 후 plan은 변경 없음이다. 실제 OIDC 인증·이미지 등록·main 반영은 대기 중이며 [적용 기록](docs/B3-registry-apply.md)을 따른다.

운영자 CLI의 파일 인자와 기본값은 [지정 폴더 정책](docs/cli-file-policy.md)을 적용한다. 기존 임의 경로는 기본 개인 저장 폴더로 옮기거나 운영자 환경변수 `AI_OPS_FILES_ROOT`에 전용 폴더를 등록해야 한다. 일반 결과는 기존 파일을 덮어쓰지 않는다.


B-03 현재 클러스터 연결(2026-10-19): 사용자가 production 읽기 전용 조사를 선택했다. [대상 등록](config/targets.production.json)의 명시적 운영 대상과 [비활성 설정 초안](config/b3-values.preflight.yaml)을 준비했으며 수집·규칙 보고서·저장/재조회 경로에서 실제 production 환경을 보존한다. production 자료의 외부 모델 전송은 계속 차단한다. 로컬 Python 154건·실습/운영 Helm 읽기 경계 검증을 통과했다. [사전 점검 기록](docs/B3-cluster-preflight.md)에 선택과 검증을 반영했으며 수정 코드의 이미지 등록·AI Pod 배포는 아직 남아 있다.


B-03-SEC-01 진행(2026-10-19): AI 베이스를 공식 Python 3.11.17-alpine3.24 digest로 고정하고 zlib를 보안 수정 패키지로 갱신했다. runtime 빌드 도구 제거 후 로컬 Trivy 0건·runtime 34건·호스트 154건을 확인했다. 현재 production 읽기 전용 지원과 함께 새 이미지 반영을 준비하며 **ECR 재등록·재스캔 전에는 완료가 아니다**. [대응·검증 기록](docs/B3-image-security.md)을 따른다.

## B-04 영속 큐·보관·worker 배포 모드

[영속 수동 큐](docs/B4-durable-queue.md), [7일 보관표·연계 삭제](docs/B4-retention.md), [worker 배포·관측](docs/B4-worker-deployment.md)을 구현했다. 기본 chart는 비활성·진단 모드를 유지하며, 승인된 새 이미지와 worker overlay를 명시해야 연속 처리한다. 수집은 읽기 전용, 보고서는 규칙 모드이며 외부 AI·경보 수신·운영 변경은 활성화하지 않는다. Python 207건·Helm 진단/worker 경계 검사 통과. 원격 이미지·실제 Pod 재시작 복구 검증은 대기다.

2026-10-20 **B-04 worker 배포·실제 Pod 복구 검증 완료**: PR #40·main c17ac3d, 원격 Python 207건·이미지 실행 통과와 사용자 승인 이미지 등록 후 Helm revision 2로 배포했다. 실제 수집 checkpoint 뒤 AI Pod를 교체해 2차 partial·재수집 없음·기존 보고서/중복 키/동일 PVC/앱 불변을 확인했다. Prometheus up=1·31개 지표와 조사 주체의 exec/Secret/patch 거부도 확인했다. 예약 보관 정리는 후속이다. [배포·운영 기록](docs/B4-worker-deployment.md).

B-04 후속 [예약 보관 정리](docs/B4-retention-schedule.md)를 구현했다. 기존 worker가 작업 사이에 정리하며 새 Kubernetes 권한이나 CronJob을 추가하지 않는다. 기본 비활성이고 worker overlay에서만 명시적으로 활성화한다. 실제 배포 상태는 해당 문서 상단을 따른다.

2026-10-20 **B-04 예약 보관 정리 배포·검증 완료**: PR #42·원격 217건/이미지 통과와 사용자 승인 등록 후 Helm revision 3에서 활성화했다. 실제 운영 큐의 예약 실행·38개 지표와 합성 만료 요청 자동 삭제/미만료·보호 파일 보존·재시작 후 예약 유지를 확인했다. 운영 큐 2건·보고서·동일 PVC·앱 Ready 2개는 보존됐다. B-04 현재 범위 완료, 다음은 B-05 경보 연동이다. [최신 기록·실제 운영 만료 삭제 미시험 한계](docs/B4-retention-schedule.md).

## B-05 첫 구현 묶음

[경보 접수·TLS·분기 준비](docs/B5-alert-ingestion.md)를 로컬 구현했다. 등록 대상의 개별 경보를 기존 큐에 원자 접수하고 중복/해제/재발을 구분한다. 내부 TLS·파일 인증, 수신기 토큰 미마운트·정확한 Alertmanager ingress, 기본 비활성 앱 경보 labels와 Slack/Watchdog 보존 분기 초안을 준비했다. 현재 checkout Python 237건·Helm 검사 통과. 실제 수신기·Secret·Alertmanager 경로는 아직 활성화하지 않았으며 B-05 전체 완료가 아니다.

2026-10-20 B-05 구현은 [PR #44](https://github.com/f-lab-edu/F-Lab-DevOps/pull/44)의 main 기반 238건·전체 CI 통과 후 병합했다. 전용 인증 Secret과 실제 Alertmanager의 분기 판정 검증을 준비했으며, [이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39393659389)은 사용자 승인 후 성공했다. Helm revision 4의 TLS 수신기를 배포하고 실제 내부 경보→규칙 보고서 및 기존 자료 보존을 확인했다. 운영 경보 연결은 PR #45·239건 CI 후 적용했고, 실제 Alertmanager 바이너리의 AI 전용 격리 발생·해제 전달과 네트워크 경계를 확인했다. 실제 외부 알림·AI 장애 중 사람 알림 전달 시험은 후속이다. [최신 현황](docs/B5-alert-ingestion.md).

## B-06 실환경 운영 평가·데모

[운영 평가와 남은 조건](docs/B6-operational-evaluation.md), [안전한 읽기 데모 절차](docs/B6-operational-demo.md), [검증 범위에 맞춘 포트폴리오 초안](docs/B6-portfolio.md)을 관리한다. 실제 큐·권한·중복·외부 전송 차단은 확인했으며, 자료 품질·독립 실습 시나리오·라이브 모델 평가는 미완료다. B 전체 완료나 C 실행 권한 부여로 해석하지 않는다.

2026-10-21 [독립 실습 환경과 시나리오 준비](docs/B6-isolated-lab.md)를 완료했다. `ai-ops-lab`의 앱·Redis·단일 PostgreSQL·읽기 수집기 4개와 실제 수집·권한·Prometheus 표본 준비를 확인했다. 운영 연결값·PVC를 재사용하지 않으며 새 RDS·영구 볼륨·LB는 없다. 시나리오 정답 기준·복구 절차와 전체 253개 시험을 기록했지만 실제 장애 주입과 라이브 모델 비교는 후속이다.

후속 [S2 cold miss·S1 Redis 접근 실패](docs/B6-cache-scenarios.md)를 실제 재현·원복했다. S2는 허위 후보 0개, S1은 error 증가와 HTTP 200 fallback·캐시 지표 근거 후보 1개를 확인했다. 두 사례 모두 자료 부족 판단과 외부 모델 차단을 유지했고 로컬 267개 시험이 통과했다. 다른 변형·S3/S4 및 나머지 시나리오와 라이브 모델 비교는 남아 있다.

[S4 앱 DB 세션 지연](docs/B6-db-delay-scenario.md)은 요청별 750ms 주입과 실제 DB histogram·지연 해제·데이터 원복을 확인했다. 규칙은 지연 근거를 설명하고 원인 확정을 유보했다. 로컬 274개 시험이 통과했으며 RDS 서버 장애나 라이브 모델 검증과 구분한다. 다음은 S3 이미지 실패·원래 digest 복구다.

[S3 이미지 취득 실패·복구](docs/B6-image-failure-scenario.md)는 실제 ErrImagePull·Ready 0·실패 이벤트 수집과 원래 digest 복구를 확인했다. 규칙 v2가 native Failed 이벤트의 이미지 오류 표식을 원인 후보로 설명하지 못해 needs_review로 남겼다. 다음은 저장 증거를 이용한 규칙 보완과 기존 보고서 호환성 검증이며 B-06 완료로 표시하지 않는다.

[규칙 v3의 동일 저장 증거 비교](docs/B6-image-rules-v3.md)는 Pod 소유·같은 이미지·근접 이벤트를 대조해 후보 0→1과 partial/unknown 유지, 기존 보고서 보존을 확인했다. 로컬 289개 시험을 통과했으며 수정 이미지의 실제 배포·새 보고서 검증은 후속이다.

## S10 배포 이력·GitOps 준비

[CI 출처 사전 점검](docs/B6-ci-history-preflight.md)과 [실습 GitOps 준비](docs/B6-gitops-preparation.md)는 운영자용 준비 기능이다. 실행·artifact 출처를 검사하고 실습 앱 한 개의 수동 Argo 정의와 공유 Lease를 준비한다. worker에 GitHub 인증·쓰기 권한을 추가하지 않는다. 실제 실습 설치·Argo 인계·CI 자동 롤백·조사 입력 연결은 후속 작업이다.

2026-10-22 [S10 실습 재설치·Argo 인계](docs/B6-gitops-adoption.md)를 완료했다. 현재 실습 Application은 읽기 수집과 제한된 수동 sync만 연결했으며 CI 실패·자동 롤백은 후속이다.
