# 전체 인프라 리소스 정리 가이드

> 대상: `Url-Shortener-EKS-Platform`
>
> 아래 명령은 리소스와 데이터를 실제로 삭제한다. 운영 RDS Primary는 현재 `deletion_protection=true`, `skip_final_snapshot=false`이고, 운영 ECR은 `repository_force_delete=false`다. 따라서 보호 설정과 이미지 보존 대상을 확인하지 않은 `terraform destroy`는 완료되지 않는다. 정상 삭제 시 운영 RDS 최종 스냅샷을 생성하도록 구성했으며, 생성 결과를 확인해 별도로 보관한다.
>
> 본 가이드는 `docs/runbooks/infrastructure-installation-and-validation.md`의 현재 구성을 기준으로 한다. 원본인 `실습 가이드 문서 모음/인프라 운영 가이드/`는 Git에서 제외되므로 PR에는 동일한 내용의 `docs/runbooks/infrastructure-cleanup.md`를 사용한다. 리소스 정리 실행 전에 현재 AWS 계정·Terraform state·클러스터 상태를 다시 확인한다. **Route53 Hosted Zone과 모든 레코드는 삭제·변경하지 않는다.**
>
> 명령은 절별로 실행한다. 조회·계획 결과를 검토해야 하는 지점에서 멈추고, 예상과 다르거나 오류가 나면 다음 삭제 명령을 실행하지 않는다.

---

## 0. 대상 확인

```bash
PROJECT_ROOT="/Users/jounghyeon/dev/Url-Shortener-EKS-Platform"
AWS_REGION="ap-northeast-2"
CLUSTER_NAME="urlshortener"

kubectl config current-context
aws sts get-caller-identity
terraform -chdir="$PROJECT_ROOT/terraform" version
terraform -chdir="$PROJECT_ROOT/terraform" init \
  -backend-config=backend.production.hcl -reconfigure -lockfile=readonly
terraform -chdir="$PROJECT_ROOT/terraform" state list

VPC_ID=$(terraform -chdir="$PROJECT_ROOT/terraform" output -raw vpc_id)
printf 'VPC ID: %s\n' "$VPC_ID"
aws ec2 describe-nat-gateways --region "$AWS_REGION" \
  --filter "Name=vpc-id,Values=$VPC_ID" \
  --query 'NatGateways[].NatGatewayAddresses[].AllocationId' --output text
```

현재 컨텍스트가 `urlshortener`, AWS 계정이 `716174522908`인지 확인한 뒤 진행한다. Terraform은 `1.14.7`과 운영용 S3 backend key(`urlshortener/production/terraform.tfstate`)를 사용해야 한다. `vpc_id` output이 없거나 state의 리소스가 예상과 다르면 삭제를 진행하기 전에 실제 AWS 리소스와 state를 대조한다. 실습용 state 또는 다른 `project_name`으로 운영 리소스를 정리하지 않는다. NAT Gateway에 연결된 Elastic IP 할당 ID도 삭제 전 기록해 최종 검증에 사용한다. 진행 중인 main 배포 Actions가 끝났는지 확인하고 정리 중에는 새 배포를 시작하지 않는다. 현재 배포 이미지, 이전 안정 이미지, RDS 백업·최종 스냅샷 보존 결정을 먼저 기록한다. 상세 정책은 `docs/runbooks/30-rds-ecr-retention.md`를 따른다.

### 0-1. CI 배포 중지 확인

설치 가이드는 앱 코드가 `main`에 push되면 자동 배포를 시작한다. 아래에서 실행 중·대기 중인 배포가 없음을 확인하고, 팀과 정리 기간의 `main` push 중단을 합의한다. 실행 중인 배포가 있으면 완료를 기다리거나 별도 승인된 취소를 진행하고, ECR·EKS 삭제를 시작하지 않는다.

```bash
gh run list --repo f-lab-edu/F-Lab-DevOps --workflow ci-cd.yml --limit 10 \
  --json databaseId,status,conclusion,headBranch
```

이 저장소의 배포 워크플로를 정리 기간 동안 중지하기로 승인했다면 다음 명령을 **별도로** 실행한다. 다른 팀의 배포에도 쓰이는 저장소라면 비활성화하지 말고 합의된 배포 동결 방법을 따른다. 재설치 후 CI를 다시 사용하려면 워크플로를 재활성화해야 한다.

```bash
gh workflow disable ci-cd.yml --repo f-lab-edu/F-Lab-DevOps
gh workflow view ci-cd.yml --repo f-lab-edu/F-Lab-DevOps
```

---

## 1. 테스트 리소스 삭제

```bash
kubectl delete namespace karpenter-test --ignore-not-found
```

---

## 2. Karpenter 노드 정리

NodePool을 먼저 삭제해야 Karpenter가 생성한 EC2가 정상 종료된다. Controller는 아직 삭제하지 않는다.

```bash
kubectl delete nodepool default --ignore-not-found --wait=false

if kubectl get nodeclaim -o name 2>/dev/null | grep -q .; then
  kubectl wait --for=delete nodeclaim --all --timeout=600s || exit 1
fi

if kubectl get nodepool default >/dev/null 2>&1; then
  kubectl wait --for=delete nodepool/default --timeout=600s || exit 1
fi
kubectl delete ec2nodeclass default --ignore-not-found

kubectl get nodeclaim
kubectl get nodepool
kubectl get ec2nodeclass
kubectl get nodes
```

NodeClaim 삭제가 타임아웃되면 Karpenter 로그와 NodeClaim 이벤트, 연결된 EC2 인스턴스·볼륨을 조사한다. finalizer만 제거하면 실제 AWS 리소스가 남을 수 있으므로 이 문서에서는 강제 제거하지 않는다.

정상 기준: `NodeClaim`, `NodePool`, `EC2NodeClass`가 없고 Karpenter EC2 노드가 남지 않는다.

---

## 3. 애플리케이션 ArgoCD Application 삭제

리소스 finalizer를 설정해 Application과 하위 리소스를 함께 삭제한다. 기존에 다른 finalizer가 있으면 배열을 덮어쓰지 않고 원인을 먼저 조사한다.

```bash
APP_PRESENT=$(kubectl get application url-shortener -n argocd --ignore-not-found -o name) || exit 1
if [ -n "$APP_PRESENT" ]; then
  kubectl get application url-shortener -n argocd -o json \
    | jq -e '(.metadata.finalizers // []) | all(. == "resources-finalizer.argocd.argoproj.io")' >/dev/null \
    || { echo '애플리케이션에 예상하지 못한 종료 처리 항목(finalizer)이 있습니다'; exit 1; }

  kubectl patch application url-shortener \
    -n argocd \
    --type=merge \
    -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}' || exit 1

  kubectl delete application url-shortener -n argocd || exit 1
  kubectl wait --for=delete application/url-shortener -n argocd --timeout=300s || exit 1
else
  printf 'url-shortener Application은 이미 없습니다. 하위 리소스를 계속 확인합니다.\n'
fi
```

확인:

```bash
kubectl get deployment,service,ingress,hpa,pdb,networkpolicy,job,servicemonitor,prometheusrule -n url-shortener
```

Redis는 Helm으로 별도 설치했고 Traefik 라우팅 Application은 다음 단계에서 삭제하므로, Redis Service와 Traefik Ingress·리다이렉트 Service가 이 시점에 남는 것은 정상이다. 앱 차트의 Deployment·Service·HPA·PDB·ServiceMonitor·PrometheusRule·NetworkPolicy·PostSync Job 잔여분을 구분해 확인하고, 예상 밖 리소스가 남았다면 다음 단계로 넘어가지 않는다.

---

## 4. Traefik 라우팅 및 Controller 삭제

EKS와 ArgoCD가 살아 있을 때 다음 순서로 삭제한다.

1. 수동 부트스트랩된 `argocd-traefik-https` Ingress와 해당 Certificate·TLS Secret을 삭제한다.
2. `url-shortener-traefik-routing` Application을 cascade 삭제해 앱 Ingress, Middleware, HTTP 리다이렉트와 ClusterIssuer를 제거한다.
3. 앱 Certificate(`url-shortener-tls`)와 TLS Secret, 남은 `letsencrypt-traefik` ClusterIssuer를 확인·삭제한다.
4. `traefik` Application을 삭제해 `LoadBalancer` Service와 NLB 정리를 시작한다.

`traefik` Application 삭제 과정에서 `LoadBalancer` Service가 제거되고 AWS Load Balancer Controller가 NLB 정리를 수행한다. 따라서 EKS 또는 AWS Load Balancer Controller를 먼저 삭제하거나 `terraform destroy`를 먼저 실행하면 Subnet과 Internet Gateway 삭제가 `DependencyViolation`으로 실패할 수 있다.

삭제 전 현재 NLB 주소를 기록한다.

```bash
TRAEFIK_NLB_DNS=$(kubectl get service traefik \
  -n traefik \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' \
  2>/dev/null || true)

printf 'Traefik NLB 주소: %s\n' "$TRAEFIK_NLB_DNS"
```

수동 설치한 Argo CD HTTPS 경로를 먼저 제거한다. Ingress를 삭제한 뒤 Certificate를 삭제해야 cert-manager가 다시 만들지 않는다.

```bash
kubectl delete ingress argocd-traefik-https -n argocd --ignore-not-found
kubectl delete certificate argocd-traefik-tls -n argocd --ignore-not-found
kubectl delete secret argocd-traefik-tls -n argocd --ignore-not-found
```

Traefik 라우팅 Application을 cascade 삭제한다.

```bash
ROUTING_APP=$(kubectl get application url-shortener-traefik-routing -n argocd --ignore-not-found -o name) || exit 1
if [ -n "$ROUTING_APP" ]; then
  kubectl get application url-shortener-traefik-routing -n argocd -o json \
    | jq -e '(.metadata.finalizers // []) | all(. == "resources-finalizer.argocd.argoproj.io")' >/dev/null \
    || { echo '애플리케이션에 예상하지 못한 종료 처리 항목(finalizer)이 있습니다'; exit 1; }

  kubectl patch application url-shortener-traefik-routing \
    -n argocd \
    --type=merge \
    -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}'

  kubectl delete application url-shortener-traefik-routing -n argocd
  kubectl wait --for=delete application/url-shortener-traefik-routing \
    -n argocd \
    --timeout=300s
else
  kubectl delete ingress url-shortener-traefik-ingress url-shortener-traefik-http-redirect \
    -n url-shortener \
    --ignore-not-found

  kubectl delete middleware url-shortener-ratelimit url-shortener-ipallowlist \
    -n url-shortener \
    --ignore-not-found

  kubectl delete service redirect-308 \
    -n url-shortener \
    --ignore-not-found
fi
```

앱 TLS Certificate와 Secret, 라우팅 Application이 남긴 ClusterIssuer가 있는지 확인하고 삭제한다.

```bash
kubectl delete certificate url-shortener-tls \
  -n url-shortener \
  --ignore-not-found

kubectl delete secret url-shortener-tls \
  -n url-shortener \
  --ignore-not-found

kubectl delete clusterissuer letsencrypt-traefik \
  --ignore-not-found
```

Traefik Controller Application을 cascade 삭제한다.

```bash
TRAEFIK_APP=$(kubectl get application traefik -n argocd --ignore-not-found -o name) || exit 1
if [ -n "$TRAEFIK_APP" ]; then
  kubectl get application traefik -n argocd -o json \
    | jq -e '(.metadata.finalizers // []) | all(. == "resources-finalizer.argocd.argoproj.io")' >/dev/null \
    || { echo '애플리케이션에 예상하지 못한 종료 처리 항목(finalizer)이 있습니다'; exit 1; }

  kubectl patch application traefik \
    -n argocd \
    --type=merge \
    -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}'

  kubectl delete application traefik -n argocd
  kubectl wait --for=delete application/traefik \
    -n argocd \
    --timeout=300s
else
  kubectl delete namespace traefik --ignore-not-found
fi

TRAEFIK_SERVICE=$(kubectl get service traefik -n traefik --ignore-not-found -o name) || exit 1
if [ -n "$TRAEFIK_SERVICE" ]; then
  kubectl wait --for=delete service/traefik \
    -n traefik \
    --timeout=300s
fi

kubectl get service -A --field-selector spec.type=LoadBalancer
```

---

## 5. 과거 ALB Ingress 정리

현재 설치 가이드는 공개 HTTP ALB Ingress를 설치하지 않는다. 이전 설치에서 `argocd-ingress`나 `grafana-ingress`가 남은 경우에만 AWS Load Balancer Controller가 살아 있을 때 삭제한다.

```bash
kubectl delete ingress argocd-ingress \
  -n argocd \
  --ignore-not-found

kubectl delete ingress grafana-ingress \
  -n monitoring \
  --ignore-not-found

kubectl get ingress -A
```

---

## 6. AWS Load Balancer 삭제 시작 확인 — 대기 중에는 7~10절 진행

4~5절에서 이 프로젝트의 `LoadBalancer` Service와 Ingress 삭제를 요청했다. Kubernetes 오브젝트가 사라져도 AWS NLB·ALB·Target Group·ENI·보안 그룹의 삭제는 비동기로 이어질 수 있다. **여기서 AWS 삭제 완료를 기다리지 말고** 아래 Kubernetes 상태를 확인한 다음 7~10절의 독립적인 정리를 진행한다.

```bash
kubectl get service -A --field-selector spec.type=LoadBalancer
kubectl get ingress -A
```

이 프로젝트의 LoadBalancer Service나 Ingress가 예상 밖으로 남아 있으면 원인을 조사하고 진행하지 않는다. AWS 정리가 진행되는 동안에도 **Argo CD, AWS Load Balancer Controller, EKS와 EBS CSI 드라이버는 유지**한다. 7~10절 완료 후 11절에서 현재 Terraform VPC의 Load Balancer·Target Group·NLB ENI·`k8s-` 보안 그룹이 모두 0인지 한 번에 확인한다. 확인 전에는 Controller·EKS 삭제나 `terraform destroy`를 실행하지 않는다.

---

## 7. 나머지 ArgoCD Application 삭제

Karpenter CR과 노드, 인증서 리소스를 먼저 제거한 뒤 각 Application을 삭제한다. `kube-prometheus-stack`은 현재 Argo CD가 관리하므로 Helm 삭제보다 먼저 Application을 제거한다. 이 Application이 살아 있으면 삭제한 모니터링 리소스를 다시 만들 수 있다. 과거에 `aws-load-balancer-controller` Application도 적용했다면 **11절의 AWS 종속 리소스 0개 확인 후** 제거한다. Controller의 Helm release와 Argo CD Application이 동시에 있으면 실제 소유권을 확인한다.

```bash
for APP_NAME in kube-prometheus-stack karpenter cert-manager metrics-server; do
  APP_PRESENT=$(kubectl get application "$APP_NAME" -n argocd --ignore-not-found -o name) || exit 1
  if [ -n "$APP_PRESENT" ]; then
    kubectl get application "$APP_NAME" -n argocd -o json \
      | jq -e '(.metadata.finalizers // []) | all(. == "resources-finalizer.argocd.argoproj.io")' >/dev/null \
      || { echo "${APP_NAME} 애플리케이션에 예상하지 못한 종료 처리 항목(finalizer)이 있습니다"; exit 1; }

    kubectl patch application "$APP_NAME" \
      -n argocd \
      --type=merge \
      -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}' || exit 1

    kubectl delete application "$APP_NAME" -n argocd || exit 1
    kubectl wait --for=delete "application/$APP_NAME" -n argocd --timeout=300s || exit 1
  fi
done

kubectl get application -n argocd
```

정상 기준: 위 네 Application이 남지 않는다. 과거 `aws-load-balancer-controller` Application만 있다면 11절의 AWS 종속 리소스 확인 전까지 유지한다.

---

## 8. 모니터링·Redis 및 선택 설치한 로그 수집기 정리

현재 설치 가이드는 Loki·Promtail을 설치하지 않는다. 먼저 릴리스 목록과 소유권을 확인한다. Argo CD가 설치한 Prometheus는 별도 Helm 릴리스가 아닐 수 있다. 아래 조회 결과를 검토하고 **이 프로젝트가 소유한** 과거 수동 Helm 릴리스만 선택해 정리한다.

```bash
helm list -n monitoring
helm list -n url-shortener
```

확인한 릴리스에 대해서만 아래 명령 블록을 실행한다. 이름이 같은 다른 팀의 릴리스가 있으면 중단한다. 목록을 읽지 못한 경우에도 삭제를 시도하지 않는다.

```bash
MONITORING_RELEASES=$(helm list -n monitoring -o json) || exit 1
for RELEASE in promtail loki kube-prometheus-stack; do
  if jq -e --arg name "$RELEASE" 'any(.[]; .name == $name)' <<< "$MONITORING_RELEASES" >/dev/null; then
    helm uninstall "$RELEASE" -n monitoring || exit 1
  fi
done

APP_RELEASES=$(helm list -n url-shortener -o json) || exit 1
if jq -e 'any(.[]; .name == "redis")' <<< "$APP_RELEASES" >/dev/null; then
  helm uninstall redis -n url-shortener || exit 1
fi

helm list -A
```

---

## 9. PVC와 EBS 볼륨 정리

Application·Helm 정리 이후 남은 PVC를 삭제한다. 먼저 `gp2` StorageClass, PVC/PV의 실제 소유권, reclaim policy와 EBS Volume ID를 조회해 **삭제 전에 기록**한다. `Delete`는 PVC 삭제 후 CSI 드라이버가 EBS 볼륨을 삭제해야 하고, `Retain`은 볼륨이 남으므로 별도 보관·비용 결정을 기록한다. 필요한 데이터는 먼저 백업한다.

```bash
kubectl get storageclass gp2 -o yaml
kubectl get pvc -n monitoring
kubectl get pvc -n url-shortener
kubectl get pv -o json | jq -r '.items[] | select(.spec.claimRef.namespace == "monitoring" or .spec.claimRef.namespace == "url-shortener") | [.metadata.name, .spec.claimRef.namespace, .spec.claimRef.name, .spec.persistentVolumeReclaimPolicy, (.spec.csi.volumeHandle // "-")] | @tsv'
```

대상 PVC와 Volume ID, `Delete`·`Retain` 결정을 확인한 뒤 **별도로** 삭제한다. 다른 Namespace의 PVC/PV는 건드리지 않는다.

```bash
kubectl delete pvc -n monitoring --all --ignore-not-found
kubectl delete pvc -n url-shortener --all --ignore-not-found
kubectl get pvc -n monitoring
kubectl get pvc -n url-shortener
kubectl get pv -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,RECLAIM:.spec.persistentVolumeReclaimPolicy,VOLUME:.spec.csi.volumeHandle'
```

기록한 `Delete` 대상 Volume ID마다 아래 조회를 실행한다. 해당 볼륨이 없다는 `InvalidVolume.NotFound`만 삭제 완료의 근거로 인정하고, 볼륨이 남아 있거나 권한·네트워크 오류라면 EKS/EBS CSI 드라이버 삭제 전에 멈춘다. `Retain` 볼륨은 자동 삭제되지 않으며 보존 대상을 최종 점검 기록에 남긴다.

```bash
: "${VOLUME_ID:?삭제 전 기록한 EBS Volume ID를 설정하세요}"
aws ec2 describe-volumes --region "$AWS_REGION" --volume-ids "$VOLUME_ID"
```

---

## 10. 수동 Secret 삭제

Namespace를 삭제하면 같이 제거되지만 정리 결과를 명확히 확인하기 위해 먼저 삭제한다.

```bash
kubectl delete secret url-shortener-secret \
  -n url-shortener \
  --ignore-not-found

kubectl delete secret grafana-admin-secret alertmanager-slack-secret alertmanager-heartbeat-secret \
  -n monitoring \
  --ignore-not-found
```

---

## 11. AWS 종속 리소스 일괄 확인 후 Argo CD·ALB Controller 삭제

7~10절에서 Argo CD가 관리하던 앱, Redis·모니터링, PVC·Secret을 정리하는 동안 AWS의 Load Balancer 삭제가 진행됐다. 이제 Controller·EKS를 내리기 전에 **현재 Terraform VPC**의 Load Balancer, Target Group, NLB ENI, Kubernetes 보안 그룹을 같은 루프에서 확인한다. 각 항목은 모두 `0`이어야 한다. 루프를 합치는 것은 대기 순서를 줄이는 것일 뿐, 통과 조건을 완화하는 것이 아니다.

```bash
for attempt in {1..40}; do
  LB_COUNT=$(aws elbv2 describe-load-balancers \
    --region "$AWS_REGION" \
    --query "length(LoadBalancers[?VpcId=='${VPC_ID}'])" \
    --output text) || exit 1

  TG_COUNT=$(aws elbv2 describe-target-groups \
    --region "$AWS_REGION" \
    --query "length(TargetGroups[?VpcId=='${VPC_ID}'])" \
    --output text) || exit 1

  NLB_ENI_COUNT=$(aws ec2 describe-network-interfaces \
    --region "$AWS_REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" \
    --query "length(NetworkInterfaces[?InterfaceType=='network_load_balancer'])" \
    --output text) || exit 1

  K8S_SG_COUNT=$(aws ec2 describe-security-groups \
    --region "$AWS_REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" \
    --query "length(SecurityGroups[?starts_with(GroupName, 'k8s-')])" \
    --output text) || exit 1

  printf '남은 LB=%s, Target Group=%s, NLB ENI=%s, k8s- SG=%s\n' \
    "$LB_COUNT" "$TG_COUNT" "$NLB_ENI_COUNT" "$K8S_SG_COUNT"

  [ "$LB_COUNT" = "0" ] && [ "$TG_COUNT" = "0" ] && \
    [ "$NLB_ENI_COUNT" = "0" ] && [ "$K8S_SG_COUNT" = "0" ] && break
  sleep 15
done

[ "$LB_COUNT" = "0" ] && [ "$TG_COUNT" = "0" ] && \
  [ "$NLB_ENI_COUNT" = "0" ] && [ "$K8S_SG_COUNT" = "0" ] || \
  { echo 'AWS Load Balancer 종속 리소스가 남아 있습니다. Controller·EKS 삭제를 중단합니다'; exit 1; }
```

최대 약 10분 대기 후에도 남으면 아래 진단 명령을 실행해 리소스의 소유자와 관련 Ingress/Service를 조사한다. AWS 조회 실패를 `0`개로 간주하지 않는다. 문제가 해결되어 위 통과 조건을 다시 충족하기 전에는 다음 삭제 단계로 진행하지 않는다.

```bash
aws elbv2 describe-load-balancers \
  --region "$AWS_REGION" \
  --query "LoadBalancers[?VpcId=='${VPC_ID}'].[LoadBalancerArn,LoadBalancerName,DNSName]"

aws elbv2 describe-target-groups \
  --region "$AWS_REGION" \
  --query "TargetGroups[?VpcId=='${VPC_ID}'].[TargetGroupArn,TargetGroupName]"

aws ec2 describe-network-interfaces \
  --region "$AWS_REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query "NetworkInterfaces[?InterfaceType=='network_load_balancer'].[NetworkInterfaceId,Description,Status]"

aws ec2 describe-security-groups \
  --region "$AWS_REGION" \
  --filters "Name=vpc-id,Values=$VPC_ID" \
  --query "SecurityGroups[?starts_with(GroupName, 'k8s-')].[GroupId,GroupName,Description]"

kubectl get service -A --field-selector spec.type=LoadBalancer
kubectl get ingress -A
```

**위 통과 조건이 모두 충족된 뒤에만** 과거에 Argo CD로 설치한 `aws-load-balancer-controller` Application을 제거한다. 현재 설치 가이드에서는 Controller를 Helm으로 설치했으므로 이 Application은 보통 없다. Helm release와 Application이 함께 있으면 소유권을 먼저 확인한다.

```bash
ALB_APP=$(kubectl get application aws-load-balancer-controller -n argocd --ignore-not-found -o name) || exit 1
if [ -n "$ALB_APP" ]; then
  kubectl get application aws-load-balancer-controller -n argocd -o json \
    | jq -e '(.metadata.finalizers // []) | all(. == "resources-finalizer.argocd.argoproj.io")' >/dev/null \
    || { echo 'ALB Controller Application에 예상하지 못한 finalizer가 있습니다'; exit 1; }

  kubectl patch application aws-load-balancer-controller -n argocd \
    --type=merge -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}' || exit 1
  kubectl delete application aws-load-balancer-controller -n argocd || exit 1
  kubectl wait --for=delete application/aws-load-balancer-controller -n argocd --timeout=300s || exit 1
fi

REMAINING_APPS=$(kubectl get application -n argocd -o name) || exit 1
[ -z "$REMAINING_APPS" ] || { printf '남은 Application:\n%s\n' "$REMAINING_APPS"; exit 1; }

helm uninstall argocd -n argocd --ignore-not-found || exit 1
helm uninstall aws-load-balancer-controller -n kube-system --ignore-not-found || exit 1
helm list -A
```

Controller 삭제 후 12절에서 Namespace를 정리한다. Route53은 조회만 하고 삭제·변경하지 않는다.

---

## 12. 실습 Namespace 삭제

```bash
kubectl delete namespace monitoring --ignore-not-found
kubectl delete namespace url-shortener --ignore-not-found
kubectl delete namespace karpenter --ignore-not-found
kubectl delete namespace cert-manager --ignore-not-found
kubectl delete namespace traefik --ignore-not-found
kubectl delete namespace argocd --ignore-not-found

kubectl get namespace
```

`kube-system`, `kube-public`, `kube-node-lease`, `default`는 직접 삭제하지 않는다.

---

## 13. Route53 보존 — 삭제·변경 금지

`종료` 또는 전체 인프라 정리 시 Route53의 공개 Hosted Zone, NS/SOA, `api.bidservice.store`·`argocd.bidservice.store` A Alias와 그 밖의 레코드는 **모두 그대로 둔다.** Route53은 Terraform 관리 대상이 아니며, 이 절차에서는 레코드 `DELETE`·`UPSERT`나 Hosted Zone 삭제를 실행하지 않는다. DNS가 이전 NLB를 가리켜 서비스가 응답하지 않더라도 정리 과정에서 수정하지 않는다.

필요한 경우 아래 **조회 전용** 명령으로 보존 상태만 확인한다. 같은 이름의 사설 Zone이 있을 수 있으므로 실제 공개 Hosted Zone ID를 확인하고 `HOSTED_ZONE_ID`에 지정한다.

```bash
aws route53 list-hosted-zones-by-name --dns-name bidservice.store \
  --query 'HostedZones[].[Id,Name,Config.PrivateZone]' --output table
: "${HOSTED_ZONE_ID:?확인한 공개 Hosted Zone ID를 설정하세요}"
aws route53 list-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" \
  --query "ResourceRecordSets[?Name=='api.bidservice.store.' || Name=='argocd.bidservice.store.']" --output json
```

재설치 시 새 Traefik NLB를 가리키도록 Alias를 바꾸는 작업은 **설치 가이드 8절의 별도 설치 단계**에서 수행한다. `종료` 절차에 DNS 변경을 포함하지 않는다.

---

## 14. Terraform 리소스 삭제

Kubernetes 리소스, Load Balancer, Target Group, PVC 정리가 끝난 뒤 실행한다. 운영 DB와 ECR의 보존 정책 때문에 아래 사전 단계를 먼저 마쳐야 한다. 운영 state에서 `environment=practice`로 바꾸거나 S3 state 객체를 지워서 보호 장치를 우회하지 않는다.

### 14-1. RDS 삭제 보호를 별도 변경으로 해제

삭제 승인과 보관할 백업·스냅샷을 확인한다. 현재 `terraform/rds.tf`의 Primary는 `deletion_protection = var.environment == "production"`이다. Primary가 이미 Terraform state와 AWS에서 제거된 재개 상황이면 이 보호 해제 단계는 건너뛴다. state와 AWS가 서로 다르면 먼저 원인을 조사한다. Primary가 남아 있을 때만 승인된 정리 작업에서 `deletion_protection = false`로 임시 변경한다.

비밀번호와 마지막 적용 `TF_VAR_db_password_rotation_version`을 보호된 경로에서 같은 셸의 plan·apply에 제공한다. `terraform.tfvars`, `*.auto.tfvars`, JSON tfvars 및 명시적 `-var-file`이 환경변수보다 우선할 수 있다. 아래 명령은 비밀번호 **값을 출력하지 않고 충돌 파일 경로만** 보여준다. 출력이 있으면 입력 원천을 확정할 때까지 중단한다.

```bash
cd "$PROJECT_ROOT/terraform"
test -n "${TF_VAR_db_password:-}"
test -n "${TF_VAR_db_password_rotation_version:-}"
find . -maxdepth 1 -type f \( -name 'terraform.tfvars' -o -name 'terraform.tfvars.json' -o -name '*.auto.tfvars' -o -name '*.auto.tfvars.json' \) \
  -exec rg -l '^[[:space:]]*"?db_password"?[[:space:]]*[:=]' {} +
terraform state list
```

Primary의 삭제 보호만 변경되도록 코드를 임시 수정한 뒤 계획을 **별도로** 실행한다. 예상하지 못한 RDS 비밀번호 회전·Replica 변경·다른 리소스 변경이 있으면 멈춘다.

```bash
terraform plan
```

현재 저장소에는 `scripts/guarded-apply.sh`가 없다. 계획에서 Primary 삭제 보호 해제 **한 건만** 있는지 직접 확인하고, 같은 입력·버전으로 `terraform apply`를 실행한다. apply가 다시 보여주는 계획도 확인한 뒤에만 `yes`로 승인하고, 결과가 `False`인지 검증한다. 일반 `terraform apply`는 회전 버전 하향이나 예상 밖 비밀번호 변경을 별도로 차단하지 않으므로, 계획에 RDS 회전·교체 또는 다른 변경이 보이면 중단한다.

```bash
terraform apply
aws rds describe-db-instances \
  --db-instance-identifier urlshortener-postgres \
  --region "$AWS_REGION" \
  --query 'DBInstances[0].DeletionProtection'
```

결과가 `False`인지 확인한다. 임시 코드 변경은 삭제가 끝난 뒤 원래 운영 보호 설정으로 복구해 다음 재설치에 반영한다. Primary는 삭제할 때 고유 이름의 최종 스냅샷을 만들고, Read Replica는 최종 스냅샷을 만들지 않는다.

### 14-2. ECR 이미지 보존 대상을 확인하고 저장소 비우기

운영 ECR은 `repository_force_delete=false`여서 이미지가 남아 있으면 `terraform destroy`가 저장소 삭제에 실패한다. 현재 배포와 이전 안정 이미지의 태그·digest를 먼저 확인하고 보관할 이미지는 별도 저장소에 복사하거나 보존 결정을 기록한다. 전체 이미지 삭제가 승인된 경우에만 각 digest를 확인해 `aws ecr batch-delete-image`로 제거한다. 아래의 `imageDigest=sha256:...`는 **확인한 실제 digest로 바꿔 한 건씩 실행**한다.

```bash
aws ecr list-images --repository-name urlshortener --region "$AWS_REGION" \
  --query 'imageIds[*].[imageTag,imageDigest]' --output table

# 보존 결정 후 승인된 digest마다 실행
aws ecr batch-delete-image --repository-name urlshortener --region "$AWS_REGION" \
  --image-ids imageDigest=sha256:...

aws ecr list-images --repository-name urlshortener --region "$AWS_REGION" \
  --query 'imageIds' --output json
```

마지막 결과가 빈 목록이어야 한다. 저장소 자체를 AWS 콘솔이나 CLI에서 강제 삭제하지 않는다. Terraform state와 실제 저장소가 달라져 이후 destroy가 실패할 수 있다.

### 14-3. 삭제 계획 확인 후 Terraform 실행

RDS 삭제 보호가 해제됐고 ECR 이미지가 비었으며, 앞 절의 AWS 종속 리소스도 정리됐는지 확인한다. **최종 스냅샷이 필요한 Primary `urlshortener-postgres`는 `available`이어야 한다.** Read Replica가 아직 있다면 그 상태도 확인한다. `modifying`, `stopping`, `stopped`, `deleting` 또는 예상 밖 상태에서는 삭제를 시도하지 말고 원인을 조사한다. 특히 Primary가 `available`이 아니면 RDS가 최종 스냅샷을 만들지 못해 destroy가 실패할 수 있다. 이미 삭제된 인스턴스가 있다면 Terraform state와 대조한다.

```bash
aws rds describe-db-instances --region "$AWS_REGION" \
  --query "DBInstances[?DBInstanceIdentifier=='urlshortener-postgres' || DBInstanceIdentifier=='urlshortener-postgres-replica'].[DBInstanceIdentifier,DBInstanceStatus,DeletionProtection,PendingModifiedValues]" \
  --output json
```

Primary가 존재하며 `available` 전환을 기다리는 정상적인 작업 중일 때만 다음을 사용한다. 제한 시간에 실패하거나 상태가 맞지 않으면 **여기서 멈춘다.** Read Replica가 존재하면 동일하게 확인한다.

Primary가 남아 있는 경우:

```bash
aws rds wait db-instance-available --db-instance-identifier urlshortener-postgres --region "$AWS_REGION"
```

Read Replica가 남아 있는 경우에만 별도로 실행한다.

```bash
aws rds wait db-instance-available --db-instance-identifier urlshortener-postgres-replica --region "$AWS_REGION"
```

기다린 뒤 위 `describe-db-instances` 조회를 다시 실행해 존재하는 각 인스턴스가 실제로 `available`인지 확인한다.

plan과 destroy에는 동일한 운영 backend, 환경 변수, 프로젝트 이름과 비밀번호 회전 버전을 사용한다. Spot 서비스 연결 역할은 다른 워크로드가 쓰는지 **destroy 전에** 확인한다. 다른 사용처가 있으면 무조건 삭제를 진행하지 말고 역할의 Terraform 소유권과 보존 방안을 검토한다. 최종 스냅샷 이름과 destroy 결과를 기록한다.

```bash
cd "$PROJECT_ROOT/terraform"
terraform plan -destroy
```

삭제 대상·보존 대상에 Route53이 없고, RDS 최종 스냅샷과 ECR 이미지 처리가 예상대로인지 계획을 검토한다. 예상 밖 리소스가 보이면 중단한다. **계획 검토 후 별도 실행**하며, destroy가 다시 보여주는 계획과 확인 질문도 검토한다.

```bash
terraform destroy
terraform state list
```

완료되면 `terraform/rds.tf`의 임시 변경을 원래 운영 보호 설정으로 되돌린다. 이 과정에서 다음 리소스가 삭제된다.

- EKS Cluster와 관리형 Node Group
- VPC, Subnet, NAT Gateway, Internet Gateway
- RDS Primary와 Read Replica. 운영 Primary의 최종 스냅샷은 남는다.
- 이미지가 비어 있는 ECR Repository
- Karpenter IAM Role, SQS, EventBridge, Spot 서비스 연결 역할
- ALB Controller와 GitHub Actions IAM 리소스

`terraform/bootstrap-state-bucket.sh`가 만든 S3 state 버킷과 버전, Terraform이 관리하지 않는 Route53 Hosted Zone과 모든 DNS 레코드는 남는다. state 버킷은 추후 복구·감사를 위해 삭제하지 않는다. Spot 서비스 연결 역할을 다른 워크로드도 사용 중이면 Terraform 삭제가 실패할 수 있으므로 다른 사용처를 확인한다.

---

## 15. 최종 검증

Terraform state가 비어 있는지 확인한다.

```bash
terraform state list
```

출력이 없어야 한다.

AWS 리소스 확인:

```bash
aws eks describe-cluster \
  --name "$CLUSTER_NAME" \
  --region "$AWS_REGION"

aws rds describe-db-instances \
  --region "$AWS_REGION" \
  --query "DBInstances[?starts_with(DBInstanceIdentifier, 'urlshortener-')].DBInstanceIdentifier"

aws elbv2 describe-load-balancers \
  --region "$AWS_REGION" \
  --query "LoadBalancers[?VpcId=='${VPC_ID}'].[LoadBalancerName,DNSName]"

aws ec2 describe-vpcs \
  --vpc-ids "$VPC_ID" \
  --region "$AWS_REGION"

aws ecr describe-repositories \
  --repository-names urlshortener \
  --region "$AWS_REGION"

aws ec2 describe-instances --region "$AWS_REGION" \
  --filters "Name=tag:Name,Values=karpenter-node-url-shortener" \
    "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table

aws ec2 describe-nat-gateways --region "$AWS_REGION" \
  --filter "Name=vpc-id,Values=$VPC_ID" \
  --query "NatGateways[?State!='deleted'].[NatGatewayId,State]" --output table

aws sqs list-queues --region "$AWS_REGION" \
  --queue-name-prefix Karpenter-urlshortener --output json
```

EKS, VPC, ECR 명령은 `ResourceNotFound` 또는 해당 리소스가 없다는 오류가 정상이다. RDS와 Load Balancer 조회 결과는 빈 배열이어야 한다. Karpenter EC2·활성 NAT Gateway 목록은 비어 있어야 하며, Karpenter SQS 큐도 없어야 한다. 다른 워크로드가 쓰는 서비스 연결 역할은 보존 대상으로 별도 기록한다.

운영 Primary의 최종 스냅샷과 원격 state 버킷은 삭제 대상이 아니다. 아래 목록에는 과거 스냅샷도 포함될 수 있으므로 **이번 destroy에서 생성된 정확한 식별자**를 구분해 기록한다. 스냅샷 상태, 보관 기간, state 객체와 버전이 남아 있는지 확인한다. state 원문은 터미널 로그나 이슈에 출력하지 않는다.

```bash
aws rds describe-db-snapshots --region "$AWS_REGION" --snapshot-type manual \
  --query "DBSnapshots[?starts_with(DBSnapshotIdentifier, 'urlshortener-postgres-final-snapshot')].[DBSnapshotIdentifier,Status,SnapshotCreateTime]" \
  --output table

aws s3api head-bucket --bucket urlshortener-tfstate-716174522908-ap-northeast-2
aws s3api list-object-versions \
  --bucket urlshortener-tfstate-716174522908-ap-northeast-2 \
  --prefix urlshortener/production/terraform.tfstate \
  --query 'Versions[*].[Key,VersionId,IsLatest,LastModified]' --output table
```

이번 최종 스냅샷의 식별자가 확인됐다면 다음 명령으로 `available`까지 기다린다. 실패·시간 초과 시 완료로 표시하지 않는다.

```bash
: "${FINAL_SNAPSHOT_ID:?이번 destroy에서 생성된 최종 스냅샷 ID를 설정하세요}"
aws rds wait db-snapshot-available --db-snapshot-identifier "$FINAL_SNAPSHOT_ID" --region "$AWS_REGION"
```

최종 스냅샷은 `docs/runbooks/30-rds-ecr-retention.md`의 최소 90일 보관 정책을 따른다. Route53 레코드는 정리 중 변경하지 않는다. 다음 설치 시에만 설치 가이드 8절에서 `api`·`argocd` Alias를 새 NLB로 갱신한다.

0절에서 NAT EIP 할당 ID를 기록한 경우 각 ID를 AWS에서 조회한다. 더 이상 존재하지 않는다는 `InvalidAllocationID.NotFound`가 예상 결과다. 9절에서 기록한 `Delete` 대상 EBS Volume ID도 각각 조회하며 `InvalidVolume.NotFound`가 예상 결과다. 다른 오류, 특히 권한 오류를 ‘리소스 없음’으로 처리하지 않는다. 기록할 ID가 없었던 부분은 건너뛴다. `Retain` 볼륨과 운영 최종 스냅샷은 의도한 보존 대상으로 기록하고 삭제하지 않는다.

```bash
: "${RECORDED_EIP_ID:?0절에서 기록한 EIP 할당 ID를 설정하세요}"
aws ec2 describe-addresses --region "$AWS_REGION" --allocation-ids "$RECORDED_EIP_ID"
```

```bash
: "${VOLUME_ID:?9절에서 기록한 Delete 대상 EBS Volume ID를 설정하세요}"
aws ec2 describe-volumes --region "$AWS_REGION" --volume-ids "$VOLUME_ID"
```

추가 비용 리소스도 AWS 콘솔에서 확인한다.

- EC2 Load Balancer와 Target Group
- 사용하지 않는 EBS Volume과 Snapshot
- NAT Gateway와 Elastic IP
- RDS Snapshot
- Route53 Hosted Zone

---

## 16. GitHub Actions 인증 설정 정리

클러스터와 Argo CD가 삭제된 뒤에도 설치 시 등록한 GitHub Actions Secret `ARGOCD_AUTH_TOKEN`과 변수 `ARGOCD_SERVER`는 GitHub 저장소에 남는다. 해당 값이 이 배포 전용인지 확인한다. 다른 팀이 공유해 사용 중이면 삭제하지 말고 소유자와 처리 방안을 결정한다. 0-1절의 배포 동결이 유지되고 있는지도 다시 확인한다.

```bash
gh secret list --repo f-lab-edu/F-Lab-DevOps
gh variable list --repo f-lab-edu/F-Lab-DevOps
gh run list --repo f-lab-edu/F-Lab-DevOps --workflow ci-cd.yml --limit 5
```

이 배포 전용이고 보존 요청이 없음을 확인했다면 **존재하는 항목만** 아래 명령으로 삭제한다. 재실행 시 이미 없는 항목은 건너뛴다. 새 클러스터를 설치할 때는 Argo CD 토큰을 다시 발급하고, 0-1절에서 중지한 워크플로를 승인 후 다시 활성화해야 한다.

```bash
CURRENT_SECRETS=$(gh secret list --repo f-lab-edu/F-Lab-DevOps --json name --jq '.[].name') || exit 1
if rg -qx 'ARGOCD_AUTH_TOKEN' <<< "$CURRENT_SECRETS"; then
  gh secret delete ARGOCD_AUTH_TOKEN --repo f-lab-edu/F-Lab-DevOps || exit 1
fi

CURRENT_VARIABLES=$(gh variable list --repo f-lab-edu/F-Lab-DevOps --json name --jq '.[].name') || exit 1
if rg -qx 'ARGOCD_SERVER' <<< "$CURRENT_VARIABLES"; then
  gh variable delete ARGOCD_SERVER --repo f-lab-edu/F-Lab-DevOps || exit 1
fi

gh secret list --repo f-lab-edu/F-Lab-DevOps
gh variable list --repo f-lab-edu/F-Lab-DevOps
```

로컬의 보호된 비밀 파일은 별도 개인 보관 대상이다. 재사용·폐기 여부를 소유자가 결정하며, 이 인프라 정리 명령은 자동으로 삭제하지 않는다.

---

## 핵심 삭제 순서

```text
1. Karpenter NodePool·NodeClaim 정리
   ↓
2. 애플리케이션 Application (url-shortener)
   ↓
3. 수동 Argo CD HTTPS Ingress·Certificate·TLS Secret
   ↓
4. Traefik Routing Application과 앱 Certificate·TLS Secret·ClusterIssuer
   ↓
5. Traefik Controller Application 및 과거 ALB Ingress 삭제 요청
   ↓
6. AWS Load Balancer 삭제가 시작됐는지 확인; Controller·EKS는 유지
   ├─ AWS: Load Balancer·Target Group·NLB ENI·k8s- SG 비동기 정리 진행
   └─ 7~10절: 나머지 Argo CD Application(과거 ALB Controller Application 제외),
              Redis·모니터링·로그 릴리스, PVC·Secret 정리
   ↓
11. AWS 네 항목 모두 0 확인 → 과거 ALB Controller Application,
    Argo CD·ALB Controller Helm 릴리스 삭제
   ↓
12. 실습 Namespace 일괄 삭제
   ↓
13. Route53 Hosted Zone·모든 레코드 보존 (조회만 수행)
   ↓
14. RDS 삭제 보호를 별도 apply로 해제, ECR 이미지 보존·정리 후 terraform destroy
   ↓
15. 최종 스냅샷과 S3 state 버킷 보존 확인
   ↓
16. GitHub Actions 배포 전용 Secret·변수 정리와 워크플로 중지 상태 확인
```
