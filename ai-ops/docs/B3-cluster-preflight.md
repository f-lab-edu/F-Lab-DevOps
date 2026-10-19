# B-03 실환경 배포 사전 점검

최신 자체 관측 상태: 2026-10-19 19:28:15 KST 자체 관측·중지/복구 실환경 검증 완료. PR #36·main e1b30b5의 새 이미지 등록 성공, ECR 검사 전체 0건과 코드 해시 일치를 확인했다. Helm revision 4에서 Prometheus up=1·진단 갱신·수동 수집/보고서 partial 각 1회 증가를 확인했다. AI StatefulSet 0→1 복구 후 동일 PVC·기존/신규 보고서 재조회와 해시·집계 보존, 앱 UID/generation 불변·Ready 2개를 확인했다. 외부 모델은 호출하지 않았다. [관측·중지/복구 절차](B3-observability-and-recovery.md). 아래 이전 이미지·배포 내용은 해당 시점의 이력이다.

최신 상태: 2026-10-19 18:12 KST 실제 수집→규칙 부분 보고서→PVC 저장→JSON/KST 재조회 연결 검증 완료. PR #34·#35 수정이 포함된 main 5d432f5 이미지로 일반 경로를 실행했고 Pod 2개 상태·설정, 배포/Argo·캐시 지표 등 7개 항목을 수집했다. 총 18개 중 available 7·invalid 2·missing 7·stale 2로, 자료 부족은 partial/unknown/insufficient_evidence로 유지했다. 이전 Pod의 저장 보고서도 새 Pod에서 해시 변화 없이 재조회했다. 외부 AI·키·예산 경로는 호출하지 않았고 기존 앱 UID/generation·Ready 2개는 유지됐다. [실제 수집 검증](B2-live-validation.md). 아래 이전 이미지·배포 결과는 해당 시점의 이력이다.

2026-10-19 실제 진단 배포: 수동 Helm으로 ai-ops namespace의 StatefulSet 한 Pod와 5Gi 암호화 gp3 EBS를 배포했다. 실제 읽기·Secret/다른 namespace 거부·쓰기/exec 권한 부재·선택한 수신/송신 차단·같은 AZ의 다른 노드 EBS 재연결과 합성 SQLite 보존을 확인했다. 같은 Pod의 토큰 자동 교체·새 토큰 API 200, 임시 nodeSelector 제거·최종 Ready·같은 PVC 자료 보존까지 확인했다. [실제 배포 기록](B3-diagnostics-deployment.md).

2026-10-19 저장소·API 경로 준비 완료: AI 전용 `ai-ops-gp3-encrypted` StorageClass를 생성하고 임시 Deployment에서 기본 API 차단·Service/Endpoint TLS GET·Secret 403·Prometheus 200을 확인했다. 확인한 세 `/32`와 StorageClass를 비활성 values에 반영했다. 임시 namespace·권한은 제거했으며 실제 진단 StatefulSet/PVC·EBS 암호화·재연결 검증은 후속이다. [준비·검증 기록](B3-storage-and-network.md)을 따른다.

2026-10-19 KST 현재 EKS를 읽기 전용으로 확인했다. 아래 값은 이번 재설치의 관측값이므로 다음 배포 직전에 UID·주소·정책을 다시 대조해야 한다. 대표 메트릭 조회 시각은 **2026-10-19 16:14:11 KST**다.

## 결과와 준비한 파일

대상 앱·Argo Application·Prometheus·DNS와 CNI/CSI 구성은 확인했다. 환경은 production 관리 대상이다. 사용자가 현재 클러스터의 읽기 전용 조사를 선택해 production 등록·수집·규칙 보고서를 허용하는 코드를 main과 새 이미지에 반영했다. 보안 수정 digest·전용 암호화 StorageClass·임시 Pod의 API 통신 경로를 확정했다. **사전 점검과 비활성 설정 작성 완료**, 실제 진단 StatefulSet/PVC 배포는 미실행이다.

[비활성 설정](../config/b3-values.preflight.yaml)은 `enabled=false`이며 [production 대상 등록](../config/targets.production.json)의 binding과 실제 연결 경로를 담는다. 이미지 digest는 ECR 검사 0건인 수정본으로 고정했고 StorageClass는 `ai-ops-gp3-encrypted`, API CIDR은 확인한 Service/Endpoint 세 `/32`로 채웠다. 현재 운영 대상을 practice로 재표기하거나 스캔 지적이 남은 digest를 배포 기본값으로 지정하지 않는다. projected CA/token의 마운트 경로만 있으며 실제 인증값은 없다.

## 조사 대상과 환경

| 항목 | 관측값 |
|---|---|
| 계정·리전 | `716174522908` / `ap-northeast-2` |
| EKS | `urlshortener`, ACTIVE, Kubernetes `1.35` |
| EKS ARN | `arn:aws:eks:ap-northeast-2:716174522908:cluster/urlshortener` |
| 생성 시각 | 2026-10-19 11:58:56 KST |
| root의 현재 S3 backend key | `urlshortener/production/terraform.tfstate` |
| 대상 namespace | `url-shortener`, UID `9f4a8b1a-6d78-490c-b9b8-966d7bff1197` |
| Deployment | `url-shortener-api`, UID `2e924b98-e8a9-4f98-b704-02762e1c4682`, ready 2 |
| Pod selector·컨테이너 | `app=url-shortener-api`, `api` |
| Service | `url-shortener-svc`, UID `b279fd91-f0a8-4d1d-948e-eddfad2e2d99`, TCP 80 → 8000 |
| Argo Application | `argocd/url-shortener`, UID `37da50b7-ccb7-4ead-a242-90868c72964e` |
| Argo source | `https://github.com/f-lab-edu/F-Lab-DevOps.git`, `url-shortener/url-shortener-chart`, main |
| Argo values | `values.yaml` 및 `values.prod.yaml` |
| Argo 상태 | Synced / Healthy, revision `c2169f7b5398c07185906814d3b1d14b33ac7fab` |
| AI namespace | 현재 목록에 없음. 생성하지 않음 |

환경 판단 근거는 현재 root backend key, `environment` 변수 기본값 production과 Argo의 운영 values다. EKS 태그에는 `Name=urlshortener-eks`만 있으며 practice 표시는 없다. AI ECR/state 이름에 포함된 practice는 대상 앱을 실습 환경으로 바꾸지 않는다.

초기 점검에서 practice 전용 조건을 발견했고, 사용자는 **현재 production 클러스터에 읽기 전용 조사를 연결**하도록 선택했다. 이에 `TargetBinding`과 snapshot 검증의 practice 전용 제한을 제거하고 기존 Target의 practice/production 계약을 사용한다. chart도 두 환경의 명시적 등록을 허용한다. 대상 cluster·namespace·workload·service·environment는 여전히 등록 목록과 정확히 대조한다.

`targets.production.json`에는 `urlshortener / url-shortener / url-shortener-api / url-shortener-svc / production` 하나를 `url-shortener-production` 별칭으로 등록했다. 원래 실습 예제도 유지한다. production을 practice로 바꾸지 않으며 보고서·저장·재조회에서도 실제 환경을 보존한다. Secret·exec·쓰기 권한은 추가하지 않았다.

현재 허용한 범위는 **읽기 수집 → 마스킹·감사 기록 → 규칙 기반 분석 → 개인/PVC 저장 및 재조회**다. `ensure_external_model_allowed`의 synthetic practice 전용 정책은 유지해 production 자료의 외부 모델 전송·토큰 계산·비용 예약을 차단한다. 운영 자료를 이용한 실제 AI 분석을 활성화하려면 자료 전송 정책과 모델 연결의 후속 구현이 별도로 필요하다.

## Prometheus·DNS·지표

| 항목 | 확인값 |
|---|---|
| Prometheus URL 후보 | `http://kube-prometheus-stack-prometheus.monitoring.svc:9090` |
| Prometheus Service selector | `app.kubernetes.io/name=prometheus`, `operator.prometheus.io/name=kube-prometheus-stack-prometheus` |
| Prometheus Pod | `prometheus-kube-prometheus-stack-prometheus-0`, Running / Ready |
| Service EndpointSlice | TCP 9090, 준비된 endpoint 존재 |
| DNS | `kube-system`, `k8s-app=kube-dns`, UDP/TCP 53 |
| ServiceMonitor | `url-shortener/url-shortener-api`, selector `app=url-shortener-api`, `/metrics`, port `http` |
| scrape 간격 | Prometheus 전역·해당 ServiceMonitor 모두 30초 |
| 실제 scrape | 대상 앱 Pod 2개 모두 `up=1`, 최근 5분 sample 각각 10개 |
| 지표 이름 | `cache_operation_total`, `http_request_total`, `http_request_duration_seconds_count`, `db_query_latency_seconds_count` 확인 |
| 현재 관측된 업무 dimension | cache `miss`, route `/items`, DB operation `select_all` |

지표 질의는 운영자 자격증명의 **로컬 127.0.0.1 전용 port-forward**로 수행했고 조회 후 종료했다. API Service proxy 경로는 15초 timeout으로 실패했다. 이 결과는 Prometheus의 실제 자료와 label을 확인한 것이며 미래 AI Pod의 DNS·HTTP 접근 성공을 증명하지 않는다.

고정 catalog의 namespace/service label은 관측 자료와 일치한다. `/healthz`·`/readyz`도 지표에 있지만 분석 대상에서 제외한다. `/items/{item_id}`·`/items/_db`와 나머지 DB operation은 앱 코드에 선언돼 있으나 이번 시점에 모두 관측된 것은 아니다. 없는 series나 cache hit/error를 0·정상으로 채우지 않으며 자료 부족으로 처리해야 한다. 업무 요청을 새로 발생시키거나 앱 데이터·로그를 수집하지 않았다.

## API 경로와 CNI

- Kubernetes Service는 `172.20.0.1:443`; EndpointSlice의 API 주소 후보는 `10.0.10.162:443`, `10.0.11.228:443`이다. egress 후보는 각각 `/32`이며 Service CIDR 전체를 허용하지 않는다.
- EKS는 public/private endpoint가 모두 켜져 있다. chart의 연결은 `https://kubernetes.default.svc:443`과 projected CA/token 경로를 사용한다. 실제 CA/token 발급·교체·Pod 접속은 아직 확인하지 않았다.
- VPC CNI addon은 ACTIVE, `v1.23.2-eksbuild.3`, `enableNetworkPolicy=true`다. `aws-node`와 정책 에이전트는 3/3 Ready, 에이전트 인수 `--enable-network-policy=true`, enforcing mode `standard`를 관측했다.
- 초기 점검에서 EndpointSlice 주소는 후보였다. 후속 임시 Deployment에서 기본 차단·Endpoint 직접 TLS GET·Service 경로 차단을 대조했고 Service `172.20.0.1/32`를 추가해야 실제 서비스 이름 경로도 성공함을 확인했다. 이 결과로 `network.apiServerCIDRs`에 세 `/32`를 반영했다. 실제 진단 Pod의 전체 다른 목적지 거부 시험은 후속이다.
- 기존 앱·Argo NetworkPolicy는 조회만 했다. AI namespace용 정책은 없으며 실제 허용/거부 집행을 확인한 것으로 표시하지 않는다.

## 저장소와 노드

| 항목 | 확인 결과 |
|---|---|
| StorageClass | `gp2` 하나, provisioner `kubernetes.io/aws-ebs`, ext4, WaitForFirstConsumer, reclaim Delete |
| StorageClass 암호화 | `encrypted` 파라미터 없음 |
| 계정 EBS 기본 암호화 | false |
| CSI Driver | `ebs.csi.aws.com` 존재, addon ACTIVE `v1.67.0-eksbuild.1` |
| CSI 준비 | controller 2/2 Ready, Linux node DaemonSet 3/3 Ready |
| CSI 역할 연결 | controller SA의 role annotation과 addon의 EBS 전용 role 일치 |
| 노드 | Ready 3대, 모두 amd64/t3.large. ap-northeast-2a 2대·2c 1대 |

현재 gp2 선언과 계정 기본값만으로 AI 데이터 볼륨의 암호화를 확보했다고 말할 수 없다. 후속 작업에서 AI용 CSI StorageClass `ai-ops-gp3-encrypted`를 생성하고 비활성 설정에 이름을 반영했다. 관리 원본은 `ai-ops/kubernetes/ai-storageclass.yaml`이며 Terraform state 소유 자원은 아니다. PVC/EBS는 생성하지 않았다. 실제 암호화·AZ 배치·volume attach·재시작 후 자료 보존은 배포 검증 대상이다. chart의 PVC Retain 설정과 StorageClass의 reclaim Delete는 서로 다른 설정이므로 함께 확인한다.

## 남은 순서

1. **방향·binding 반영 완료:** 현재 production 클러스터의 읽기 전용 조사로 결정하고 계약·대상 등록·비활성 초안을 수정했다. PR #33으로 main에 반영했고 새 이미지에도 포함됐다.
2. 필수 후속 작업 **B-03-SEC-01**에서 HIGH 8건 수정·재등록·재스캔을 완료해 새 digest를 확정했다. [등록·스캔 기록](B3-image-registration.md)을 따른다.
3. **저장소·API 경로 준비 완료:** 전용 암호화 StorageClass 생성·임시 Pod API 차단/허용 대조·비활성 설정 반영과 활성 렌더링 10개 리소스의 경계 검사를 통과했다.
4. diagnostics Pod를 배포하고 읽기 허용·Secret/exec/쓰기/다른 namespace 거부·외부 통신 차단·token 교체·PVC 재연결을 확인한다.

사전 점검 과정에서 Kubernetes 리소스·Terraform·앱 GitOps 설정을 변경하지 않았고 모델 호출도 하지 않았다. 로컬 검증은 고정 Pydantic 2.13.5 환경에서 Python **154건 통과**, Helm 실습/운영 읽기 권한 검사·잘못된 설정 11종 거부·권한/통신 확대 3종 검출, 실제 대상 초안 lint 및 리소스 0개 렌더링을 확인했다. production 수집·규칙 보고서 저장/재조회는 모의 transport로 검사했고 실제 운영 로그나 모델을 호출하지 않았다. 새 코드의 원격 CI·이미지 재등록·ECR 0건 확인은 완료했다. 실제 Pod 배포·통신·권한·PVC 검증은 미실행이다.


## 보안 수정 이미지 확정

2026-10-19 **B-03-SEC-01 완료**: 사용자 환경 승인 후 [등록 실행 39219009295](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39219009295)가 성공했다. 소스는 main `b6548b82e588b09efee925a2c663ee71d3b91f1d`, 새 digest는 `sha256:bbfa1859831b5811d4bfe3057adf25141ed1c66b38c9165d235952059ef286be`다. **16:57:16 KST ECR 스캔 COMPLETE·전체 0건**으로 기존 HIGH 8건 해소를 확인했다. 등록 artifact·manifest/config digest·소스 파일 3개 해시를 대조했다. production 읽기 전용 지원도 새 이미지에 포함됐으며, [비활성 values](../config/b3-values.preflight.yaml)에 이 digest를 고정했다. AI Pod 배포는 아직 미실행이다. 다음은 암호화 StorageClass와 실제 API 경로·권한·통신·PVC 검증이다.
