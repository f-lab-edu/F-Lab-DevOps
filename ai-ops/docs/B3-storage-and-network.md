# B-03 암호화 저장소·API 통신 경로 준비

최신 자체 관측 상태: 2026-10-19 19:28:15 KST 자체 관측·중지/복구 실환경 검증 완료. PR #36·main e1b30b5의 새 이미지 등록 성공, ECR 검사 전체 0건과 코드 해시 일치를 확인했다. Helm revision 4에서 Prometheus up=1·진단 갱신·수동 수집/보고서 partial 각 1회 증가를 확인했다. AI StatefulSet 0→1 복구 후 동일 PVC·기존/신규 보고서 재조회와 해시·집계 보존, 앱 UID/generation 불변·Ready 2개를 확인했다. 외부 모델은 호출하지 않았다. [관측·중지/복구 절차](B3-observability-and-recovery.md). 아래 이전 이미지·배포 내용은 해당 시점의 이력이다.

최신 상태: 2026-10-19 18:12 KST 실제 수집→규칙 부분 보고서→PVC 저장→JSON/KST 재조회 연결 검증 완료. PR #34·#35 수정이 포함된 main 5d432f5 이미지로 일반 경로를 실행했고 Pod 2개 상태·설정, 배포/Argo·캐시 지표 등 7개 항목을 수집했다. 총 18개 중 available 7·invalid 2·missing 7·stale 2로, 자료 부족은 partial/unknown/insufficient_evidence로 유지했다. 이전 Pod의 저장 보고서도 새 Pod에서 해시 변화 없이 재조회했다. 외부 AI·키·예산 경로는 호출하지 않았고 기존 앱 UID/generation·Ready 2개는 유지됐다. [실제 수집 검증](B2-live-validation.md). 아래 이전 이미지·배포 결과는 해당 시점의 이력이다.

2026-10-19 실제 진단 배포: 수동 Helm으로 ai-ops namespace의 StatefulSet 한 Pod와 5Gi 암호화 gp3 EBS를 배포했다. 실제 읽기·Secret/다른 namespace 거부·쓰기/exec 권한 부재·선택한 수신/송신 차단·같은 AZ의 다른 노드 EBS 재연결과 합성 SQLite 보존을 확인했다. 같은 Pod의 토큰 자동 교체·새 토큰 API 200, 임시 nodeSelector 제거·최종 Ready·같은 PVC 자료 보존까지 확인했다. [실제 배포 기록](B3-diagnostics-deployment.md).

2026-10-19 KST **준비 완료**. AI 전용 `ai-ops-gp3-encrypted` StorageClass를 생성했고, 임시 Deployment 한 Pod에서 API 기본 차단→주소별 허용→실제 TLS GET을 비교했다. 확인한 설정을 [비활성 values](../config/b3-values.preflight.yaml)에 반영했다. 실제 진단 StatefulSet·PVC 배포는 다음 단계다.

## 이 작업의 목적과 흐름

보고서를 저장할 볼륨에 암호화를 요구하고, 수집기가 필요한 API·DNS·Prometheus에만 접근하도록 배포 조건을 정한다.

1. AWS 계정·클러스터·CNI/CSI·API Endpoint와 기존 StorageClass를 다시 읽어 대조한다.
2. 기존 앱과 기본 StorageClass를 바꾸지 않고 AI 전용 CSI StorageClass를 생성한다.
3. 별도 임시 namespace에 고정 이미지·non-root·읽기 전용 파일시스템·최소 GET 권한의 점검 Deployment를 만든다.
4. 기본 송신 차단 상태에서 API 접속 실패를 확인한 뒤 DNS·API `/32`·Prometheus만 허용한다.
5. CA·서버 이름·단기 토큰을 사용한 실제 API 응답과 Secret 조회 거부를 확인한다. 토큰·응답 본문은 기록하지 않는다.
6. 점검용 namespace와 대상 namespace의 임시 Role/RoleBinding을 제거하고 values는 비활성으로 유지한다.

## 암호화 저장소 정의와 소유권

[StorageClass 원본](../kubernetes/ai-storageclass.yaml)은 `ebs.csi.aws.com`을 사용하는 일반 EBS CSI 구성이다. EKS Auto Mode용 `ebs.csi.eks.amazonaws.com`을 사용하지 않는다.

| 항목 | 적용값·의미 |
|---|---|
| 이름 | `ai-ops-gp3-encrypted` |
| provisioner | `ebs.csi.aws.com` |
| 볼륨·파일시스템 | `gp3`, `ext4` |
| 암호화 | `encrypted: "true"`; 별도 KMS 키 ARN을 지정하지 않아 리전 기본 EBS 키 사용 |
| 볼륨 생성 시점 | `WaitForFirstConsumer` — 소비 Pod의 배치에 맞춰 AZ 결정 |
| 기본 StorageClass | `false`; 기존 `gp2`·앱 PVC 변경 없음 |
| 회수 | `Delete`; 향후 PVC를 명시 삭제하면 EBS도 정리 |
| Pod 삭제와 구분 | AI chart의 PVC 정책은 `whenDeleted/whenScaled: Retain`; StatefulSet 삭제만으로 PVC를 삭제하지 않음 |
| EBS 식별 태그 | `Project=urlshortener`, `Component=ai-ops` |

파라미터와 기본 키 동작은 [EBS CSI 공식 파라미터](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/docs/parameters.md)를 따른다. `WaitForFirstConsumer`·PV 회수 정책은 [Kubernetes StorageClass 설명](https://kubernetes.io/docs/concepts/storage/storage-classes/)을 참고한다.

이 리소스의 관리 원본은 위 Kubernetes manifest이며 운영자 적용으로 관리한다. **ECR/IAM용 Terraform root와 그 별도 state의 관리 리소스를 늘리지 않았다.** 클러스터 재설치 시 manifest도 다시 적용해야 한다. 설치·정리 가이드에 연결했으며 새 파일의 원격 반영 여부는 재설치 전에 별도 확인한다.

이번에는 PVC/EBS를 생성하지 않았다. 선언상의 암호화 설정만 확인했으므로 실제 `Encrypted=true`·KMS 키·AZ·attach·자료 보존은 실제 PVC 배포 후 AWS 조회로 검증해야 한다.

## 실제 API 경로 점검 결과

검사 대상은 계정 `716174522908`, 리전 `ap-northeast-2`, EKS `urlshortener`다. 임시 Pod는 `ai-ops-netcheck` namespace의 Deployment로 생성해 CNI 정책 집행의 대상이 되게 했다. UID 10001·read-only rootfs·모든 capability 제거·자동 토큰 마운트 비활성 상태에서 별도 projected token/CA를 읽었다. 임시 Role은 `url-shortener/url-shortener-api` Deployment 한 개의 GET만 허용했다.

| 정책 상태 | API Service `172.20.0.1` | Endpoint `10.0.10.162` | Endpoint `10.0.11.228` |
|---|---|---|---|
| 기본 차단 | TCP timeout | TCP timeout | TCP timeout |
| Endpoint `/32` 두 개만 허용 | 서비스 이름 경로 timeout | TLS GET 200 | TLS GET 200 |
| Service `/32` 한 개 추가 | `kubernetes.default.svc` TLS GET 200 | TLS GET 200 | TLS GET 200 |

최종 허용 주소는 `172.20.0.1/32`, `10.0.10.162/32`, `10.0.11.228/32`다. Service CIDR 전체나 `0.0.0.0/0`을 허용하지 않는다. 이 클러스터의 관측 결과는 Endpoint 주소만으로 실제 코드의 Service 경로를 허용할 수 없음을 보여준다. CNI 내부 처리 순서 전체를 단정하는 대신 실제 허용/차단 결과를 배포 기준으로 사용한다.

최종 검사 시각은 **2026-10-19 17:16:36 KST**다. DNS는 `172.20.0.1`로 해석됐고 API의 CA·`kubernetes.default.svc` 서버 이름 검증이 성공했다. 같은 ServiceAccount의 Secret 목록 GET은 `403`, Prometheus `/-/ready`는 `200`이었다. 로그·Secret 본문·AI 모델은 읽거나 호출하지 않았다. 기존 앱은 Ready 2개를 유지했다.

- [검사 스크립트](../scripts/probe_cluster_network.py): 운영자가 임시 점검 Pod에서 실행하는 고정 GET 검사다. CLI/AI 도구나 자동 조사 기능에 연결하지 않았으며 이미지에 추가 패키지를 설치하지 않았다.
- [JSON 근거](assets/B3-storage-network-validation.json): UTC 시각, 정책별 결과, 실제 Pod/이미지·노드, StorageClass, 정리 확인을 기록한다. 인증값과 원시 API 본문은 없다.

## 정책 집행의 한계

현재 VPC CNI는 `standard` 모드다. AWS 문서에 따르면 Pod 시작과 정책 설정 사이에 기본 허용 구간이 있을 수 있다. 이번 작업에서 클러스터 전체 CNI 모드를 바꾸지 않았다. [AWS 시작 모드 설명](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy-configure.html), [컨트롤러 Pod와 정책 적용 범위](https://docs.aws.amazon.com/eks/latest/userguide/cni-network-policy.html)를 참고한다.

이번 결과는 정책 반영 후 **임시 Deployment 한 Pod·한 노드**에서 확인한 결과다. 시작 직후부터 차단된다는 보장, 모든 노드·미래 StatefulSet의 동작, 전체 외부 송신/다른 namespace 차단 검증으로 확대하지 않는다. 실제 진단 Pod는 네트워크 작업을 자동 시작하지 않으며 수집 전에 정책 반영과 허용/거부를 다시 확인한다. Endpoint는 클러스터 재생성·제어면 변경 시 바뀔 수 있으므로 재조회하고 재검증해야 한다.

## 재설치와 정리

클러스터·계정·CSI 준비 상태를 먼저 대조한 뒤 저장소 정의를 적용한다.

```bash
aws sts get-caller-identity
kubectl config current-context
kubectl get csidriver ebs.csi.aws.com
kubectl apply --dry-run=server -f ai-ops/kubernetes/ai-storageclass.yaml
kubectl apply -f ai-ops/kubernetes/ai-storageclass.yaml
kubectl get storageclass ai-ops-gp3-encrypted -o yaml
```

임시 점검 Deployment·ServiceAccount·정책·projected token은 namespace와 함께 제거했다. 대상 namespace에 추가한 임시 Role/RoleBinding도 제거했고, namespace·두 권한 리소스가 없음을 재조회했다. AI 전용 StorageClass만 유지했으며 실제 `ai-ops` namespace/PVC는 없다.

전체 인프라 정리 시 향후 AI StatefulSet을 중지하고 보관할 자료를 먼저 확보한다. `ai-ops` PVC/PV와 EBS Volume ID·회수 정책을 기록한 뒤 해당 PVC를 명시 삭제하고 실제 EBS 부재를 확인한다. 이 확인은 EKS/CSI 제거보다 먼저 수행한다. StorageClass만 삭제해도 기존 PVC/PV/EBS가 삭제되는 것은 아니다. 상세 순서는 [설치 가이드](../../docs/runbooks/infrastructure-installation-and-validation.md)와 [정리 가이드](../../docs/runbooks/infrastructure-cleanup.md)를 따른다.

## 다음 작업

1. 실제 `ai-ops` namespace에 진단 StatefulSet을 배포한다. 이미지·환경·StorageClass·API 주소를 배포 직전에 다시 대조한다.
2. 해당 Pod의 실제 읽기 권한, Secret/쓰기/exec/다른 namespace 거부, DNS/API/Prometheus·외부 송신 거부와 토큰 교체를 확인한다.
3. PVC가 만든 EBS의 암호화·KMS 키·AZ를 조회하고 재시작·detach/attach·저장 자료 재조회까지 확인한다.

이번 values는 완성된 배포 입력을 담지만 `enabled=false`다. 활성 렌더링은 10개 리소스의 기존 권한·통신·저장 경계 검사를 통과했다. 렌더링 성공을 실제 배포 성공으로 표시하지 않는다. 다음 작업 추천 추론 깊이는 **GPT-6 SOL high**다.
