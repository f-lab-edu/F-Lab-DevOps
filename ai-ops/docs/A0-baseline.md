# A0 코드 기준선과 확인 결과

- 확인일: 2026-09-27
- 검증 Git SHA: `e4c51585bbc449f444527ed3c0614aac4b4b5700`
- 기존 계획 기준 `e4c5158`과 동일한 커밋이다.
- 착수 시 `git status --short` 출력은 비어 있었다. Git으로 추적되는 파일과 표시되는 untracked 파일 기준이며, 무시된 실습 문서·Secret 파일의 변경 유무는 포함하지 않는다.
- 완료 범위: 코드 대조, 런북 선별·작성, 출처/hash 고정, 로컬 검증. 실클러스터·Redis/DB·AWS·Argo·Slack·외부 모델 호출은 수행하지 않았다.

## 1. 확인한 판단 기준

| 항목 | 현재 코드에서 확인한 사실 | 런북 반영 |
|---|---|---|
| 캐시 사용 불가 | URL 미설정과 초기 연결·설정 오류 모두 unavailable | 사용 의도와 정제 구성 없이 서버 장애 확정 금지 |
| 조회 오류 | RedisError와 JSONDecodeError를 잡고 write 세션 사용 | 예외 종류 구분, 네트워크 장애 단정 금지 |
| 캐시 지표 중복 | hit 증가가 파싱보다 앞서 있어 hit와 error가 한 요청에 함께 증가 가능 | operation 카운터를 요청당 단일 결과로 취급하지 않음 |
| 캐시 저장 실패 | warning만 기록하며 해당 error 결과를 증가시키지 않음 | 조회·저장 오류 분리, 지표만으로 모든 오류 부재 확정 금지 |
| 최근 쓰기 | commit 전 marker, commit 후 세대 증가; 기본 구간 60초 | 정상 Primary 경로 후보와 commit 성공을 구분 |
| DB 실제 역할 | read URL 없음/동일이면 write engine 공유 | replica 로그만으로 물리 Replica 확정 금지 |
| DB 지연 | 앱 코드의 측정 구간에 캐시·ORM·실습 지연 등이 포함될 수 있음 | 순수 SQL 시간 또는 DB 서버 원인으로 단정 금지 |
| 운영 구성 | 풀 write 2+1/read 3+1, timeout 10초; Terraform Multi-AZ Primary와 Read Replica | 실제 런타임 설정·AWS 상태는 별도 확인 |
| 알림 식별 | sum/by 집계 후 namespace/service 손실, rule labels는 severity만 | B 전에 대상 라벨/매핑·수신 payload 검증 |
| 경보 의미 | hit 비율과 cache 접근 오류는 다른 분모; 모든 트래픽/오류를 표현하지 않음 | 경보별 표본·누락·관측 범위 설명 |
| 배포 검증 | 정확한 GitOps revision + Synced/Healthy + readyz/items | 이전 revision 성공 오인 금지 |
| 롤백 | 검증 실패 시 이전 desired image tag 복원·재검증; CI는 실패 유지 | 과거 실패·현재 복구 구분, 이전 tag 정상성 별도 검증 |
| 증거 보존 | 실패/복구 로그는 있으나 Pod/event 구조화 artifact 업로드 없음 | B의 보존 작업으로 남김 |

각 런북의 source 파일과 SHA-256은 [index.yaml](../runbooks/index.yaml)에 고정했다. hash가 달라지면 재검토한다. 현재 runtime에서 index를 읽고 허용 목록을 집행하는 기능은 아직 없다.

## 2. 참고 문서 선별 결과

| 문서·범위 | 결정 | 코드 대조 근거 |
|---|---|---|
| 루트 README의 nginx 구조·Multi-AZ 비활성 설명 | 현행 AI 입력에서 제외 | prod는 별도 Traefik routing, rds.tf는 multi_az=true |
| week6-7의 nginx/alb chart 예시 | 현행 AI 입력에서 제외 | chart 기본 ingressClass는 traefik, prod chart ingress 비활성 |
| week10의 Redis 중단 → CacheHitRateLow 설명 | 현행 판단 기준으로 사용하지 않음 | 현재 hit/miss 분모는 unavailable/error를 제외하며 요청량 조건도 있음 |
| week12의 NGINX/Traefik 비교·이전 실습 절차 | 전체 자동 입력에서 제외 | 과거 비교·주입 절차가 섞여 있어 범위를 선별해야 함 |
| 과거 DB pool 값·단일 AZ 가정 | 현재 기준으로 사용하지 않음 | config/values와 Terraform을 직접 기준으로 삼음 |
| `docs/runbooks/`의 상태 저장·비밀번호 회전·의존성·보존 문서 | 이번 입력 범위 밖 | 이번 cache/database/deployment 조사 런북과 목적이 다름. 정확성 전체 검토를 완료했다는 뜻은 아님 |
| 이번 cache/database/deployment 런북 | 입력 후보로 등록 | 현재 코드에서 재작성, 범위·검증 SHA·출처/hash 고정 |

제외는 문서 전체가 쓸모없다는 뜻이 아니다. 과거 사건 자료로 사용할 때는 당시 환경·revision과 목적을 명시한다. 원본 실습 문서와 README를 일괄 수정하지 않았다.

## 3. 로컬 검증과 한계

| 검증 | 명령·환경 | 결과·범위 |
|---|---|---|
| 기준선 | 저장소 루트에서 `git rev-parse HEAD`, `git status --short` | 위 SHA, 착수 시 출력 없음 |
| 캐시 helper 회귀 | url-shortener에서 `python3 -m unittest discover -s tests -p 'test_*.py' -v` | 2건 통과, Python 3.9.6. FakeRedis는 TTL 만료를 구현하지 않으며 실제 Redis/DB·라우트 시험 아님 |
| Chart lint | `helm lint url-shortener/url-shortener-chart -f url-shortener/url-shortener-chart/values.prod.yaml` | 통과, icon 권고만 있음. 사용 Helm v4.1.3이며 기존 CI의 v3.19.5와 다름 |
| 운영 대상 렌더링 | 아래 명령 | rule·monitor·deployment·service 생성 확인. 런타임 설치 검증 아님 |
| Bash 구문 | `bash -n .github/scripts/verify-gitops-deployment.sh` | 통과. 실제 Argo/smoke/rollback 실행 아님 |
| 런북 무결성 | index의 문서·근거 파일 SHA-256 재계산, 로컬 Markdown 링크 확인 | 일치. 의미 검증은 위 코드 대조 범위에 한정 |

렌더링 명령(Secret 템플릿 제외):

```sh
helm template url-shortener url-shortener/url-shortener-chart \
  --namespace url-shortener \
  -f url-shortener/url-shortener-chart/values.prod.yaml \
  --show-only templates/prometheusrule.yaml \
  --show-only templates/servicemonitor.yaml \
  --show-only templates/deployment.yaml \
  --show-only templates/service.yaml
```

렌더링된 6개 경보는 severity만 명시하고, workload/service는 url-shortener-api/url-shortener-svc다. 운영 payload·external labels·실제 scrape와 전달·ingress·권한 통제·자동 롤백 성공은 미검증이다. A0 완료는 런북 준비 완료이며 A단계 조사 기능 완료가 아니다.

## 4. 후속 작업

1. A1: 패키지·typed contract·schema·대상/시간/version 입력 검증. Python·의존성 버전은 이 단계에서 확정한다.
2. A2: 출처·마스킹·hash·경로 검증과 S1/S2/S9 개발 fixture. 아직 실제/합성 bundle은 생성하지 않았다.
3. CI(C0): 기존 캐시 테스트를 app-only/tests-only PR의 CI에 연결. 현재 dependency workflow는 앱/테스트 경로 자체를 trigger로 포함하지 않고 unittest 실행 step도 없다.
4. B: 대상 라벨·라이브 권한/수집·경보 전달·실패 전후 artifact 보존 검증.
5. C: 이전 정상 image 근거·특정 diff/revision 승인·별도 실행·롤백 경쟁 방지 구현.

다음 작업 추천: **GPT-6 SOL / high**. A1의 요청·증거·보고서 계약과 거부 조건을 일관되게 설계하고 검증해야 한다.
