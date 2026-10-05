# 인프라 재설치 및 DevOps/SRE 개선 검증 가이드

이 문서는 현재 저장소의 Terraform, Argo CD, Helm 구성을 기준으로 한다. 과거 설치 기록의 완료 표시는 이번 재설치 결과가 아니다. 명령 실행 전에 현재 AWS 계정, 대상 클러스터, Git 브랜치를 확인한다. 비밀번호·접속 URL·Webhook·토큰은 이 문서나 Git에 기록하지 않는다.

기준 저장소: /Users/jounghyeon/dev/Url-Shortener-EKS-Platform  
AWS 리전: ap-northeast-2  
운영 EKS 클러스터와 Helm release: urlshortener, url-shortener

## 0. 배포 전 확인과 단계 통과 원칙

- 운영 CI/CD는 `main`에 `url-shortener/app/**`, `url-shortener/Dockerfile`, `url-shortener/requirements.txt`, `url-shortener/requirements.lock` 변경이 push될 때 자동 실행된다. 새 앱 코드가 있는 PR은 **8절의 Argo CD HTTPS·CI 토큰과 9절의 현재 이미지·앱 상태가 준비되기 전에는 병합하지 않는다.** 이미 병합한 경우에는 과거 실행을 무작정 재시도하지 말고 현재 ECR 태그와 GitOps 상태부터 조사한다.
- Argo CD의 애플리케이션, Traefik 라우팅, Prometheus values는 `main`을 추적한다. 로컬 또는 PR 브랜치의 values 변경은 `main`에 반영되기 전까지 클러스터에 적용되지 않는다. 설치에 필요한 구성 수정은 앱 코드 변경과 분리해 `main`에 먼저 반영한다. 부트스트랩은 현재 `main`의 선언을 기준으로 하고, 새 코드의 배포 판정은 병합 후에 한다.
- CI의 `values.prod.yaml` 커밋·push에는 워크플로의 `contents: write` 권한이 필요하다. 병합 전 실제 브랜치 보호 규칙과 push 권한도 다시 확인한다.
- Argo CD 서버는 4절에서 내부 `ClusterIP`로 설치하고, 8절에서 Traefik HTTPS와 `ARGOCD_SERVER`·`ARGOCD_AUTH_TOKEN`을 준비한다. 기존 `argocd-ingress.yaml`은 공개 HTTP ALB이므로 적용하지 않는다. CI 토큰은 이 애플리케이션의 상태 조회 권한만 부여한다.
- 배포 검증은 클러스터 내부 PostSync Job이 `/readyz`와 `/items`를 호출한다. GitHub 러너가 IP 제한된 `api.bidservice.store`에 접근할 필요가 없고 `SMOKE_TEST_BASE_URL`도 사용하지 않는다.
- **각 적용 직후 명시된 검증이 통과해야만 다음 적용으로 넘어간다.** `Synced`만으로는 부족하며 필요한 `Healthy`, Pod `Ready`, PVC `Bound`, 실제 HTTP/DB 응답을 확인한다. 실패·시간 초과·예상 밖 Terraform 변경이면 중단하고 원인을 조사한다.
- 첫 이미지가 ECR에 없는 경우 9절의 **일회성 이미지 부트스트랩**을 먼저 수행한다. 앱을 `ImagePullBackOff` 상태로 둔 채 일반 CI가 해결해 줄 것이라고 가정하지 않는다. 이전 이미지가 없는 첫 CI 실패 시에는 자동 롤백하지 않고 원인을 조사한다.

## 1. Terraform 원격 state와 인프라

Terraform CLI는 1.14.7을 사용한다. 운영과 실습은 서로 다른 작업 디렉터리, backend key, project_name을 쓴다. 아래는 운영 기준이다. **Terraform 단계에 필요한 비밀 입력은 DB 비밀번호와 회전 버전뿐이다.** `APP_SECRET_FILE`은 RDS endpoint가 만들어진 뒤 6절에서 준비한다.

~~~bash
cd /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/terraform
terraform version
aws sts get-caller-identity --query Account --output text
bash bootstrap-state-bucket.sh
terraform init -backend-config=backend.production.hcl -reconfigure -lockfile=readonly
terraform state list
~~~

AWS 계정은 716174522908이어야 한다. state에 예상 밖 리소스가 있으면 중단한다. `TF_VAR_db_password`와 양의 정수 `TF_VAR_db_password_rotation_version`을 보호된 입력 경로에서 **같은 셸의 plan·apply에** 제공한다. 최초 회전 버전은 `1`; 기존 환경이면 마지막 적용 버전보다 낮추지 않는다. 로컬 `terraform.tfvars`, `*.auto.tfvars`, 명시적인 `-var-file`의 `db_password`는 `TF_VAR_db_password`보다 우선할 수 있다. 다음 조회는 비밀번호 **값이 아닌 충돌 파일 경로만** 출력한다. 출력이 있으면 어느 입력을 쓸지 확정하기 전까지 진행하지 않는다.

~~~bash
test -n "${TF_VAR_db_password:-}"
test -n "${TF_VAR_db_password_rotation_version:-}"
find . -maxdepth 1 -type f \( -name 'terraform.tfvars' -o -name '*.auto.tfvars' \) -exec rg -l '^[[:space:]]*db_password[[:space:]]*=' {} +
terraform validate
terraform plan
~~~

`terraform plan`에서 예상 리소스·변경만 있는지 **사람이 확인한 뒤에만**, 동일한 입력과 버전으로 별도 실행한다. 계획 검토 없이 아래 명령까지 한꺼번에 붙여넣지 않는다. 예상하지 못한 RDS Replica 설정 변경이나 비밀번호 회전이 보이면 멈춘다.

~~~bash
terraform apply
terraform output
aws rds wait db-instance-available --db-instance-identifier urlshortener-postgres --region ap-northeast-2
aws rds wait db-instance-available --db-instance-identifier urlshortener-postgres-replica --region ap-northeast-2
~~~

적용 후 RDS 상태뿐 아니라 6절의 Secret을 준비한 다음 실제 Primary·Replica DB 접속을 검증한다. `available`만으로 새 비밀번호의 인증 성공을 의미하지 않는다. 비밀번호 회전 절차와 `secretRevision` 롤아웃은 `docs/runbooks/32-rds-password-rotation.md`를 따른다.

실습용 backend.practice.hcl을 운영 작업 디렉터리에서 -migrate-state와 함께 사용하지 않는다. 세부 내용은 31-terraform-remote-state.md와 32-rds-password-rotation.md를 따른다.

## 2. kubeconfig와 노드

~~~bash
aws eks update-kubeconfig --region ap-northeast-2 --name urlshortener
kubectl config current-context
kubectl get nodes
~~~

## 3. AWS Load Balancer Controller

Terraform output은 이 저장소의 `terraform/`에서 읽는다. 동일 Controller를 직접 Helm과 Argo CD로 동시에 관리하지 않는다. 최초 설치는 Helm으로 하고, Argo CD로 이관할 때 기존 release의 소유권을 확인한다.

~~~bash
helm repo add eks https://aws.github.io/eks-charts
helm repo update
cd /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/terraform
terraform output -raw alb_controller_role_arn
terraform output -raw vpc_id
~~~

최초 설치는 다음처럼 Helm으로 진행한다. cluster-addons/alb-controller/application.yaml에는 아직 Role ARN placeholder가 있으므로 값을 확인하지 않고 적용하지 않는다.

~~~bash
ALB_ROLE_ARN=$(terraform output -raw alb_controller_role_arn)
VPC_ID=$(terraform output -raw vpc_id)
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller --version 1.14.1 -n kube-system --set clusterName=urlshortener --set serviceAccount.create=true --set serviceAccount.name=aws-load-balancer-controller --set-string "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn=$ALB_ROLE_ARN" --set region=ap-northeast-2 --set "vpcId=$VPC_ID"
kubectl rollout status deployment/aws-load-balancer-controller -n kube-system --timeout=180s
kubectl get serviceaccount aws-load-balancer-controller -n kube-system -o yaml
~~~

## 4. Argo CD

Argo CD를 설치한다. Traefik에서 TLS를 종료하므로 Argo CD 서버는 클러스터 내부 HTTP로 실행한다. 기존 argocd-ingress.yaml과 grafana-ingress.yaml은 공개 HTTP ALB이므로 적용하지 않는다. 초기 접속은 port-forward 또는 보호된 사설 경로를 사용한다.

~~~bash
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update
helm upgrade --install argocd argo/argo-cd -n argocd --create-namespace --set "server.extraArgs[0]=--insecure"
kubectl rollout status deployment/argocd-server -n argocd --timeout=300s
kubectl get pods -n argocd
~~~

## 5. Metrics Server와 Namespace

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/metric-server/application.yaml
kubectl wait -n argocd application/metrics-server --for=jsonpath='{.status.sync.status}'=Synced --timeout=300s
kubectl wait -n argocd application/metrics-server --for=jsonpath='{.status.health.status}'=Healthy --timeout=300s
kubectl rollout status -n kube-system deployment/metrics-server --timeout=300s
kubectl top nodes
~~~

초기 Pod 다운로드·Metrics API 등록에는 시간이 걸릴 수 있다. `kubectl top nodes`가 실패하면 잠시 후 **같은 단계에서 재검증**하고, 성공하기 전에는 Namespace·Redis 단계로 넘어가지 않는다. 현재 Metrics Server 설정에는 kubelet TLS 검증 우회가 남아 있다. `kubectl top` 성공만으로 39번 개선이 완료된 것은 아니다.

~~~bash
kubectl create namespace url-shortener --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
kubectl get namespace url-shortener monitoring
~~~

## 6. Redis와 애플리케이션 Secret

Redis 비밀번호는 문서나 쉘 명령에 직접 넣지 않는다. Git 밖의 제한된 권한을 가진 Helm values 파일을 준비해 standalone Redis를 설치한다. 파일의 auth.password와 애플리케이션 Secret의 REDIS_URL 비밀번호는 같아야 한다.

~~~yaml
architecture: standalone
auth:
  password: <비밀 저장소에서 제공>
master:
  persistence:
    enabled: true
    size: 1Gi
    storageClass: gp2
~~~

~~~bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
helm upgrade --install redis bitnami/redis -n url-shortener --values "$REDIS_HELM_VALUES_FILE"
kubectl wait -n url-shortener pod/redis-master-0 --for=condition=Ready --timeout=300s
kubectl wait -n url-shortener pvc --all --for=jsonpath='{.status.phase}'=Bound --timeout=300s
kubectl get pods,svc,pvc -n url-shortener
~~~

보호된 APP_SECRET_FILE에는 DATABASE_URL, DATABASE_READ_URL, REDIS_URL 세 키를 넣는다. DB host:port는 현재 terraform output의 rds_primary_endpoint와 rds_replica_endpoint를 사용한다. DB 비밀번호의 특수문자는 URL 인코딩한다. 문서에 있던 예전 endpoint와 자격 증명을 재사용하지 않는다.

~~~bash
cd /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/terraform
terraform output -raw rds_primary_endpoint
terraform output -raw rds_replica_endpoint
kubectl create secret generic url-shortener-secret -n url-shortener --from-env-file="$APP_SECRET_FILE" --dry-run=client -o yaml | kubectl apply -f -
kubectl get secret url-shortener-secret -n url-shortener -o json | jq -r '.data | keys[]'
~~~

Redis PVC는 `Bound`, 앱 Secret의 키는 `DATABASE_URL`, `DATABASE_READ_URL`, `REDIS_URL`이어야 한다. 위 명령은 키 이름만 출력한다. Secret 존재와 RDS `available` 상태는 비밀번호 인증 성공의 증거가 아니므로 9절에서 앱의 실제 DB 경로를 반드시 확인한다.

## 7. 모니터링 Secret과 Prometheus

보호된 파일로 grafana-admin-secret, alertmanager-slack-secret, alertmanager-heartbeat-secret을 만든다. Grafana Secret에는 admin-user와 admin-password, 두 Alertmanager Secret에는 webhook-url 키가 있어야 한다. 각 파일은 의도하지 않은 끝 줄바꿈 없이 준비한다. Secret 값이나 생성 명령의 리터럴을 가이드에 넣지 않는다.

~~~bash
kubectl create secret generic grafana-admin-secret -n monitoring --from-file=admin-user="$GRAFANA_ADMIN_USER_FILE" --from-file=admin-password="$GRAFANA_ADMIN_PASSWORD_FILE" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic alertmanager-slack-secret -n monitoring --from-file=webhook-url="$SLACK_WEBHOOK_FILE" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic alertmanager-heartbeat-secret -n monitoring --from-file=webhook-url="$HEARTBEAT_WEBHOOK_FILE" --dry-run=client -o yaml | kubectl apply -f -
kubectl get secret grafana-admin-secret alertmanager-slack-secret alertmanager-heartbeat-secret -n monitoring
~~~

세 Secret이 존재하고 필요한 키가 있는지 **값을 출력하지 않고** 확인한다. 적용 전에 Argo CD가 읽을 `main`의 Prometheus values도 점검한다. 특히 `retentionSize`는 `40GiB`처럼 단위 끝에 `B`가 필요하며 `40Gi`는 Prometheus CR에서 거부된다. 기존 수동 Helm release가 있으면 Argo CD와 이중 관리하지 않는다.

~~~bash
git fetch origin main
git show origin/main:cluster-addons/prometheus/values.yaml | rg 'retentionSize: "40GiB"'
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/prometheus/application.yaml
kubectl wait -n argocd application/kube-prometheus-stack --for=jsonpath='{.status.sync.status}'=Synced --timeout=600s
kubectl wait -n argocd application/kube-prometheus-stack --for=jsonpath='{.status.health.status}'=Healthy --timeout=600s
kubectl get prometheus,alertmanager -n monitoring
kubectl wait -n monitoring pod/prometheus-kube-prometheus-stack-prometheus-0 --for=condition=Ready --timeout=600s
kubectl wait -n monitoring pod/alertmanager-kube-prometheus-stack-alertmanager-0 --for=condition=Ready --timeout=600s
kubectl rollout status -n monitoring deployment/kube-prometheus-stack-grafana --timeout=600s
kubectl wait -n monitoring pvc --all --for=jsonpath='{.status.phase}'=Bound --timeout=600s
kubectl get pods,pvc -n monitoring
~~~

Argo CD Application은 외부 Helm chart 83.7.0과 `main`의 `cluster-addons/prometheus/values.yaml`을 조합한다. Prometheus CR 생성, Prometheus·Grafana·Alertmanager Pod `Ready`, 해당 PVC `Bound`까지 확인한 뒤 다음 단계로 간다. Secret과 Pod가 존재하는 것만으로 Slack/Watchdog의 실제 수신 성공을 단정하지 않는다.

## 8. cert-manager, Traefik, DNS, Argo CD HTTPS·CI 인증

### 8-1. cert-manager

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/cert-manager/application.yaml
kubectl wait -n argocd application/cert-manager --for=jsonpath='{.status.sync.status}'=Synced --timeout=300s
kubectl wait -n argocd application/cert-manager --for=jsonpath='{.status.health.status}'=Healthy --timeout=300s
kubectl wait -n cert-manager deployment --all --for=condition=Available --timeout=300s
kubectl -n cert-manager get deployments,pods
~~~

cert-manager Deployment·Pod가 모두 준비되지 않으면 Traefik·인증서 단계로 넘어가지 않는다.

### 8-2. Traefik과 NLB

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/traefik/application.yaml
kubectl wait -n argocd application/traefik --for=jsonpath='{.status.sync.status}'=Synced --timeout=300s
kubectl wait -n argocd application/traefik --for=jsonpath='{.status.health.status}'=Healthy --timeout=300s
kubectl rollout status -n traefik deployment/traefik --timeout=300s
kubectl -n traefik get pods,svc
kubectl get ingressclass traefik
~~~

`traefik` Service에 NLB 호스트명이 할당되고 AWS Load Balancer 상태·Target Group이 정상인지 확인한 뒤에만 DNS를 갱신한다.

### 8-3. Route53 Alias와 등록기관 NS

아래는 **설치 시에만** 사용하는 `UPSERT` 절차다. `종료`·리소스 정리에서는 Route53 레코드와 Hosted Zone을 절대 삭제하거나 변경하지 않는다. 먼저 실제 공개 Hosted Zone ID와 현재 Traefik NLB의 Canonical Hosted Zone ID를 조회한다. 이름이 같은 사설 Zone이나 과거 nginx NLB를 선택하지 않는다.

~~~bash
TRAEFIK_NLB_DNS=$(kubectl -n traefik get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
test -n "$TRAEFIK_NLB_DNS"
aws elbv2 describe-load-balancers --region ap-northeast-2 --query "LoadBalancers[?DNSName=='${TRAEFIK_NLB_DNS}'].[LoadBalancerArn,CanonicalHostedZoneId,Type,State.Code]" --output table
aws route53 list-hosted-zones-by-name --dns-name bidservice.store --query 'HostedZones[].[Id,Name,Config.PrivateZone]' --output table
~~~

조회 결과에서 공개 Zone의 ID를 `HOSTED_ZONE_ID`, 현재 `active` 상태인 Traefik `network` NLB의 Canonical Hosted Zone ID를 `NLB_ZONE_ID`로 설정한다. 기존 `api`·`argocd` A 레코드를 조회해 다른 서비스가 사용 중이거나 예상 밖 타입·대상이면 중단한다. 두 변수에 검증한 ID를 지정한 뒤에만 다음을 실행한다.

~~~bash
: "${HOSTED_ZONE_ID:?검증한 공개 Hosted Zone ID를 설정하세요}"
: "${NLB_ZONE_ID:?검증한 현재 Traefik NLB Zone ID를 설정하세요}"
aws route53 list-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" --query "ResourceRecordSets[?Name=='api.bidservice.store.' || Name=='argocd.bidservice.store.']" --output json
~~~

조회 결과의 두 이름·타입·대상이 의도한 운영 레코드인지 확인한다. 예상 밖 레코드가 있으면 **여기서 중단**하고 UPSERT를 실행하지 않는다. 확인이 끝난 경우에만 아래 변경 블록을 별도로 실행한다.

~~~bash
CHANGE_ID=$(aws route53 change-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" --change-batch "$(jq -nc --arg dns "$TRAEFIK_NLB_DNS" --arg zone "$NLB_ZONE_ID" '{Changes: (["api.bidservice.store.", "argocd.bidservice.store."] | map({Action:"UPSERT",ResourceRecordSet:{Name:.,Type:"A",AliasTarget:{HostedZoneId:$zone,DNSName:$dns,EvaluateTargetHealth:false}}}))}')" --query 'ChangeInfo.Id' --output text)
aws route53 wait resource-record-sets-changed --id "$CHANGE_ID"
aws route53 get-hosted-zone --id "$HOSTED_ZONE_ID" --query 'DelegationSet.NameServers' --output text
dig @1.1.1.1 bidservice.store NS +short
dig @1.1.1.1 api.bidservice.store A +short
dig @1.1.1.1 argocd.bidservice.store A +short
~~~

Route53의 NS 목록과 등록기관(가비아)의 NS 설정 및 공개 DNS 응답을 대조한다. NS 불일치나 Alias 미전파 시 다음 단계로 넘어가지 않는다. 기존 Alias를 삭제하지 않고 새 NLB 대상으로 갱신하는 것이 설치 동작이다.

### 8-4. Argo CD HTTPS와 GitHub Actions 인증

Argo CD 서버는 4절에서 내부 HTTP `ClusterIP`로 설치했다. Traefik에서 외부 TLS를 종료한다. 앱 라우팅 Application을 적용하기 전이므로 동일 디렉터리의 `ClusterIssuer`를 먼저 단독 적용·검증한다. 9절에서 라우팅 Application이 이 선언을 이어서 관리한다. 아래 부트스트랩 Ingress는 클러스터 재생성 때 재적용하며, 기존 공개 HTTP ALB Ingress는 적용하지 않는다.

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/traefik/clusterissuer-traefik.yaml
kubectl wait clusterissuer/letsencrypt-traefik --for=condition=Ready --timeout=300s
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/argocd/argocd-traefik-https-ingress.yaml
kubectl wait -n argocd certificate/argocd-traefik-tls --for=condition=Ready --timeout=10m
curl -fsS -o /dev/null -w 'HTTP %{http_code}, TLS %{ssl_verify_result}\n' https://argocd.bidservice.store/
kubectl get ingress -A
~~~

HTTP `200`·TLS 검증 `0`을 확인한다. 이 Ingress는 인터넷에서 HTTPS로 접속할 수 있으므로 관리 UI 접근 제한은 별도 설계 대상이다. 기존 공개 HTTP ALB Ingress가 남았다면 해당 리소스의 소유권을 확인하고 제거한다.

Argo CD 관리자 자격과 GitHub CLI 인증을 준비한다. 기존 `ci-read` 역할이 있으면 새로 만들지 않고 정책을 확인한다. 읽기 권한만 가진 30일 토큰을 **출력·파일 저장 없이** Actions Secret으로 전달한다. 토큰의 값은 다시 조회할 수 없으며 실제 Actions 주입은 10절의 새 실행으로 검증한다.

~~~bash
set -o pipefail
argocd login argocd.bidservice.store --grpc-web
argocd proj role create default ci-read
argocd proj role add-policy default ci-read --resource applications --action get --object url-shortener --permission allow
argocd proj role create-token default ci-read --expires-in 720h --token-only | gh secret set ARGOCD_AUTH_TOKEN --repo f-lab-edu/F-Lab-DevOps
gh variable set ARGOCD_SERVER --body https://argocd.bidservice.store --repo f-lab-edu/F-Lab-DevOps
gh secret list --repo f-lab-edu/F-Lab-DevOps
gh variable list --repo f-lab-edu/F-Lab-DevOps
~~~

`ARGOCD_AUTH_TOKEN`과 `ARGOCD_SERVER`의 등록 여부, `ci-read`의 `default/url-shortener` 조회 정책, 토큰 만료일을 확인한다. HTTPS·인증 준비가 끝나기 전에는 앱 코드 변경을 `main`에 push/merge하지 않는다. 현재 Traefik 라우팅에는 IP 허용 목록이 있으므로 접속자 IP가 `url-shortener/k8s/traefik/middleware-ipallowlist.yaml`에 없으면 API HTTPS 요청은 `403`이다. 허용 IP 변경은 `main`에 반영된 뒤 Argo CD가 동기화해야 한다.

## 9. ECR 첫 이미지, 애플리케이션, Traefik 라우팅

두 Application은 `main`을 추적한다. **앱 Application 적용 전에** 현재 `main`의 `values.prod.yaml` 태그가 ECR에 있는지 확인한다. 없는 경우 `ImagePullBackOff`를 정상적인 중간 상태로 취급하지 않는다. `aws ecr describe-images`가 `ImageNotFoundException` 이외의 오류를 내면 권한·리전·저장소부터 조사한다.

~~~bash
cd /Users/jounghyeon/dev/Url-Shortener-EKS-Platform
git fetch origin main
IMAGE_TAG=$(git show origin/main:url-shortener/url-shortener-chart/values.prod.yaml | awk '$1 == "tag:" {print $2; exit}')
test -n "$IMAGE_TAG"
aws ecr describe-images --repository-name urlshortener --region ap-northeast-2 --image-ids "imageTag=$IMAGE_TAG" --query 'imageDetails[0].imageDigest' --output text
~~~

현재 태그가 ECR에 없다면, **일회성 빌드 전용 부트스트랩**으로 태그가 가리키는 Git 커밋을 정확히 빌드한다. 일반 `ci-cd.yml`을 재실행하면 Argo CD 앱이 없거나 변경 불가 ECR 태그가 이미 있을 때 실패할 수 있으므로 부트스트랩 대신 사용하지 않는다. 아래 명령은 Docker Buildx와 ECR push 권한이 있는 운영자만 실행한다. 각 줄이 실패하면 즉시 중단하고, 성공 전에는 앱 Application을 만들지 않는다.

~~~bash
BOOTSTRAP_SHORT_SHA=${IMAGE_TAG#sha-}
test "${#BOOTSTRAP_SHORT_SHA}" -eq 7
BOOTSTRAP_COMMIT=$(git rev-parse --verify "${BOOTSTRAP_SHORT_SHA}^{commit}")
test "$(printf '%.7s' "$BOOTSTRAP_COMMIT")" = "$BOOTSTRAP_SHORT_SHA"
git merge-base --is-ancestor "$BOOTSTRAP_COMMIT" origin/main
BOOTSTRAP_ROOT=$(mktemp -d /private/tmp/urlshortener-ecr-bootstrap.XXXXXX)
git worktree add --detach "$BOOTSTRAP_ROOT/source" "$BOOTSTRAP_COMMIT"
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 716174522908.dkr.ecr.ap-northeast-2.amazonaws.com
docker buildx build --platform linux/amd64 --tag "716174522908.dkr.ecr.ap-northeast-2.amazonaws.com/urlshortener:$IMAGE_TAG" --push "$BOOTSTRAP_ROOT/source/url-shortener"
aws ecr describe-images --repository-name urlshortener --region ap-northeast-2 --image-ids "imageTag=$IMAGE_TAG" --query 'imageDetails[0].imageDigest' --output text
git worktree remove "$BOOTSTRAP_ROOT/source"
rmdir "$BOOTSTRAP_ROOT"
~~~

이 경로는 **태그가 없는 경우에만** 사용한다. ECR 태그가 이미 있으면 재푸시하지 않는다. 짧은 SHA가 현재 Git에서 유일한 커밋으로 해석되지 않거나 빌드 입력이 확인되지 않으면 중단한다. 부트스트랩 이미지에는 CI 검증 완료를 뜻하는 `release-` 태그를 붙이지 않는다.

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/argocd/application.yaml
kubectl wait -n argocd application/url-shortener --for=jsonpath='{.status.sync.status}'=Synced --timeout=600s
kubectl wait -n argocd application/url-shortener --for=jsonpath='{.status.health.status}'=Healthy --timeout=600s
kubectl rollout status -n url-shortener deployment/url-shortener-api --timeout=600s
kubectl -n url-shortener get pods,deploy,hpa,networkpolicy
kubectl wait -n argocd application/url-shortener --for=jsonpath='{.status.operationState.phase}'=Succeeded --timeout=600s
~~~

Application `Healthy`, API Pod `Ready`, PostSync `Succeeded`를 확인한다. 실제 Primary·Replica 비밀번호 인증과 역할도 **Secret 값을 출력하지 않고** 앱 Pod에서 검증한다. Primary의 `pg_is_in_recovery()`는 `False`, Replica는 `True`여야 한다. 하나라도 실패하면 다음 라우팅 단계로 넘어가지 않고 Terraform 입력·RDS 회전 버전·앱 Secret을 대조한다. DB 비밀번호 변경을 반영할 때는 Helm `secretRevision`도 올려 GitOps로 새 Pod를 만든다.

~~~bash
kubectl -n url-shortener exec deployment/url-shortener-api -- python -c 'from sqlalchemy import text; from app.core.database import write_engine, read_engine; print("primary_recovery=", write_engine.connect().scalar(text("select pg_is_in_recovery()"))); print("replica_recovery=", read_engine.connect().scalar(text("select pg_is_in_recovery()")))'
~~~

DB 경로가 정상일 때만 별도 Traefik 라우팅 Application을 적용한다. 이 Application은 `url-shortener/k8s/traefik`의 ClusterIssuer, Middleware, HTTPS·HTTP redirect Ingress와 redirect Service를 관리한다. 과거 `traefik-canary`나 nginx Ingress는 적용하지 않는다.

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/argocd/application-traefik-routing.yaml
kubectl wait -n argocd application/url-shortener-traefik-routing --for=jsonpath='{.status.sync.status}'=Synced --timeout=600s
kubectl wait -n argocd application/url-shortener-traefik-routing --for=jsonpath='{.status.health.status}'=Healthy --timeout=600s
kubectl get clusterissuer letsencrypt-traefik
kubectl get ingress,middleware,certificate -n url-shortener
kubectl wait -n url-shortener certificate/url-shortener-tls --for=condition=Ready --timeout=10m
~~~

허용된 접속자 IP에서 `https://api.bidservice.store/readyz`와 `/items` 응답을 확인한다. IP 허용 목록 밖의 `403`은 예상된 차단이며 외부 기능 검증 통과로 기록하지 않는다.

## 10. 다음 앱 변경의 자동 CI/CD 검증

8~9절에서 Argo CD HTTPS 인증서, Actions 변수·Secret, ECR의 현재 이미지, 앱·라우팅 Application의 `Synced/Healthy`, PostSync 성공, Primary·Replica 접속, API HTTPS 응답이 모두 확인된 뒤에만 새 앱 코드 변경을 `main`에 병합한다. 설치용 구성 수정만 있었다면 앱 배포 워크플로는 시작되지 않는다. 단지 CI를 실행하려고 의미 없는 앱 코드 변경을 만들 필요는 없다.

1. 실제 앱 변경 PR에 `url-shortener/app/**`, `Dockerfile`, `requirements.txt`, `requirements.lock` 중 워크플로 paths에 해당하는 변경이 있는지 확인한다.
2. `ARGOCD_SERVER`·`ARGOCD_AUTH_TOKEN` 등록, 토큰 만료·조회 정책, Actions `contents: write`와 `main` push 정책을 확인한다.
3. 승인된 앱 변경을 `main`에 병합한다. 이 `main` push로 `.github/workflows/ci-cd.yml`이 **자동** 시작된다. `develop` 병합이나 paths 밖의 변경은 배포 실행을 만들지 않는다.
4. 해당 실행에서 린트, ECR 이미지 push, `values.prod.yaml` 태그 커밋·push, Argo CD 동기화와 클러스터 내부 PostSync Job 완료, 검증 후 `release-` 태그 digest를 확인한다. Argo CD 토큰의 실제 Actions 주입도 이 새 실행으로 검증된다.
5. 실패하면 그 단계에서 중단한다. 이전 이미지가 없는 첫 실패는 자동 롤백 대상이 아니며, 새 원하는 상태가 실제로 정상인지 확인하기 전에는 배포 완료로 표시하지 않는다. 이전 이미지가 있을 때만 워크플로의 GitOps 롤백과 복구 상태를 확인한다.

이미 앱 변경이 `main`에 반영됐다면 새 병합을 반복하지 않는다. 9절의 ECR·앱·PostSync·DB·API 검증으로 **현재 배포 상태**를 확인한다. 과거 CI 실행을 재실행하면 변경 불가 태그와 충돌할 수 있다. 현재 상태 검증과 새 자동 CI 실행의 성공은 구분해 기록한다. Actions의 `GITHUB_TOKEN`이 만든 values 태그 커밋은 배포 실행을 다시 시작하지 않는다.

~~~bash
gh run list --repo f-lab-edu/F-Lab-DevOps --workflow ci-cd.yml --limit 5
kubectl get application url-shortener url-shortener-traefik-routing -n argocd
kubectl get pods,deploy,hpa,networkpolicy -n url-shortener
kubectl get certificate url-shortener-tls -n url-shortener
kubectl get application url-shortener -n argocd -o jsonpath='{.status.operationState.phase}{"\n"}'
curl -fsS https://api.bidservice.store/readyz
curl -fsS https://api.bidservice.store/items
~~~

외부 `curl`은 호출자 IP가 허용된 경우에만 통과한다. PostSync Job은 클러스터 내부 Service를 호출하므로 외부 IP 제한과 독립적으로 앱·DB 읽기 경로를 검사한다.

## 11. Karpenter

Karpenter를 설치할 때는 현재 `terraform output`의 `karpenter_node_role_arn`으로 노드 IAM 인증을 확인한다. 현재 Terraform은 EKS `API_AND_CONFIG_MAP` 인증 모드를 사용한다. 해당 역할의 access entry 또는 `aws-auth` 매핑이 이미 있다면 중복 생성하지 않는다.

~~~bash
cd /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/terraform
terraform output -raw karpenter_node_role_arn
aws eks list-access-entries --cluster-name urlshortener --region ap-northeast-2
eksctl get iamidentitymapping --cluster urlshortener --region ap-northeast-2
~~~

두 곳 모두 노드 역할 ARN이 없을 때만 다음 매핑을 만든다.

~~~bash
eksctl create iamidentitymapping --cluster urlshortener --region ap-northeast-2 --arn "$(terraform output -raw karpenter_node_role_arn)" --group system:bootstrappers,system:nodes --username 'system:node:{{EC2PrivateDNSName}}' --no-duplicate-arns
~~~

Karpenter 적용 전에는 기존 노드의 여유 CPU·메모리, 이미 Pending인 Pod, Controller 리소스 요청을 확인한다. 현재 Application 선언은 Controller Pod당 `1 CPU / 1Gi`를 요청하고 replica가 2개면 합계 `2 CPU / 2Gi`의 여유가 필요하다. 수용 여력이 없으면 **적용을 멈추고** 관리형 Node Group 용량이나 배치 시간을 먼저 조정한다. 실제 재설치에서는 여유 부족으로 기존 API·Prometheus Pod가 새 Spot 노드 준비 전 잠시 밀려난 적이 있다.

~~~bash
kubectl top nodes
kubectl describe nodes
kubectl get pods -A -o wide
rg -n 'replicas:|requests:|cpu:|memory:' /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/karpenter/application.yaml
~~~

여유가 확인되면 Karpenter Application을 적용하고 `Synced/Healthy`, Controller `Available`을 각각 확인한다. 그다음 EC2NodeClass의 `Ready`를 확인한 뒤 NodePool을 적용한다. 어느 단계든 시간 초과·불일치가 있으면 다음 리소스를 만들지 않는다.

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/karpenter/application.yaml
kubectl wait -n argocd application/karpenter --for=jsonpath='{.status.sync.status}'=Synced --timeout=600s
kubectl wait -n argocd application/karpenter --for=jsonpath='{.status.health.status}'=Healthy --timeout=600s
kubectl wait -n karpenter deployment/karpenter --for=condition=Available --timeout=10m
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/karpenter/ec2nodeclass.yaml
kubectl wait ec2nodeclass/default --for=condition=Ready --timeout=10m
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/karpenter/nodepool.yaml
kubectl wait nodepool/default --for=condition=Ready --timeout=10m
kubectl get ec2nodeclass,nodepool,nodeclaim,nodes
kubectl -n url-shortener get pods,deploy
kubectl -n monitoring get pods
~~~

NodeClaim이 생겼다면 해당 노드가 `Ready`가 될 때까지 기다리고, API·Prometheus Pod가 다시 정상인지 확인한다. NodeClaim이 없다면 강제로 만들 필요는 없다.

과거 문서의 Pod 이름과 노드 이름은 재사용하지 않는다. 현재 저장소에는 Loki/로그 수집기를 배포하는 선언이 없으므로, 로그 수집 설치는 별도의 설계와 검증 대상으로 기록한다. 예전 Promtail 설치 명령을 그대로 실행하지 않는다.

## 12. 미뤄 둔 개선 항목 검증

설치 성공과 개선 검증은 별도로 기록한다.

| 번호 | 확인할 결과 |
| --- | --- |
| 1, 6 | Prometheus Application의 Git values 반영, Prometheus·Alertmanager PVC와 재시작 후 데이터 유지 |
| 2, 33 | Helm 패키지·Docker 컨텍스트의 Secret 제외, 의존성 PR의 Terraform·Python·Docker·Helm 검사 |
| 3, 4 | HPA 확장 중 Argo CD 재동기화 후 replica 수, 최대 Pod 수에서 RDS 연결 수 |
| 5 | 쓰기 직후 Primary 조회 창과 세대별 캐시 키, Replica 지연 중 삭제 데이터 재삽입 방지 |
| 8, 21 | main 이외 OIDC 역할 거부, 허용·비허용 Pod 연결로 NetworkPolicy 실제 집행 확인 |
| 17, 19 | 애플리케이션 범위 알림식, 0/없음 지표 처리, 외부 Watchdog 수신 |
| 28, 30 | PostSync 성공·실패, 이전 이미지가 있는 경우 GitOps 롤백, ECR 현재·이전 이미지 pull과 release 태그 digest |
| 29, 32 | RDS Multi-AZ 장애조치, 격리 스냅샷 복원, 비밀번호 회전 버전·Secret 갱신·secretRevision 롤아웃 |
| 31 | S3 state 객체·버전·잠금, 다른 작업 디렉터리에서 같은 state 확인 |
| 36 | Traefik으로 /items 경로가 rewrite 없이 전달되는지 확인 |

RDS/ECR 보존, state, 비밀번호 회전, 의존성 갱신의 자세한 절차는 `docs/runbooks/`의 30~33번 runbook을 따른다. 34·35·39번은 아직 완료 항목이 아니다.

## 13. 정리할 때

운영 RDS에는 deletion_protection이 있고 운영 ECR에는 강제 삭제 금지가 있다. Terraform destroy를 바로 실행하지 않는다. 정리 순서와 Argo CD HTTPS Ingress·인증서, Traefik NLB, Prometheus Application, PVC 처리 방법은 `docs/runbooks/infrastructure-cleanup.md`를 따른다. **`종료` 때 Route53 Hosted Zone과 모든 레코드는 삭제·변경하지 않는다.** DNS Alias 갱신은 다음 설치의 8절에서만 수행한다. RDS 삭제 보호 해제는 별도 Terraform apply로 진행하고 ECR 이미지와 최종 스냅샷의 보존 대상을 확인한다. `docs/runbooks/30-rds-ecr-retention.md`의 보존 정책을 적용한다.
