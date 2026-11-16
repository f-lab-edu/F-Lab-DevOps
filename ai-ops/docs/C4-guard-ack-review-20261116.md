# C-04 감시 Lease 세션 검토와 반영

## 문제와 변경

감시 전환·ACK 대기에서 반복 kubectl 실행과 인증 초기화를 줄이기 위해 각 감시가 고정 Lease만 처리하는 개인 Unix proxy를 준비 단계에 연다. 원격 감시와 로컬 감시, 실제 실행기의 세션은 각각 독립이다. 인증·만료 갱신·upstream TLS는 기존 kubectl/kubeconfig가 처리하며 새 권한·자격은 추가하지 않는다.

proxy는0700 디렉터리/0600 Unix 소켓과 단일 API 경로 필터를 사용하며 TCP 포트를 열지 않는다. helper는 고정 이름/namespace/UID/resourceVersion의 GET/PUT만 처리한다. GET 응답을 캐시하지 않으며 CAS409만 새 원본으로 재시도한다. 두 guard가 동시에 ACK를 써도 다른 guard의 정확한 ACK를 보존한다. 통신/인증/원본 변경은 중지하며 자동 우회하지 않는다.

## 검토에서 보완한 사항

1. API 그룹 이름의 점을 정규식 wildcard로 허용하지 않도록 고정 경로를 escape했다. 유사하지만 다른 API 그룹 경로가 upstream에 도달하지 않는 회귀 검증을 추가했다.
2. SIGTERM이 proxy 생성 중 도착하면 자식 PID를 보관한 뒤 기존 예외/원복/finally 경로로 전달한다. 회수 중에는 종료 신호를 잠시 보류해 자식·소켓 회수가 끝나도록 한다. 기존 signal handler는 복원한다. 생성 중 취소와 실제 자식 종료 시험을 추가했다. SIGKILL 회수를 보장하는 주장은 하지 않는다.

## 검증과 범위

이 저장소의 전체 **886개 시험·skip0**, fixture4종·schema변경0·배포 정의·구문/diff 검사를 통과했다. 로컬 kubectl v1.34.1의 TLS 대역에서 인증 재사용/만료 갱신, 잘못된CA·경로/메서드/Host거절, CAS경쟁·양쪽ACK보존, SIGTERM 뒤 proxy/소켓 제거를 확인했다. CI는 실습과 같은 고정 Linux kubectl v1.35.8을 공식 배포/checksum으로 받아 동일한 로컬 fixture를 실행한다. 이 fixture는 AWS/GitHub/실습 클러스터·모델을 호출하지 않는다.

AI 저장소는 실행기 세션/상세 arm 계측과 감시 공통 코드를 관리한다. 플랫폼은 현재 원격 감시를 실행하므로 공통 helper·guard CLI·9파일 source hash 계약·검증만 최소 반영한다. 전체 AI 실행기나 repository literal을 일괄 복제하지 않는다. 기존 실제 guard workflow의 환경 승인과 main 조건은 그대로다.

시작15초, intent/readiness별 실제 사용자 승인과 한 번만 소비하는 표식, 현재 owner/UID/activation·양쪽 최신 ACK, 전후 snapshot과 최종 ACK, 안내 후120초 리뷰·시작 후210초 리뷰마감·240초 소유원복·300초 독립원복 시작·120초 회복관찰을 유지한다. Ready schema·실제 원복 경로·IAM·Terraform·앱/worker 배포 정의는 변경하지 않았다. 과거 승인/receipt는 재사용하지 않는다.

전체15초 시작의 실환경 성공은 아직 증명하지 않았다. 반영 후 새 AI/guard source·정상CI/현재 binding을 대조하고 새 준비/실행 승인을 받아 검증한다. 새 복구 PR 정확 head의 실제 사람 리뷰와 CI는 별도다. 기존 SQLite ResourceWarning은 이 변경의 해결 대상으로 보고하지 않는다. C04/C05 최종·A5/A7·전체 실행 연결 전환도 완료로 표시하지 않는다.

검증 수치와 공통 코드 해시는 [검증 JSON](assets/C4-guard-ack-review-20261116.json)에 있다.
