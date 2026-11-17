# 지표 초기화 코드 검토와 로컬 검증

2026-11-17. 플랫폼 main 54bb497 기반의 변경이다.

트래픽이 없을 때 cache 오류·HTTP 상태·DB latency 시계열 자체가 빠지는 문제를 줄이기 위해, 앱 lifespan에서 고정된 label의 child를 생성한다. inc/observe 호출은 없으며 카운터·histogram count/sum은 0이고 반복 초기화도 실제 관측을 보존한다. 없는 요청이나 latency 표본을 만들지 않는다.

캐시 endpoint 2×결과6, DB operation4, HTTP route/method/status22 조합만 초기화한다. 실제 요청에서는 기존 route template과 unmatched 처리를 유지한다. 새 실제 상태 코드의 계측을 차단하지 않는다. /metrics scrape는 카운터에 포함하지 않는다.

독립 체크아웃에서 앱 시험7개(기존 cache2+신규5), 실제 ASGI lifespan/metrics 검사와 Ruff 통과. 이전 로컬 기록의8개는 원래 작업 폴더의 기존 시험3개를 포함한 수치이며 이번 최신main 체크아웃은7개다. ASGI 검사는 DB startup 대역, DB connect/외부 socket 차단으로 실행했다. idle0→합성 요청3개만 증가·route template·scrape 제외·DB 관측0 확인. CI에는 앱 lock과 같은 prometheus-client0.25.0의 해시 고정 시험 의존성만 추가한다.

이번 변경은 코드·로컬 검증이며 실제 Prometheus 표본·Slack 응답의 partial 해소를 보장하지 않는다. 시간 제한·승인·원복·Terraform·배포 정의는 변경하지 않는다. 배포/실습 workflow의 기존 비활성 상태를 유지한다. 재설치 후 새 이미지·새 worker·정상 CI·사용자의 새 Slack 요청으로 검증한다.
