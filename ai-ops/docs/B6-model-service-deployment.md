# B-06-M6 실습 모델 gateway·proxy 비활성 배포

상태: **2026-10-24 KST 기본 비활성 배포·임시 Pod 네트워크 실측 완료**. M5의 규칙 보고서와 모의 outbox는 유지한다. M3의 입력 한 건 전송 승인은 상시 캠페인 승인이 아니다. 이 단계는 실제 모델 전송이나 운영 자동 조치를 켜지 않는다.

## 이번 검증 결과

- [PR #67](https://github.com/f-lab-edu/F-Lab-DevOps/pull/67)의 계약·이미지 CI 통과 후 `main` SHA `9c2a29f2…`를 병합했다. 사용자 승인한 [이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40071684645)은 성공했다. ECR digest `sha256:14678e4858ba365fc784ccf989fb0a81b16794e3746c2f72bb3d63a9233801c9`를 artifact와 ECR 실물에 대조했고 이미지 검사 `COMPLETE`, 지적 0건을 확인했다.
- `ai-model` namespace를 먼저 생성한 뒤 전체 manifest가 EKS 서버의 strict dry-run을 통과했다. ServiceAccount 2개·PVC 1개·Deployment 2개·ClusterIP Service 2개·NetworkPolicy 3개를 적용했다. 두 Deployment는 **0/0**, Secret 0개, PVC는 `WaitForFirstConsumer`의 Pending 상태라 EBS는 아직 생성되지 않았다. 암호화 StorageClass는 `encrypted=true`, `WaitForFirstConsumer`, `Delete`다.
- 첫 실측에서 gateway의 proxy TCP 8080은 허용됐지만 DNS egress 누락 때문에 서비스 이름 조회가 실패했다. gateway에 kube-system CoreDNS Pod의 UDP/TCP 53만 추가한 뒤 이름 조회와 proxy 연결이 성공했다. 같은 gateway 임시 Pod에서 임의 호스트 CONNECT는 403, 고정 `api.openai.com:443` CONNECT는 200, proxy 우회 공개 TCP 443은 시간 초과로 차단됐다. 고정 CONNECT 뒤 목적지 TLS 1.3 인증서 검증도 성공했다. **API 요청·키는 보내지 않아 모델 API 호출은 0회**다.
- 키·PVC·Kubernetes API 토큰을 마운트하지 않은 임시 Pod 두 개로 실측하고 삭제했다. 두 Pod에서 토큰/키/장부 파일 부재를 확인했다. 기존 `ai-ci/ci-worker`는 gateway Service 연결이 차단됐고, `ci-worker`·`ci-relay` 및 운영 `ai-ops`는 각각 1/1 Ready다. 이는 기존 알림 전달의 별도 종단 간 시험을 대신하지 않는다.

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

gateway egress는 같은 namespace proxy의 TCP 8080과 kube-system CoreDNS의 UDP/TCP 53만, proxy ingress는 gateway의 TCP 8080만, proxy egress는 DNS·공개 TCP 443만 허용한다. gateway ingress는 `ai-ci/ci-worker`의 TCP 8081만 허용하지만 기존 worker 쪽 egress는 아직 열지 않았다. proxy 프로그램은 목적지 호스트/포트와 DNS 결과를 다시 검사한다. **NetworkPolicy만으로 FQDN을 묶었다고 주장하지 않는다.** proxy Pod 자체가 침해되면 정책상 다른 공개 TCP 443에도 접근할 수 있으므로 고정 목적지 보장은 정상 proxy 코드의 검사 범위다. CNI가 `standard` 모드이므로 Pod 시작 직후 정책 적용 전 구간이 있을 수 있으며, 임시 Pod가 Ready가 된 뒤 실측한 차단을 모든 시작 순간의 차단으로 일반화하지 않는다.

M7 전까지 필요한 작업은 전용 클러스터 Secret 두 개와 `ai-ci` 측 별도 인증 자격 회전, worker→gateway egress의 좁은 추가, gateway의 승인·예산 원자 장부/live 처리 연결, 새 코드 해시 결합, 정제 view와 결과의 사람 검토다. **현재 replicas를 1로 올리거나 키를 넣어도 모델 호출이 작동하지 않는다.** 캠페인 승인 없이 임의로 활성화하면 안 된다.

## 정리·재설치

AWS 전체 삭제에서는 `ai-model`을 실습/운영 namespace와 EBS CSI보다 먼저 정리한다. gateway/proxy를 0으로 확인하고 캠페인을 중지해 `sending` 불명 요청을 성공으로 바꾸지 않는다. `model-ledger`의 PV·Volume ID와 보존 결정을 기록하고 PVC→PV/EBS 실물 부재→Secret/Service/NetworkPolicy/ServiceAccount/namespace 순으로 확인한다. StorageClass는 다른 AI PVC가 모두 사라진 뒤 기존 AI 정리 절차에서 제거한다. Route53과 Terraform state는 기존 범위를 따른다. 재설치 때 예전 키·캠페인 승인·UID·이미지 digest·볼륨 ID를 재사용하지 않고 새 클러스터 결합을 검증한다.
