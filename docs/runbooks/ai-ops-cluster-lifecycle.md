# AI 클러스터 리소스 설치·정리 자동화 연결

인프라 담당 대화가 `인프라 설치 시작`·`종료` 요청을 수행할 때 이 절차도 **필수 단계로 포함**한다. 별도 예약 작업이나 전체 인프라를 한 번에 삭제하는 스크립트가 아니다. 아래 명령을 자동 실행 담당자가 순서대로 수행하고 각 검증을 통과해야 다음 단계로 넘어간다. 이 문서를 반영하는 작업 자체에서는 AWS 설치·삭제를 실행하지 않는다.

## 범위와 소유권

| 대상 | 설치 | 전체 정리 |
| --- | --- | --- |
| AI ECR·lifecycle·이미지 등록 IAM Role/policy | production OIDC 뒤 별도 Terraform root | production destroy 전에 AI root destroy |
| AI 이미지 | main의 `ai-ops-image.yml`, publish=true, artifact/ECR 검증 | 해당 AI 저장소의 모든 digest를 비운 뒤 ECR 삭제 |
| namespace·암호화 StorageClass | 커밋된 `ai-ops/kubernetes/` 원본 | PVC/EBS 삭제 확인 후 삭제 |
| 수동 조사 worker StatefulSet·PVC·전용 읽기 RBAC·NetworkPolicy·metrics Service/ServiceMonitor | 고정 digest와 새 클러스터 값으로 수동 Helm | Helm uninstall 후 남은 PVC 명시 삭제·EBS 부재 확인 |
| AI state·bucket의 버전/삭제 마커 | 별도 backend key 유지 | 두 root와 AWS 실물 제거 확인 후 마지막 정리 |

`ai-ops` namespace만 지우거나 EKS만 destroy해서 EBS까지 없어졌다고 판단하지 않는다. StatefulSet의 PVC 보존 정책은 Retain, AI PV의 reclaim은 Delete다. 보고서·정제 입력·관측 집계는 PVC 삭제 시 사라진다. 전체 삭제 요청이 없는 평상시 중지는 [0→1 복구 가이드](../../ai-ops/docs/B3-observability-and-recovery.md)를 따른다. 로컬 소스·개인 API 키 파일·로컬 설명 사이트는 AWS 정리 대상이 아니다.

Route53은 전체 정리에서도 조회만 하며 삭제·변경하지 않는다. 기존 RDS 최종 스냅샷 보존 정책도 바꾸지 않는다. B-04 worker의 실제 배포·Pod 복구 검증 후 재설치 대상은 **수동 조사 큐 worker·읽기 수집·규칙 보고서와 자체 관측**이다. B-05의 내부 경보 수신기를 함께 복원한다. 외부 모델·운영 변경은 활성화하지 않는다. 모니터링 Application 설치 전 [TLS·인증 Secret](../../ai-ops/docs/B5-webhook-identity.md)을 먼저 준비해야 한다. 예약 보관 정리는 검증한 새 이미지와 worker overlay로 명시적으로 복원한다. chart 기본값의 비활성·진단 모드는 유지하며 아래 worker overlay를 명시해야 실행된다.

## 설치 순서

### 1. Terraform 등록 기반

production root의 OIDC 생성 뒤 [등록 root 가이드](ai-ops-registry-lifecycle.md)의 설치를 실행한다. `enable_ai_ops_registry=true`, 별도 state key와 managed resource 4개를 확인하고 GitHub `ai-ops-image` 환경 변수 4개를 output과 대조한다. AI root 완료가 설치 전체 완료는 아니다.

### 2. 이미지 확보와 승인 지점

AI ECR을 새로 만들었다면 과거 digest는 존재하지 않는다. main에 병합된 코드로 이미지 등록 workflow를 **자동화 담당자가 직접 시작**한다. 앱 CI를 억지로 실행하거나 로컬 Docker push로 대체하지 않는다.

```bash
git fetch origin main
gh workflow run ai-ops-image.yml --repo f-lab-edu/F-Lab-DevOps --ref main -f publish=true
gh run list --repo f-lab-edu/F-Lab-DevOps --workflow ai-ops-image.yml --limit 5 \
  --json databaseId,headSha,status,conclusion,url
```

실행 출력의 URL과 source SHA에 해당하는 정확한 run ID를 기록한다. 기존 GitHub environment에 `required_reviewers`가 있으므로 waiting이면 **그 실행의 사용자 승인에서 중지**한다. 자동화 담당자는 `Review deployments → ai-ops-image → Approve and deploy` 링크를 제공한다. 이 설정을 제거하거나 대신 승인하지 않는다. 승인 후 같은 run이 success인지 확인하고 재개한다. 이미 적합한 승인 이미지와 등록 artifact가 있으면 재빌드하지 않고 다음 검증으로 재사용할 수 있다.

```bash
AI_IMAGE_RUN_ID=REPLACE_WITH_VERIFIED_RUN_ID
AI_LIFECYCLE_DIR="$HOME/.local/share/url-shortener-ai-ops/lifecycle-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$AI_LIFECYCLE_DIR"
chmod 700 "$AI_LIFECYCLE_DIR"
gh run view "$AI_IMAGE_RUN_ID" --repo f-lab-edu/F-Lab-DevOps --json status,conclusion,headSha
gh run download "$AI_IMAGE_RUN_ID" --repo f-lab-edu/F-Lab-DevOps \
  --pattern "ai-ops-image-${AI_IMAGE_RUN_ID}-*" --dir "$AI_LIFECYCLE_DIR/image"
```

artifact 디렉터리에서 `image-registration.json`의 정확한 경로를 확인해 `AI_IMAGE_RECORD`로 지정한다. 여러 attempt가 있으면 최신 성공 attempt 하나를 선택하며 실패 실행이나 publish=false 기록을 쓰지 않는다. private 작업 폴더는 이번 실행 동안 유지하고 재개 시 새 임의 폴더로 바꾸지 않는다.

### 3. 클러스터 준비·실제 환경값 생성

아래 설치는 기존 설치 가이드의 **앱·Argo·Prometheus·Karpenter 설치/검증 뒤 11-1절**에서 실행한다. EBS CSI ACTIVE, 노드 Ready, VPC CNI NetworkPolicy 활성, Prometheus ServiceMonitor CRD·대상 선택, 앱 2개 Pod Ready와 Argo Healthy를 먼저 확인한다. CNI가 standard 모드면 Pod 시작 시 default-allow 구간이 있다는 기존 한계를 기록하며 무중단 CNI 모드 변경을 이 절차에 섞지 않는다.

```bash
aws sts get-caller-identity --query Account --output text
kubectl config current-context
kubectl get nodes
kubectl -n kube-system get daemonset aws-node -o json
kubectl get csidriver ebs.csi.aws.com
aws eks describe-addon --cluster-name urlshortener --addon-name aws-ebs-csi-driver \
  --region ap-northeast-2 --query addon.status --output text
kubectl -n url-shortener get deployment url-shortener-api
kubectl -n argocd get application url-shortener
kubectl -n monitoring get prometheus kube-prometheus-stack-prometheus -o json
kubectl -n monitoring get pods -l app.kubernetes.io/name=prometheus --show-labels
kubectl -n kube-system get pods -l k8s-app=kube-dns --show-labels
```

현재 chart는 `monitoring` namespace의 `app.kubernetes.io/name=prometheus`·`operator.prometheus.io/name=kube-prometheus-stack-prometheus`, kube-system의 `k8s-app=kube-dns`를 사용한다. 실제 labels/port와 ServiceMonitor namespace/label selector가 이 선언과 다르면 중지하고 배포 정의부터 검토한다. ServiceMonitor의 namespace 선택이 ai-ops를 포함하고 `release=kube-prometheus-stack`을 허용해야 한다.

잠긴 의존성의 Python 환경과 `PYTHONPATH=ai-ops/src`로 아래 읽기 전용 준비 도구를 실행한다. `AI_IMAGE_RECORD`는 앞에서 확인한 artifact 절대 경로다.

```bash
AI_IMAGE_RECORD=REPLACE_WITH_PRIVATE_ARTIFACT_PATH
PYTHONPATH=ai-ops/src python ai-ops/scripts/prepare_reinstall.py \
  --registration "$AI_IMAGE_RECORD" --output "$AI_LIFECYCLE_DIR/values.json"
AI_WORKER_SOURCE_SHA=$(python -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_revision"])' "$AI_IMAGE_RECORD")
git cat-file -e "${AI_WORKER_SOURCE_SHA}:ai-ops/src/ai_ops/worker_runtime.py"
git cat-file -e "${AI_WORKER_SOURCE_SHA}:ai-ops/src/ai_ops/retention_schedule.py"
git cat-file -e "${AI_WORKER_SOURCE_SHA}:ai-ops/src/ai_ops/alert_receiver.py"
```

도구는 계정·컨텍스트·origin/main의 등록 소스·artifact 소스 해시·ECR manifest/image ID·COMPLETE/검사 0건을 검증하고 현재 API Service/Endpoint 내부 IPv4 /32를 조회한다. 과거 IP·digest를 재사용하지 않는다. 원본 base는 계속 enabled=false이며 생성한 private values와 아래 worker overlay를 함께 쓰는 경우만 활성화한다. 등록 소스에 `ai-ops/src/ai_ops/worker_runtime.py` 또는 `retention_schedule.py`가 없으면 worker 설치를 중지하고 검증된 새 이미지를 확보한다. 조회 실패/403을 리소스 부재로 해석하지 않는다.

### 4. 수동 큐 worker·자체 관측 설치와 완료 판정

원본 namespace/StorageClass가 main에 있는지 확인한다. 같은 이름의 기존 StorageClass가 있다면 CSI/gp3/encrypted=true/WFFC/reclaim Delete·기본값 아님·소유 labels가 원본과 일치해야 하며 다른 소유자의 리소스를 덮어쓰지 않는다. 사용 중인 다른 StorageClass나 앱 PVC는 변경하지 않는다.

```bash
kubectl apply -f ai-ops/kubernetes/namespace.yaml
kubectl apply -f ai-ops/kubernetes/ai-storageclass.yaml
helm template ai-ops ai-ops/chart --namespace ai-ops \
  -f "$AI_LIFECYCLE_DIR/values.json" -f ai-ops/config/b4-values.worker.yaml \
  -f ai-ops/config/b5-values.receiver.yaml \
  > "$AI_LIFECYCLE_DIR/rendered.yaml"
kubectl apply --dry-run=server -f "$AI_LIFECYCLE_DIR/rendered.yaml"
helm upgrade --install ai-ops ai-ops/chart --namespace ai-ops \
  -f "$AI_LIFECYCLE_DIR/values.json" -f ai-ops/config/b4-values.worker.yaml \
  -f ai-ops/config/b5-values.receiver.yaml \
  --wait --timeout 180s
kubectl -n ai-ops rollout status statefulset/ai-ops --timeout=180s
kubectl -n ai-ops get pod,pvc,service,servicemonitor,networkpolicy
kubectl -n ai-ops exec ai-ops-0 -c diagnostics -- python -m ai_ops.worker_runtime --probe
kubectl -n ai-ops logs ai-ops-0 -c diagnostics --tail=5
kubectl -n ai-ops logs ai-ops-0 -c alert-receiver --tail=5
```

완료 조건은 Helm deployed·Pod Ready·PVC Bound·실제 EBS encrypted=true·지정 이미지 digest 일치, Prometheus `up{namespace="ai-ops",service="ai-ops-metrics"}=1`과 `ai_ops_worker_last_progress_timestamp_seconds` 갱신, 8개 고정 큐 상태 지표다. worker mode에서 예전 진단 heartbeat를 완료 기준으로 사용하지 않는다. Ready만 보고 완료로 표시하지 않는다. 기존 비활성 Argo AI Application은 동시에 적용하지 않는다. 새 PVC에는 삭제된 과거 보고서·큐·집계가 자동 복원되지 않는다. 별도 실제 수집 시험은 보관 기한을 기록하고 외부 AI 차단을 유지한 상태에서 수행한다.

## 전체 정리: 앱·모니터링·Argo·CSI를 지우기 전

인프라 정리 담당자는 배포 동결과 전체 삭제 범위를 확인한 뒤 이 절을 **기존 정리 가이드 0-2절에서 먼저 실행**한다. 현재 릴리스의 실제 Volume ID를 조회하며 예전 문서의 ID를 상수로 삭제하지 않는다. 2026-10-19 기록의 `vol-037d3068cc25c23ef`도 조회 결과를 대체하지 않는다.

### 1. 소유권·볼륨 기록

```bash
aws sts get-caller-identity --query Account --output text
kubectl config current-context
helm list -n ai-ops -o json
kubectl -n ai-ops get statefulset,pod,pvc,service,servicemonitor,networkpolicy
kubectl get storageclass ai-ops-gp3-encrypted -o yaml
kubectl get pv -o json | jq '[.items[] | select(.spec.claimRef.namespace == "ai-ops") |
  {pv:.metadata.name,pvc:.spec.claimRef.name,storageClass:.spec.storageClassName,
   reclaim:.spec.persistentVolumeReclaimPolicy,volume:.spec.csi.volumeHandle}]'
aws ec2 describe-volumes --region ap-northeast-2 \
  --filters Name=tag:Project,Values=urlshortener Name=tag:Component,Values=ai-ops \
  --query 'Volumes[].{Id:VolumeId,State:State,Size:Size,Attachments:Attachments}' --output json
```

PV/Volume ID 목록을 private 작업 폴더에 기록해 PVC 삭제 후에도 대조할 수 있게 한다. AI namespace가 없다는 이유만으로 태그가 붙은 잔여 EBS가 없다고 가정하지 않는다.

예상 소유권은 ai-ops Helm release·`data-ai-ops-0` PVC·`ai-ops-gp3-encrypted`다. 다른 PVC·StorageClass·연결된 워크로드가 보이면 소유권을 확인할 때까지 중지한다. 조회 오류이면 임의로 다음 단계로 가지 않는다. 다른 namespace의 PVC는 이 절에서 삭제하지 않는다.

### 2. Helm·PVC·EBS를 먼저 제거

```bash
helm uninstall ai-ops -n ai-ops --wait --timeout 180s
kubectl -n ai-ops delete pvc data-ai-ops-0 --ignore-not-found --wait=true --timeout=180s
kubectl -n ai-ops get pvc
kubectl get pv -o json | jq '[.items[] | select(.spec.claimRef.namespace == "ai-ops")]'
```

이미 Helm release가 없다면 성공한 `helm list` 결과로만 확인하고 uninstall을 건너뛴다. release 부재와 권한/네트워크 오류를 구분한다. PVC 보존은 전체 삭제 요청에서는 적용하지 않는다. **기록한 각 Volume ID**를 `aws ec2 describe-volumes --region ap-northeast-2 --volume-ids <확인한_ID>`로 조회하고 `InvalidVolume.NotFound`가 나올 때까지 CSI/EKS 삭제로 넘어가지 않는다. PVC/PV가 남거나 CSI 삭제가 실패하면 그 원인을 해결한다. PV finalizer를 임의로 없애지 않는다.

클러스터가 이미 삭제돼 CSI를 쓸 수 없는 재개 상황이면 두 태그(Project=urlshortener, Component=ai-ops)·기록한 PV/Volume ID·attachment와 소유권을 대조한다. 전체 삭제 대상으로 확정된 **available/unattached** AI 볼륨만 정확한 ID로 `aws ec2 delete-volume --region ap-northeast-2 --volume-id <확인한_ID>`를 수행한 뒤 부재를 검증한다. 연결 중인 볼륨의 강제 detach·기본 VPC 전체 볼륨 삭제는 하지 않는다.

### 3. 전용 클러스터 리소스·잔여 권한 확인

```bash
kubectl -n url-shortener get role,rolebinding -o name
kubectl -n argocd get role,rolebinding -o name
kubectl -n ai-ops get statefulset,pod,service,servicemonitor,networkpolicy,configmap,serviceaccount,pvc
kubectl delete storageclass ai-ops-gp3-encrypted --ignore-not-found
kubectl delete namespace ai-ops --ignore-not-found --wait=true --timeout=180s
```

Helm 소유의 `ai-ops-read-target`·`ai-ops-read-argo` Role/RoleBinding이 각 namespace에서 없어졌는지 확인한다. release가 손상돼 남았다면 정확한 이름·Helm 소유 annotations를 대조한 뒤 그 리소스만 정리한다. default ServiceAccount·루트 CA ConfigMap은 namespace 삭제로 정리되며 다른 namespace의 공유 리소스는 지우지 않는다. ServiceMonitor가 없어도 Prometheus에 과거 시계열은 retention 기간 남을 수 있다. AI EBS 태그 조회의 결과가 `[]`인지 최종 확인한다.

### 4. 등록 기반과 backend는 마지막

이제 기존 인프라 정리의 Karpenter·앱·모니터링·로드밸런서·EKS/CSI 순서로 이어간다. production Terraform destroy **전**에 [AI 등록 root 정리](ai-ops-registry-lifecycle.md#정리-기존-production-terraform-destroy-전)를 수행해 AI ECR의 태그 없는 이미지를 포함한 모든 digest를 비우고 ECR/IAM 네 리소스를 별도 state로 destroy한다. ECR `RepositoryNotFoundException`·IAM `NoSuchEntity`와 AI state managed resource 0개를 확인한다.

두 root와 AWS 실물 제거를 확인한 뒤에만 state bucket의 AI/production key·잠금 파일·이전 버전·삭제 마커를 정리한다. state를 먼저 지우지 않는다. Route53과 기존 RDS 최종 스냅샷은 기존 보존 정책대로 남긴다. GitHub environment의 변수·필수 승인과 로컬 원본 소스는 AWS 삭제와 별개이며 재설치 때 다시 검증한다.

## 자동화 담당자의 결과 기록

설치는 registry 4개·이미지 run/source/digest/scan·Helm revision·Pod/PVC·암호화·Prometheus 결과를, 정리는 AI Helm·PVC/PV·EBS·전용 권한·StorageClass/namespace·ECR/IAM·state/bucket 확인을 기존 실행 기록에 포함한다. 어떤 단계가 승인/오류/미반영 코드로 막혔으면 해당 범위를 미완료로 표시하고 전체 완료라고 기록하지 않는다.

## B-04 worker 운영·보관 정리

2026-10-20 승인 이미지로 실제 worker 배포·동일 PVC의 강제 Pod 교체 후 checkpoint 재사용과 중복/보고서 보존을 확인했다. [실행 기록](../../ai-ops/docs/B4-worker-deployment.md)을 참고한다. 재설치 완료를 확인하려고 매번 강제 Pod 교체 시험을 반복하지 않는다. 기본은 probe·Prometheus·이미지·PVC 확인이며 새 코드나 실패가 있을 때만 필요한 복구 시험을 추가한다.

보관표 조회·삭제는 단일 worker 잠금과 겹칠 수 없다. 같은 이미지·private values·worker overlay를 유지하고 Helm 마지막 값에 `--set mode=diagnostics --set retention.enabled=false --set receiver.enabled=false`를 함께 지정하여 전환해 worker 종료를 확인한 뒤 수행한다. 정리 후 override를 제거해 worker로 복귀한다. 예약 정리는 현재 worker의 안전 지점에서 활성화돼 있으며 전체 인프라 삭제에서는 기존 순서대로 AI를 먼저 중지·삭제하고 PVC/EBS와 ECR/state를 확인한다.

예약 정리는 새 이미지의 worker overlay에서 명시적으로 활성화한다. 현재 worker가 안전 지점에서 잠깐 정리한 뒤 조사 처리를 재개하므로, 이 자동 경로에는 Helm 모드 전환이나 Kubernetes 변경 권한을 부여하지 않는다. 주기·장부·실제 배포 상태는 [예약 정리 문서](../../ai-ops/docs/B4-retention-schedule.md)를 따른다. 과거의 예약 비활성 문구는 최초 배포 당시 기록이다.

2026-10-20 예약 정리 이미지도 사용자 승인·ECR 검사 0건 후 실제 배포했다. 재설치 완료 기준에는 worker probe·Prometheus up=1 외에 `ai_ops_retention_enabled=1`·예약 성공 시각·overdue 확인을 포함한다. 현재 총 38개 지표이며, 과거 진단 heartbeat로 대신하지 않는다. 실제 운영 자료의 날짜를 조작하거나 강제 Pod 종료 시험을 설치 때마다 반복하지 않는다. [검증·한계](../../ai-ops/docs/B4-retention-schedule.md).

## B-05 수신기 재설치·정리 추가 조건

수신기는 내부 ClusterIP TLS 9443이며 2개 컨테이너 모두 Ready여야 한다. 위 TLS Secret 사전 준비와 두 overlay가 필수다. Alertmanager의 인증 Secret 마운트·CA 호스트명·Bearer 인증과 실제 경보 접수를 확인한다. TCP Ready만으로 전달 완료라 판단하지 않는다. 새 설치에는 예전 중복/해제 장부와 보고서가 복원되지 않는다.

정리 시 monitoring의 `ai-ops-alertmanager-auth`는 AI Helm 종료 직후가 아니라 기존 모니터링 종료·수동 Secret 삭제 단계에서 제거한다. AI namespace의 수신기 Secret은 전용 namespace 삭제로 제거된다. 추가 ECR·IAM·state·외부 LoadBalancer는 만들지 않는다. 외부 Slack 시험 전송이 필요하면 별도 승인한 범위에서 수행한다.

## S10 실습을 Argo에 인계한 경우

2026-10-22 이후 선택 실습에는 `argocd/lab-target`·Project `b6-lab`과 namespace 밖 실습 전용 읽기 Role/RoleBinding·AdmissionPolicy/Binding이 추가됐다. 실습만 정리할 때는 namespace 삭제만으로 끝내지 않는다. 전체 삭제 권한과 목적 label·실제 관리 Deployment 하나·finalizer/토큰 부재를 확인하고 Application → 실습 namespace → 실습 전용 namespace 밖 구성 순으로 정리한다. 정확한 이름·제한과 실제 연결 상태는 [S10 인계 기록](../../ai-ops/docs/B6-gitops-adoption.md)을 따른다. 기본 운영 설치는 이 선택 실습을 자동 생성하지 않으며 재설치는 검토 SHA·현재 환경·권한 검증 후 별도로 인계한다.

## S10 상시 전달기·실습 worker 추가 구성

`ai-ci` 별도 namespace·전용 1Gi 암호화 PVC·GitHub Secret·두 Deployment를 추가하는 경우 [상시 CI 서비스 절차](../../ai-ops/docs/B6-ci-service.md)를 따른다. 기본 replicas=0 정의 생성은 상시 설치 완료가 아니다. 현재 main 이미지에 기능이 포함되고 승인 이미지 검사·worker probe·relay 조회·실제 CI 자동 접수까지 검증한 범위만 완료로 기록한다.

전체 삭제에서 `ai-ops-lab` 및 EBS CSI보다 **먼저** `ai-ci`의 relay/worker를 0개로 중지하고 실제 PV/Volume ID를 기록한다. `ci-queue` PVC를 삭제하고 EBS 실물 부재를 확인한 뒤 namespace를 제거한다. `ai-ops-lab`·`argocd`의 `ci-worker-read` RoleBinding도 제거한다. 새 namespace가 없더라도 기록한 PVC/EBS와 태그 소유권을 대조하며 이전 볼륨 ID를 재사용하지 않는다. 로컬 전용 토큰 파일·소스·GitHub 설정은 AWS 전체 삭제와 별도로 보존한다. 재설치에서는 운영 설치·실습 복원·GitOps 인계와 새 자격 검증 후 worker→relay 순으로 활성화한다.

2026-10-23에는 ai-ci worker·relay를 실제 활성화하고 최신 실습 CI 자동 접수와 보고서 저장을 확인했다. 전체 정리에서 이 namespace의 **전용 1Gi PVC/EBS**를 누락하지 않는다. 실제 Volume ID는 항상 새 조회로 기록하며 [실측](../../ai-ops/docs/B6-ci-service-deployment.md)을 참고한다. 재설치 시 기존 Secret 값·Pod UID·API 주소를 그대로 재사용하지 않는다.

## B-06-M6 모델 서비스 추가 구성

실습 모델용 `ai-model` namespace·gateway/proxy·전용 1Gi 암호화 PVC·NetworkPolicy의 비활성 설치와 검증은 [M6 절차](../../ai-ops/docs/B6-model-service-deployment.md)를 따른다. 기본 두 Deployment는 replicas=0이고 기존 `ai-ci` worker egress·운영 AI 경로는 변경하지 않는다. manifest의 Secret 이름 참조는 키 생성이나 상시 모델 승인으로 간주하지 않는다. 새 코드가 포함된 승인 이미지와 서버 dry-run, 실제 네트워크 허용/거부 검증 전에는 활성화하지 않는다.

전체 삭제에서는 `ai-model`을 `ai-ci`·`ai-ops-lab`·EBS CSI보다 먼저 중지한다. `model-ledger` PVC/PV의 현재 Volume ID를 기록하고 EBS 실물 부재를 확인한 뒤 Secret·Service·NetworkPolicy·ServiceAccount·namespace 잔여를 확인한다. 다른 AI PVC가 남아 있는 동안 공용 암호화 StorageClass를 먼저 제거하지 않는다. 재설치에서는 이전 Secret·승인·digest·PVC/PV 식별자를 그대로 재사용하지 않는다.
