# B-06-M7-2 실습 모델 배포 연결 준비

상태: **2026-10-24 KST 코드·시험·이미지 등록 및 비활성 준비 리소스 적용 완료, 클러스터 활성화·캠페인 승인 전**. [M7-1 정제 전송 계약](B6-model-campaign-preflight.md)에 이어 실제 전송을 맡을 gateway 처리 루프와 현재 대상 재확인, 비활성 배포 준비 정의를 추가했다. 이 문서와 패치는 활성화 승인이 아니다.

현재 클러스터에서 확인한 내부 Kubernetes API 경로 `10.0.10.113/32`, `10.0.11.214/32`, `172.20.0.1/32`로 준비 manifest를 만들고 EKS 서버 dry-run 후 적용했다. 이 주소는 해당 설치의 시점 값이며 재설치 때 다시 조회한다. 적용 후 gateway/proxy는 각각 **0/0**, gateway `AI_MODEL_ENABLED=false`, `ai-model` Pod·Secret은 0개, PVC는 Pending이었다. 기존 `ai-ci/ci-worker`는 1/1이고 모델 연결 환경변수·Secret 마운트가 없었다. 권한 대조에서 gateway 서비스 계정은 고정 `lab-api` Deployment와 `ci-config` ConfigMap GET만 허용받고 모델 Secret GET은 거부됐다.

[PR #72](https://github.com/f-lab-edu/F-Lab-DevOps/pull/72)가 main에 병합된 뒤 [이미지 등록 실행 #40078057507](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40078057507)이 성공했다. 등록 기록의 소스 revision은 `8e9560825fcd57310aabe4ad88851a3f69c65ff6`, ECR digest는 `sha256:04e28f8e3879d46166dfb857766425af6e9923e9354a4f8b4153ed6d142044ed`이며 ECR에서 같은 digest를 확인했다. 이 digest와 위 API 주소로 새 manifest를 생성해 불변식 검사·EKS 서버 dry-run을 통과한 후 적용했다. 재확인 결과 gateway/proxy는 새 digest로 각각 **0/0**, gateway 스위치는 `false`, `ai-model` Pod·Secret은 0개, PVC는 Pending이며 worker 환경변수는 `HOME`뿐이다. 즉 이미지는 등록·배포 정의에 반영됐지만 모델 프로세스와 API 호출은 시작되지 않았다.

재설치 때는 등록된 비공개 출력 폴더(`AI_OPS_FILES_ROOT`)에 `PYTHONPATH=ai-ops/src python ai-ops/scripts/prepare_model_service.py --prepared --image <새 승인 digest> --api-cidr <현재 내부 API /32> --output <새 manifest.json>`으로 생성한다. API 주소가 여러 개면 `--api-cidr`를 반복한다. manifest에서 두 복제수 0, gateway 스위치 false, Secret 오브젝트 부재를 확인하고 `kubectl apply --dry-run=server -f <manifest.json>`을 거쳐 적용한다. 생성된 파일은 설치 입력일 뿐 활성 패치가 아니다.

## 처리 흐름

1. 기본 `model_service.build_service()`는 계속 gateway/proxy **0/0**, `AI_MODEL_ENABLED=false`, Secret 생성 없음이다. `build_prepared_service()`는 같은 비활성 상태에서 gateway의 짧은 Kubernetes 조회 토큰, 이름을 고정한 읽기 전용 Role/RoleBinding, 내부 API `/32`와 `ci-worker`→gateway 8081 경로만 추가한다. Secret과 worker 활성 설정은 넣지 않는다.
2. 승인된 캠페인으로 gateway를 시작하려고 할 때 실행 진입점은 장부의 승인 상태, 코드·이미지·키 지문을 확인한다. 승인/Secret/마운트가 하나라도 없으면 HTTP 서비스를 열기 전에 실패한다. 승인되지 않은 현재 배포에서는 실행되지 않는다.
3. 대기열에서 **아직 전송하지 않은 한 건**을 고른 뒤, Kubernetes API GET으로 `ai-ops-lab/lab-api` Deployment UID, `ai-ci/ci-config`의 설정 해시, `ai-model/model-gateway`의 이미지 digest를 재확인한다. 마운트된 API CA 해시와 캠페인의 클러스터 지문도 고정값과 대조한다. 하나라도 다르면 비용 예약과 모델 호출 전에 멈춘다.
4. 일치하면 M7-1 장부가 UTC 일일 예산을 먼저 예약하고 토큰 계산 1회·생성 1회만 시도한다. 완료 보고서는 규칙 보고서와 분리해 `needs_review`로 남긴다. `sending` 중 재시작·응답 유실은 `unknown_send`로 보류하며 자동 재전송하지 않는다. 중지·만료는 새 처리 전에 확인한다.
5. `build_activation_patches()`는 **이미 승인된** 캠페인의 정확한 결합 해시·이미지로만 gateway/proxy/worker 패치를 만든다. 결과에는 Secret 이름만 있고 값은 없다. 이 패치는 이번 단계에서 생성·적용하지 않았다.

## 아직 필요한 활성화 절차

- 최신 이미지 digest 등록과 비활성 준비 manifest 적용은 완료했다. 재설치·캠페인 승인 직전에는 현재 클러스터 API `/32`, 대상 UID, CI 설정 해시, 클러스터 CA 해시와 지문을 다시 읽고 새 manifest의 서버 dry-run 및 0/0 상태를 재검증한다. prepared manifest만으로는 실제 모델 프로세스가 시작되지 않는다.
- API 키와 worker↔gateway 자격을 별도 Secret에 안전하게 생성·회전한다. gateway PVC를 첫 consumer에서 초기화하고, 별도 사용자 승인에 결합된 캠페인 장부를 넣는 절차가 필요하다. 현재는 이 bootstrap과 Secret 생성이 없다.
- 현재 가격·선택적 데이터 공유 설정·제공자 보관 조건을 재확인하고 자동 미래 입력/기간/건수/비용을 명시한 검토안을 사용자에게 제시한다. 그 승인 뒤에만 활성 패치, proxy/gateway/worker 순서 배포, 정상 1건·제한된 장애 1건 검증으로 넘어간다.

현재는 **실제 모델 API 호출 0회**를 유지한다. M7-2의 코드 준비와 로컬 시험은 B 단계 완료나 운영 효과 검증이 아니다. 다음 작업 권장 추론 깊이는 **GPT-6 SOL high**다.
