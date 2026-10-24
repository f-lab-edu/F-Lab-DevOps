# B-06-M5 실습 상시 모델 연결 모의 구현

상태: **2026-10-24 KST 로컬 모의 구현·검증 완료, 클러스터 배포 및 실제 모델 전송 전**. [M4 설계](B6-continuous-model-connection-design.md)의 첫 범위인 `ai-ci` worker와 `ai-ops-lab/lab-api`만 다룬다. 기본 배포 worker에는 새 연결 객체를 주입하지 않았고, 외부 모델 요청도 0회다. M3의 한 건 승인을 상시 연결 승인으로 사용하지 않는다.

## 구현한 처리 흐름

1. 최신 `main`의 실습 CI 전달·검증 코드를 현재 작업 브랜치의 모델 코드와 함께 사용한다. CI 입력은 저장소/워크플로/실행·artifact와 큐 HMAC을 검사하고, worker는 고정 snapshot과 CI 관측·규칙 보고서를 대조한다. 기존 S9/S11 실습 기능은 현재 브랜치 버전을 보존했다.
2. [worker.py](../src/ai_ops/worker.py)는 **명시적으로 `model_bridge`가 주입된 실습 전용 worker**에서만 규칙 보고서 commit 후 [continuous_ci_bridge.py](../src/ai_ops/continuous_ci_bridge.py)를 부른다. 이 연결은 완료 보고서·같은 CI 실행의 snapshot·대상 UID를 재검증하고 [model_projection.py](../src/ai_ops/model_projection.py)의 허용 필드만 정제한다. 완료 직후 중단된 경우 다음 worker 순환에서 기존 완료 건을 다시 확인해 같은 outbox 키로 재구성한다. 규칙 보고서의 내용·상태는 모델 경로 실패로 변경하지 않는다.
3. [continuous_model.py](../src/ai_ops/continuous_model.py)는 별도 비공개 SQLite outbox와 모의 캠페인 장부를 관리한다. 저장된 전송 view에는 원본 CI·로그·이벤트·배포 문장, Pod 실명, 원본 ID 대응표를 넣지 않는다. CI 정보만 있고 허용 가능한 Pod/설정/지표 근거가 없다면 `projection_rejected`만 기록하고 규칙 보고서를 유지한다. 이 모의 경로는 대응표를 영속 저장하지 않으며 원본 snapshot은 기존 비공개 큐 저장소에 보존한다.
4. 캠페인은 고정 대상/Deployment UID·클러스터 지문·정책·프롬프트·구현 코드·가격/모델 계획을 결합한다. 모의 승인도 결합 해시와 승인자를 요구한다. 모델 처리 전 24시간 만료, 최대 생성 10건, 건당 $0.10 예약, UTC 일일 $1 한도를 하나의 SQLite transaction에서 확인한다. 날짜 역행은 거부하고 여러 캠페인이 같은 일일 장부를 공유한다.
5. 모의 transport는 사전 지정한 토큰 계산/생성 응답만 반환하며 네트워크 기능이 없다. 새 결과는 같은 정제 view의 규칙 요약과 분리 저장하고 근거 ID·입력 해시·미실행 조치 계약을 검증한 뒤 `needs_review/semantic_review_required`로 둔다. HTTP 응답 유실을 가정한 실패나 재시작 후 `sending` 기록은 자동 재전송하지 않는다. 정제 view/결과 본문은 7일, 중복·예산 기록은 90일 보존한다.

## 검증과 제한

- 관련 경로 106개 및 현재 브랜치 전체 Python **439개 시험 통과**. 전체 시험의 로컬 HTTP/TLS 서버는 샌드박스 밖의 loopback 허용 환경에서 실행했다.
- CI 완료→outbox 한 건, 재시작 중복 차단, CI 입력 변조·대상 UID 불일치 차단, CI 자료만 있을 때 규칙 결과 보존, 승인 전 호출 0회, 모의 결과의 사람 검토 상태, 10건/UTC 일일 한도, 중지·만료·가격 만료·시계 역행, 본문 정리와 불명 전송 재시도 차단을 확인했다. [집중 시험](../tests/test_continuous_ci_bridge.py), [장부 시험](../tests/test_continuous_model.py).
- 이번 단계에는 클러스터 gateway 프로세스, 별도 Secret/PVC, worker↔gateway 서비스 인증, egress proxy, NetworkPolicy, 운영용 TLS transport가 없다. 현재 코드는 모의 응답만 처리하며 `CallPlan.execution_mode=live`와 임의 callable transport를 거부한다. 배포된 운영·CI worker의 기존 규칙 경로는 그대로다.
- M6에서 gateway 전용 저장소·서비스/자격·네트워크 경계를 **기본 비활성** 상태로 추가하고 이미지/배포·설치/정리 절차를 검증한다. M7의 실제 캠페인은 별도 명시 승인과 새 데이터 제어/가격 확인 전까지 시작하지 않는다. B 전체 완료와 C 진입은 계속 보류한다.
