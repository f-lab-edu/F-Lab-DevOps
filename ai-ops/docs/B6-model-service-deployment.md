# B-06-M6 실습 모델 gateway·proxy 비활성 배포

상태: **2026-10-24 KST 코드·기본 비활성 정의 준비**. M5의 규칙 보고서와 모의 outbox는 유지한다. M3의 입력 한 건 전송 승인은 상시 캠페인 승인이 아니다. 이 단계는 실제 모델 전송이나 운영 자동 조치를 켜지 않는다.

## 이번 검증 결과

- `model_service.py`의 기본 비활성 정의와 gateway/proxy 경계 시험 6개, 현재 작업 브랜치 전체 Python **445개 시험**이 통과했다. 기존 전체 시험의 SQLite `ResourceWarning`은 종료 상태에 영향을 주지 않았다.
- AWS 계정 `716174522908`와 현재 EKS 컨텍스트를 확인했다. `ai-ci`/`ai-ops-lab` namespace가 있고 `ai-model`은 없었다. `ai-ops-gp3-encrypted`는 `encrypted=true`, `WaitForFirstConsumer`, `Delete`다.
- 현재 ECR digest `e0bbce4f…`로 **형식 검사 전용** manifest를 만들었다. 새 코드는 이 이미지에 포함되지 않는다. 신규 namespace와 그 namespace의 항목을 한 번에 server dry-run하면 namespace가 실제로 생기지 않아 후속 항목이 `NotFound`로 실패한다. 기존 `ai-ci` namespace로만 바꾼 사본을 `kubectl apply --dry-run=server --validate=strict`로 검증했으며 ServiceAccount 2개, PVC 1개, Deployment 2개, ClusterIP Service 2개, NetworkPolicy 3개가 모두 통과했다. 이 사본을 실제 적용하지 않았다.
- 새 이미지가 없고 Pod 네트워크 허용/거부 실측이 전이므로 M6은 **진행 중**이다. `ai-model` 자원·Secret·EBS를 실제 생성하지 않았고 모델 API 호출은 0회다.
- server dry-run 뒤 `ai-model` namespace는 여전히 없고, 기존 `ai-ci/ci-worker`·`ci-relay`는 각각 1/1 Ready, 운영 `ai-ops` StatefulSet은 1/1 Ready다. 이 조회만으로 알림 경로 전체의 통합 동작을 검증했다고 보지는 않는다.

## 무엇을 배치하는가

`model_service.py`는 `ai-model` 독립 namespace에 `model-gateway`와 `model-egress-proxy` ServiceAccount·Deployment·ClusterIP Service, 전용 `model-ledger` 1Gi 암호화 PVC 및 기본 거부 NetworkPolicy를 만든다. 두 Deployment는 **replicas=0**이고 gateway 환경은 `AI_MODEL_ENABLED=false`다. manifest에는 Secret 값이 없다. `model-openai-key/token`과 `model-client-auth/token`은 gateway에만 마운트하도록 참조한다. proxy에는 키·PVC·Kubernetes API 토큰이 없다. 기존 `ai-ci` worker와 운영 `ai-ops` 배포 정의는 바꾸지 않는다.

gateway의 `/v1/analyze`는 서비스 인증, 32KiB 제한, `ModelView` 정형 계약과 해시를 검사한 뒤에도 **503**만 반환한다. M7 승인과 별도 구현 전에는 성공 결과를 만들지 않는다. proxy는 `CONNECT api.openai.com:443` 한 목적지만 파싱하고 공개 DNS 주소만 선택한다. API 키는 proxy로 보내지 않고 gateway에서 목적지 TLS 인증을 수행한다. redirect·임의 API 경로·응답 크기 초과·동일 경로 재시도는 전송부에서 거부한다. 현재 `continuous_model.py`는 live 계획을 거부하므로 gateway에 실제 전송 경로가 연결되지 않았다.

## 적용 전과 비활성 배포

1. `main`에 gateway/proxy 코드가 포함된 **승인된 새 이미지 digest**를 얻고 ECR 등록·보안 검사를 확인한다. 기존 digest로 만든 manifest는 API 형식 검사에는 쓸 수 있어도 실행 준비가 됐다는 증거가 아니다.
2. 현재 AWS 계정·EKS 컨텍스트, `ai-ci`/`ai-ops-lab` namespace, `ai-ops-gp3-encrypted`의 `encrypted=true`·`WaitForFirstConsumer`·`Delete`, 기존 `ai-model` 동명 자원의 소유권을 확인한다.
3. 개인 비공개 폴더에서 `PYTHONPATH=ai-ops/src python ai-ops/scripts/prepare_model_service.py --image <승인 digest> --output <신규 manifest.json>`을 실행한다. 출력에 키·실제 승인·원본 자료가 없는지 확인한다.
4. `kubectl apply --dry-run=server -f <manifest.json>`으로 API 검증 후, manifest의 두 Deployment가 replicas=0이고 Secret 오브젝트가 없음을 재확인한다. namespace가 처음 생기는 경우 다른 항목의 server dry-run은 namespace의 실제 생성 후 별도로 수행한다.
5. 비활성 정의를 적용해 두 Service의 외부 IP가 없고 Deployment 0/0, PVC가 `WaitForFirstConsumer` 대기이며 EBS 실물이 아직 생기지 않았는지 확인한다. Secret은 M7의 승인·키 회전 절차 전까지 생성하지 않는다. `ai-ci` worker egress를 이번 단계에서 열지 않는다.

## 네트워크 검증과 활성화 전 중단 조건

정적 시험은 gateway egress가 같은 namespace proxy의 TCP 8080만, proxy ingress가 gateway의 TCP 8080만, proxy egress가 DNS·공개 TCP 443만, gateway ingress가 `ai-ci/ci-worker`의 TCP 8081만 허용하는지 확인한다. proxy 프로그램은 목적지 호스트/포트와 DNS 결과를 다시 검사한다. **NetworkPolicy만으로 FQDN을 묶었다고 주장하지 않는다.** 실측 Pod 허용/거부 시험은 새 코드가 포함된 이미지와 별도 승인 자격을 갖춰 임시로 scale할 때 수행해야 한다. CNI 정책 적용 시점의 짧은 default-allow 가능성도 기존 클러스터 절차의 한계로 기록한다.

M7 전까지 필요한 작업은 전용 클러스터 Secret 두 개와 `ai-ci` 측 별도 인증 자격 회전, worker→gateway egress의 좁은 추가, gateway의 승인·예산 원자 장부/live 처리 연결, 새 코드 해시 결합, 정제 view와 결과의 사람 검토, Pod 내부 실제 직접 인터넷 거부 시험이다. **현재 replicas를 1로 올리거나 키를 넣어도 모델 호출이 작동하지 않는다.** 캠페인 승인 없이 임의로 활성화하면 안 된다.

## 정리·재설치

AWS 전체 삭제에서는 `ai-model`을 실습/운영 namespace와 EBS CSI보다 먼저 정리한다. gateway/proxy를 0으로 확인하고 캠페인을 중지해 `sending` 불명 요청을 성공으로 바꾸지 않는다. `model-ledger`의 PV·Volume ID와 보존 결정을 기록하고 PVC→PV/EBS 실물 부재→Secret/Service/NetworkPolicy/ServiceAccount/namespace 순으로 확인한다. StorageClass는 다른 AI PVC가 모두 사라진 뒤 기존 AI 정리 절차에서 제거한다. Route53과 Terraform state는 기존 범위를 따른다. 재설치 때 예전 키·캠페인 승인·UID·이미지 digest·볼륨 ID를 재사용하지 않고 새 클러스터 결합을 검증한다.
