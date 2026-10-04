# 전체 인프라 리소스 정리 가이드

> 대상: `Url-Shortener-EKS-Platform`
>
> 아래 명령은 리소스와 데이터를 실제로 삭제한다. 운영 RDS Primary는 현재 `deletion_protection=true`, `skip_final_snapshot=false`이고, 운영 ECR은 `repository_force_delete=false`다. 따라서 보호 설정과 이미지 보존 대상을 확인하지 않은 `terraform destroy`는 완료되지 않는다. 정상 삭제 시 운영 RDS 최종 스냅샷을 생성하도록 구성했으며, 생성 결과를 확인해 별도로 보관한다.
>
> 본 가이드는 `docs/runbooks/infrastructure-installation-and-validation.md`의 현재 구성을 기준으로 한다. 원본인 `실습 가이드 문서 모음/`은 Git에서 제외되므로 PR에는 동일한 내용의 `docs/runbooks/infrastructure-cleanup.md`를 사용한다. 리소스 정리 실행 전에 현재 AWS 계정·Terraform state·클러스터 상태를 다시 확인한다.

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
```

현재 컨텍스트가 `urlshortener`, AWS 계정이 `716174522908`인지 확인한 뒤 진행한다. Terraform은 `1.14.7`과 운영용 S3 backend key(`urlshortener/production/terraform.tfstate`)를 사용해야 한다. `vpc_id` output이 없거나 state의 리소스가 예상과 다르면 삭제를 진행하기 전에 실제 AWS 리소스와 state를 대조한다. 실습용 state 또는 다른 `project_name`으로 운영 리소스를 정리하지 않는다. 진행 중인 main 배포 Actions가 끝났는지 확인하고 정리 중에는 새 배포를 시작하지 않는다. 현재 배포 이미지, 이전 안정 이미지, RDS 백업·최종 스냅샷 보존 결정을 먼저 기록한다. 상세 정책은 `docs/runbooks/30-rds-ecr-retention.md`를 따른다.

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
kubectl get application url-shortener -n argocd -o json \
  | jq -e '(.metadata.finalizers // []) | all(. == "resources-finalizer.argocd.argoproj.io")' >/dev/null \
  || { echo '애플리케이션에 예상하지 못한 종료 처리 항목(finalizer)이 있습니다'; exit 1; }

kubectl patch application url-shortener \
  -n argocd \
  --type=merge \
  -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}'

kubectl delete application url-shortener -n argocd
kubectl wait --for=delete application/url-shortener -n argocd --timeout=300s
```

확인:

```bash
kubectl get deployment,service,ingress,hpa -n url-shortener
```

Redis는 Helm으로 별도 설치했으므로 이 단계에서는 남아 있는 것이 정상이다.

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
if kubectl get application url-shortener-traefik-routing \
  -n argocd >/dev/null 2>&1; then
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
if kubectl get application traefik -n argocd >/dev/null 2>&1; then
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

if kubectl get service traefik -n traefik >/dev/null 2>&1; then
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

## 6. ALB·NLB·Target Group·AWS 종속 리소스 삭제 대기

Ingress와 Service 오브젝트가 사라져도 AWS Load Balancer 삭제에는 수분이 걸릴 수 있다. 현재 Terraform VPC에 Load Balancer가 하나도 없을 때까지 기다린다.

```bash
for attempt in {1..40}; do
  LB_COUNT=$(aws elbv2 describe-load-balancers \
    --region "$AWS_REGION" \
    --query "length(LoadBalancers[?VpcId=='${VPC_ID}'])" \
    --output text) || exit 1

  printf '남은 로드 밸런서: %s개\n' "$LB_COUNT"

  [ "$LB_COUNT" = "0" ] && break
  sleep 15
done
[ "$LB_COUNT" = "0" ] || { echo '로드 밸런서가 남아 있습니다'; exit 1; }
```

Target Group도 확인한다.

```bash
for attempt in {1..40}; do
  TG_COUNT=$(aws elbv2 describe-target-groups \
    --region "$AWS_REGION" \
    --query "length(TargetGroups[?VpcId=='${VPC_ID}'])" \
    --output text) || exit 1

  printf '남은 대상 그룹: %s개\n' "$TG_COUNT"

  [ "$TG_COUNT" = "0" ] && break
  sleep 15
done
[ "$TG_COUNT" = "0" ] || { echo '대상 그룹이 남아 있습니다'; exit 1; }
```

NLB가 사용하던 Network Interface와 Kubernetes Load Balancer Security Group도 정리되었는지 확인한다.

```bash
for attempt in {1..40}; do
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

  printf '남은 NLB 네트워크 인터페이스: %s개, Kubernetes 보안 그룹: %s개\n' \
    "$NLB_ENI_COUNT" "$K8S_SG_COUNT"

  [ "$NLB_ENI_COUNT" = "0" ] && [ "$K8S_SG_COUNT" = "0" ] && break
  sleep 15
done
[ "$NLB_ENI_COUNT" = "0" ] && [ "$K8S_SG_COUNT" = "0" ] || { echo 'NLB 네트워크 인터페이스 또는 Kubernetes 보안 그룹이 남아 있습니다'; exit 1; }
```

Load Balancer, Target Group, NLB ENI, `k8s-` Security Group 결과가 모두 `0`이어야 한다. 이 확인을 건너뛰면 `terraform destroy`에서 Subnet, Internet Gateway 또는 VPC 삭제가 `DependencyViolation`으로 실패할 수 있다.

각 확인은 최대 약 10분간 기다린다. 제한 시간에 도달하면 아래 명령으로 남은 리소스를 조사한다.

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
```

이 리소스가 남아 있다면 EKS와 AWS Load Balancer Controller를 삭제하지 말고 원인이 된 Ingress 또는 `LoadBalancer` Service가 남았는지 먼저 확인한다.

---

## 7. 나머지 ArgoCD Application 삭제

Karpenter CR과 노드, 인증서 리소스를 먼저 제거한 뒤 각 Application을 삭제한다. `kube-prometheus-stack`은 현재 Argo CD가 관리하므로 Helm 삭제보다 먼저 Application을 제거한다. 이 Application이 살아 있으면 삭제한 모니터링 리소스를 다시 만들 수 있다. 과거에 `aws-load-balancer-controller` Application도 적용했다면 Load Balancer가 모두 사라진 이 시점에 함께 제거한다. Controller의 Helm release와 Argo CD Application이 동시에 있으면 실제 소유권을 확인한다.

```bash
for APP_NAME in kube-prometheus-stack karpenter cert-manager metrics-server aws-load-balancer-controller; do
  if kubectl get application "$APP_NAME" -n argocd >/dev/null 2>&1; then
    kubectl get application "$APP_NAME" -n argocd -o json \
      | jq -e '(.metadata.finalizers // []) | all(. == "resources-finalizer.argocd.argoproj.io")' >/dev/null \
      || { echo "${APP_NAME} 애플리케이션에 예상하지 못한 종료 처리 항목(finalizer)이 있습니다"; exit 1; }

    kubectl patch application "$APP_NAME" \
      -n argocd \
      --type=merge \
      -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}'

    kubectl delete application "$APP_NAME" -n argocd
    kubectl wait --for=delete "application/$APP_NAME" -n argocd --timeout=300s
  fi
done

kubectl get application -n argocd
```

정상 기준: ArgoCD Application이 남지 않는다.

---

## 8. 모니터링·Redis 및 선택 설치한 로그 수집기 정리

현재 설치 가이드는 Loki·Promtail을 설치하지 않는다. 과거 실습에서 설치한 경우에만 해당 Helm release를 확인하고 삭제한다. Prometheus가 Argo CD Application으로 설치됐다면 Helm release가 없을 수 있으므로, `helm uninstall kube-prometheus-stack`은 별도 Helm 설치 이력이 확인된 경우에만 실행한다.

```bash
helm list -n monitoring
helm uninstall promtail -n monitoring --ignore-not-found  # 과거에 Helm으로 설치한 경우
helm uninstall loki -n monitoring --ignore-not-found      # 과거에 Helm으로 설치한 경우
helm uninstall kube-prometheus-stack -n monitoring --ignore-not-found  # 별도 Helm 설치 이력이 있는 경우
helm uninstall redis -n url-shortener --ignore-not-found

helm list -A
```

---

## 9. PVC와 EBS 볼륨 정리

Application·Helm 정리 이후 남은 PVC를 삭제한다. 먼저 실제 `gp2` StorageClass와 각 PV의 reclaim policy를 확인한다. `Delete`이면 CSI 드라이버가 연결된 EBS 볼륨을 삭제해야 하며, `Retain`이면 볼륨이 남는다. 백업이 필요한 PVC의 데이터는 삭제 전에 보관한다.

```bash
kubectl get storageclass gp2 -o yaml
kubectl get pv -o custom-columns='NAME:.metadata.name,RECLAIM:.spec.persistentVolumeReclaimPolicy,STATUS:.status.phase,VOLUME:.spec.csi.volumeHandle'
kubectl delete pvc -n monitoring --all --ignore-not-found
kubectl delete pvc -n url-shortener --all --ignore-not-found

kubectl get pvc -A
kubectl get pv
```

PV가 `Released` 상태로 계속 남는다면 CSI Volume ID와 reclaim policy를 확인해 AWS에서 삭제 상태를 점검한다. `Retain` 볼륨은 자동 삭제되지 않는다.

```bash
kubectl get pv -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,RECLAIM:.spec.persistentVolumeReclaimPolicy,VOLUME:.spec.csi.volumeHandle'
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

## 11. ArgoCD와 ALB Controller 삭제

모든 Application과 AWS Load Balancer가 삭제된 뒤 실행한다.

```bash
helm uninstall argocd -n argocd --ignore-not-found
helm uninstall aws-load-balancer-controller -n kube-system --ignore-not-found

helm list -A
```

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

## 13. Route53 DNS 정리

Route53 Hosted Zone은 Terraform 외부에서 수동 생성했으므로 `terraform destroy`가 삭제하지 않는다. 현재 설치 가이드는 Traefik NLB에 `api.bidservice.store`와 `argocd.bidservice.store` 두 A Alias를 연결한다. 실제 Hosted Zone ID와 각 레코드의 Alias target을 조회해 현재 NLB를 가리키는지 확인한다. Hosted Zone 자체의 삭제는 해당 Zone이 이 프로젝트 전용일 때만 진행한다.

### Route53 레코드와 Hosted Zone을 그대로 유지할 경우

이 단계의 삭제 명령을 실행하지 않는다. `terraform destroy`는 Route53 리소스를 삭제하지 않으므로 Hosted Zone과 다음 Alias 레코드는 그대로 남는다.

- `api.bidservice.store` (운영 Traefik NLB Alias)
- `argocd.bidservice.store` (Argo CD HTTPS Ingress의 Traefik NLB Alias)

다음에 인프라를 다시 설치하면 Traefik NLB DNS 주소가 달라지므로, 기존 Alias의 Target을 새 Traefik NLB 주소로 갱신해야 한다.

### Hosted Zone은 유지하고 현재 NLB Alias만 삭제할 경우

두 레코드 중 현재 정리한 Traefik NLB를 가리키는 Alias만 삭제한다. 4절에서 기록한 NLB DNS를 `TRAEFIK_NLB_DNS`로 다시 설정하고, `HOSTED_ZONE_ID`는 아래 조회 결과에서 실제 Zone을 확인한 뒤 설정한다. 다른 NLB를 가리키거나 Alias가 아닌 레코드는 자동 삭제하지 않는다.

```bash
aws route53 list-hosted-zones-by-name --dns-name bidservice.store
: "${HOSTED_ZONE_ID:?확인한 Hosted Zone ID를 설정하세요}"
: "${TRAEFIK_NLB_DNS:?4절에서 기록한 Traefik NLB DNS를 설정하세요}"

RECORD_NAMES=(
  "api.bidservice.store."
  "argocd.bidservice.store."
)

for RECORD_NAME in "${RECORD_NAMES[@]}"; do
  RECORD_PREFIX=${RECORD_NAME%%.*}
  RECORD_FILE=$(mktemp)
  CHANGE_FILE=$(mktemp)

  aws route53 list-resource-record-sets \
    --hosted-zone-id "$HOSTED_ZONE_ID" \
    --query "ResourceRecordSets[?Name=='${RECORD_NAME}' && Type=='A']" \
    --output json > "$RECORD_FILE" || exit 1

  if jq -e 'length == 0' "$RECORD_FILE" >/dev/null; then
    printf '%s: 삭제할 A Alias 없음\n' "$RECORD_NAME"
    rm -f "$RECORD_FILE" "$CHANGE_FILE"
    continue
  fi

  jq -e --arg target "$TRAEFIK_NLB_DNS" '
    [.[] | select((.AliasTarget.DNSName // "" | ascii_downcase | rtrimstr(".")) ==
                   ($target | ascii_downcase | rtrimstr(".")))]
    | if length == 1 then
        {Changes: [{Action: "DELETE", ResourceRecordSet: .[0]}]}
      else
        error("현재 NLB를 가리키는 A Alias가 정확히 하나가 아닙니다")
      end
  ' "$RECORD_FILE" > "$CHANGE_FILE" || { rm -f "$RECORD_FILE" "$CHANGE_FILE"; exit 1; }

  cat "$CHANGE_FILE"
  CHANGE_ID=$(aws route53 change-resource-record-sets \
    --hosted-zone-id "$HOSTED_ZONE_ID" \
    --change-batch "file://$CHANGE_FILE" \
    --query 'ChangeInfo.Id' \
    --output text) || exit 1

  aws route53 wait resource-record-sets-changed --id "$CHANGE_ID" || exit 1
  rm -f "$RECORD_FILE" "$CHANGE_FILE"
done
```

### Hosted Zone까지 완전히 삭제할 경우

1. 가비아에서 네임서버를 Route53 NS가 아닌 가비아 기본 네임서버로 먼저 변경한다.
2. 네임서버 변경이 반영된 뒤 Hosted Zone의 사용자 생성 레코드를 모두 조회하고 삭제한다. 이 Zone에 다른 서비스의 레코드가 있으면 함께 지워지는 범위를 확인한다.
3. NS와 SOA만 남았을 때 아래 명령을 실행한다.

```bash
aws route53 delete-hosted-zone \
  --id "$HOSTED_ZONE_ID"
```

---

## 14. Terraform 리소스 삭제

Kubernetes 리소스, Load Balancer, Target Group, PVC 정리가 끝난 뒤 실행한다. 운영 DB와 ECR의 보존 정책 때문에 아래 사전 단계를 먼저 마쳐야 한다. 운영 state에서 `environment=practice`로 바꾸거나 S3 state 객체를 지워서 보호 장치를 우회하지 않는다.

### 14-1. RDS 삭제 보호를 별도 변경으로 해제

삭제 승인과 보관할 백업·스냅샷을 확인한다. 현재 `terraform/rds.tf`의 Primary는 `deletion_protection = var.environment == "production"`이다. 승인된 정리 작업에서만 이를 임시로 `deletion_protection = false`로 변경한다. 비밀번호 입력 `TF_VAR_db_password`와 마지막으로 사용한 `TF_VAR_db_password_rotation_version`도 안전한 경로에서 동일하게 제공한다. `terraform plan`에서 Primary의 삭제 보호 변경만 예상되는지 확인하고 **별도 `terraform apply`**로 먼저 반영한다. 예상 밖 변경이 있으면 적용하지 않는다.

```bash
cd "$PROJECT_ROOT/terraform"
terraform plan
terraform apply
aws rds describe-db-instances \
  --db-instance-identifier urlshortener-postgres-primary \
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

RDS 삭제 보호가 해제됐고 ECR 이미지가 비었으며, 앞 절의 AWS 종속 리소스도 정리됐는지 확인한다. plan과 destroy에는 동일한 운영 backend, 환경 변수, 프로젝트 이름과 비밀번호 회전 버전을 사용한다. 최종 스냅샷 이름과 `terraform destroy` 결과를 기록한다.

```bash
cd "$PROJECT_ROOT/terraform"

terraform plan -destroy
terraform destroy
terraform state list
```

삭제 계획을 검토한 뒤에만 확인 질문에 `yes`를 입력한다. 완료되면 `terraform/rds.tf`의 임시 변경을 되돌린다. 이 과정에서 다음 리소스가 삭제된다.

- EKS Cluster와 관리형 Node Group
- VPC, Subnet, NAT Gateway, Internet Gateway
- RDS Primary와 Read Replica. 운영 Primary의 최종 스냅샷은 남는다.
- 이미지가 비어 있는 ECR Repository
- Karpenter IAM Role, SQS, EventBridge, Spot 서비스 연결 역할
- ALB Controller와 GitHub Actions IAM 리소스

`terraform/bootstrap-state-bucket.sh`가 만든 S3 state 버킷과 버전, Terraform이 관리하지 않는 Route53 Hosted Zone은 남는다. state 버킷은 추후 복구·감사를 위해 삭제하지 않는다. Spot 서비스 연결 역할을 다른 워크로드도 사용 중이면 Terraform 삭제가 실패할 수 있으므로 다른 사용처를 확인한다.

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
```

EKS, VPC, ECR 명령은 `ResourceNotFound` 또는 해당 리소스가 없다는 오류가 정상이다. RDS와 Load Balancer 조회 결과는 빈 배열이어야 한다.

운영 Primary의 최종 스냅샷과 원격 state 버킷은 삭제 대상이 아니다. 스냅샷 식별자·상태, 보관 기간, state 객체와 버전이 남아 있는지 확인한다. state 원문은 터미널 로그나 이슈에 출력하지 않는다.

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

최종 스냅샷은 `docs/runbooks/30-rds-ecr-retention.md`의 최소 90일 보관 정책을 따른다. Route53 레코드를 유지했다면 다음 설치 전에 `api`·`argocd` Alias가 새 NLB를 가리키도록 갱신해야 한다.

추가 비용 리소스도 AWS 콘솔에서 확인한다.

- EC2 Load Balancer와 Target Group
- 사용하지 않는 EBS Volume과 Snapshot
- NAT Gateway와 Elastic IP
- RDS Snapshot
- Route53 Hosted Zone

---

## 핵심 삭제 순서

```text
1. Karpenter NodePool·NodeClaim 정리
   ↓
2. 애플리케이션 Application (url-shortener)
   ↓
3. 수동 Argo CD HTTPS Ingress·Certificate·TLS Secret
   ↓
4. Traefik Routing Application (url-shortener-traefik-routing)과 앱 Certificate·TLS Secret·ClusterIssuer
   ↓
5. Traefik Controller Application 및 NLB (traefik)
   ↓
6. 과거 ALB Ingress 잔여분 (있을 때만)
   ↓
7. AWS Load Balancer·Target Group·NLB ENI·k8s Security Group 삭제 대기 (모두 0 확인)
   ↓
8. 나머지 ArgoCD Application (kube-prometheus-stack, karpenter, cert-manager, metrics-server, 과거 ALB Controller Application)
   ↓
9. Redis Helm 릴리스와 과거 수동 설치한 모니터링·로그 릴리스
   ↓
10. PVC·Secret 정리
   ↓
11. ArgoCD·ALB Controller Helm 릴리스
   ↓
12. 실습 Namespace 일괄 삭제
   ↓
13. Route53 (유지 또는 선택 정리)
   ↓
14. RDS 삭제 보호를 별도 apply로 해제, ECR 이미지 보존·정리 후 terraform destroy
   ↓
15. 최종 스냅샷과 S3 state 버킷 보존 확인
```
