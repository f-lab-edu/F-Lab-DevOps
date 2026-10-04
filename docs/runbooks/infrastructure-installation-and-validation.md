# 인프라 재설치 및 DevOps/SRE 개선 검증 가이드

이 문서는 현재 저장소의 Terraform, Argo CD, Helm 구성을 기준으로 한다. 과거 설치 기록의 완료 표시는 이번 재설치 결과가 아니다. 명령 실행 전에 현재 AWS 계정, 대상 클러스터, Git 브랜치를 확인한다. 비밀번호·접속 URL·Webhook·토큰은 이 문서나 Git에 기록하지 않는다.

기준 저장소: /Users/jounghyeon/dev/Url-Shortener-EKS-Platform  
AWS 리전: ap-northeast-2  
운영 EKS 클러스터와 Helm release: urlshortener, url-shortener

## 0. 병합 전 확인

- 이번 개선 사항이 포함된 PR의 병합 대상은 main이다. 운영 CI/CD는 main push 중 url-shortener/app/**, Dockerfile, requirements.txt, requirements.lock 변경이 있을 때 실행된다. 이번 개선 PR에는 이 경로의 변경이 있다.
- Argo CD의 애플리케이션, Traefik 라우팅, Prometheus values는 main을 추적한다. 병합 전에는 이전 main의 내용이 반영될 수 있다. 새 코드의 검증 완료 판정은 병합 후에 한다.
- GitHub 저장소의 Actions 권한과 main 보호 규칙이 CI의 values.prod.yaml 커밋·push를 허용하는지 확인한다.
- GitHub Actions 변수 ARGOCD_SERVER와 비밀값 ARGOCD_AUTH_TOKEN을 준비한다. ARGOCD_SERVER는 아래에서 만드는 https://argocd.bidservice.store를 사용한다. 현재 argocd-ingress.yaml은 공개 HTTP ALB이므로 이 주소를 토큰 전달용으로 사용하지 않는다. HTTPS 인증서가 준비되지 않았다면 main 병합 전 배포 검증 단계는 실행할 준비가 되지 않은 것이다. Argo CD 토큰은 이 애플리케이션의 상태 조회에 필요한 권한만 부여하고 GitHub Actions Secret으로 저장한다.
- 배포 검증은 클러스터 내부 PostSync Job이 /readyz와 /items를 호출한다. GitHub 러너가 IP 제한된 api.bidservice.store에 접근할 필요가 없고 SMOKE_TEST_BASE_URL도 사용하지 않는다.
- 첫 배포에서 ECR에 이전 이미지가 없어도 빌드와 배포를 시도한다. 실패하면 존재하지 않는 태그로 롤백하지 않고 CI를 실패로 표시한다. 이전 이미지가 있을 때만 GitOps 롤백을 수행한다.

## 1. Terraform 원격 state와 인프라

Terraform CLI는 1.14.7을 사용한다. 운영과 실습은 서로 다른 작업 디렉터리, backend key, project_name을 쓴다. 아래는 운영 기준이다. 과거 로컬 terraform.tfvars에 db_password가 있으면 최신 입력인지 확인한다. tfvars는 TF_VAR 환경변수보다 우선하므로 오래된 값을 남긴 채 환경변수만 바꾸지 않는다.

~~~bash
cd /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/terraform
terraform version
aws sts get-caller-identity --query Account --output text
bash bootstrap-state-bucket.sh
terraform init -backend-config=backend.production.hcl -reconfigure -lockfile=readonly
terraform state list
~~~

AWS 계정은 716174522908이어야 한다. state에 예상 밖 리소스가 있으면 apply 전에 조사한다. `db_password`는 보호된 입력 경로에서 `TF_VAR_db_password`로 제공하고, 최초 `TF_VAR_db_password_rotation_version`은 `1`로 설정한다. plan과 apply에는 같은 값과 버전을 제공한다. 기존 환경의 회전 버전이 있다면 이전 기록을 확인하고 그보다 낮추지 않는다.

~~~bash
terraform plan
terraform apply
terraform output
~~~

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
kubectl create namespace url-shortener --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
~~~

Metrics Server Application이 Synced·Healthy가 된 뒤 `kubectl top nodes`를 실행한다. 현재 Metrics Server 설정에는 kubelet TLS 검증 우회가 남아 있다. `kubectl top` 성공만으로 39번 개선이 완료된 것은 아니다.

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
kubectl get pods,svc,pvc -n url-shortener
~~~

보호된 APP_SECRET_FILE에는 DATABASE_URL, DATABASE_READ_URL, REDIS_URL 세 키를 넣는다. DB host:port는 현재 terraform output의 rds_primary_endpoint와 rds_replica_endpoint를 사용한다. DB 비밀번호의 특수문자는 URL 인코딩한다. 문서에 있던 예전 endpoint와 자격 증명을 재사용하지 않는다.

~~~bash
cd /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/terraform
terraform output -raw rds_primary_endpoint
terraform output -raw rds_replica_endpoint
kubectl create secret generic url-shortener-secret -n url-shortener --from-env-file="$APP_SECRET_FILE" --dry-run=client -o yaml | kubectl apply -f -
kubectl get secret url-shortener-secret -n url-shortener
~~~

## 7. 모니터링 Secret과 Prometheus

보호된 파일로 grafana-admin-secret, alertmanager-slack-secret, alertmanager-heartbeat-secret을 만든다. Grafana Secret에는 admin-user와 admin-password, 두 Alertmanager Secret에는 webhook-url 키가 있어야 한다. 각 파일은 의도하지 않은 끝 줄바꿈 없이 준비한다. Secret 값이나 생성 명령의 리터럴을 가이드에 넣지 않는다.

~~~bash
kubectl create secret generic grafana-admin-secret -n monitoring --from-file=admin-user="$GRAFANA_ADMIN_USER_FILE" --from-file=admin-password="$GRAFANA_ADMIN_PASSWORD_FILE" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic alertmanager-slack-secret -n monitoring --from-file=webhook-url="$SLACK_WEBHOOK_FILE" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic alertmanager-heartbeat-secret -n monitoring --from-file=webhook-url="$HEARTBEAT_WEBHOOK_FILE" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/prometheus/application.yaml
kubectl get application kube-prometheus-stack -n argocd
kubectl get pods,pvc -n monitoring
~~~

Argo CD Application은 외부 Helm chart 83.7.0과 main의 cluster-addons/prometheus/values.yaml을 조합한다. 개선 PR 병합 전에는 이전 main values가 적용될 수 있으므로 PVC·Alertmanager·Watchdog 검증은 병합 후에 한다. 기존 Helm release가 있다면 Argo CD에 이중 관리시키기 전에 소유권 이관을 확인한다.

## 8. cert-manager, Traefik, DNS

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/cert-manager/application.yaml
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/traefik/application.yaml
kubectl get application cert-manager traefik -n argocd
kubectl get pods,svc -n traefik
kubectl get ingressclass
~~~

Traefik Service의 NLB 주소를 확인하고 Route53의 api.bidservice.store와 argocd.bidservice.store A Alias를 이 NLB로 연결한다. Hosted Zone NS는 현재 Route53 응답에서 다시 조회해 등록기관에 반영한다. 예전 nginx NLB나 과거 NS 목록을 복사하지 않는다.

~~~bash
kubectl get svc traefik -n traefik -o jsonpath='{.status.loadBalancer.ingress[0].hostname}{"\n"}'
aws route53 list-hosted-zones-by-name --dns-name bidservice.store
dig @1.1.1.1 api.bidservice.store A +short
dig @1.1.1.1 argocd.bidservice.store A +short
~~~

현재 Traefik 라우팅에는 IP 허용 목록이 있다. 실제 접속자 IP가 url-shortener/k8s/traefik/middleware-ipallowlist.yaml에 없는 경우 HTTPS 요청은 403이 된다. 허용 IP 변경은 main에 반영된 뒤 Argo CD가 동기화해야 한다.

## 9. 애플리케이션과 Traefik 라우팅

이 두 Application은 main을 추적한다. 로컬의 최신 Application 매니페스트를 적용하되, 개선 PR 병합 전에는 오래된 main 이미지와 chart 때문에 일시적으로 Degraded가 될 수 있다. ECR의 첫 이미지가 없는 경우도 마찬가지다. 병합 후 CI가 새 이미지를 올리고 values.prod.yaml 태그를 갱신하면 다시 확인한다.

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/argocd/application.yaml
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/argocd/application-traefik-routing.yaml
kubectl get application url-shortener url-shortener-traefik-routing -n argocd
kubectl get ingress -n url-shortener
~~~

Traefik 라우팅 Application은 url-shortener/k8s/traefik 디렉터리의 ClusterIssuer, Middleware, HTTPS·HTTP redirect Ingress와 redirect Service를 적용한다. 예전 traefik-canary 경로나 별도 nginx Ingress는 사용하지 않는다. DNS와 cert-manager가 준비된 뒤 인증서 상태를 확인한다.

~~~bash
kubectl get clusterissuer letsencrypt-traefik
kubectl get ingress,middleware,certificate -n url-shortener
kubectl wait certificate/url-shortener-tls -n url-shortener --for=condition=Ready --timeout=10m
~~~

Argo CD용 HTTPS Ingress도 적용한다. 이 파일은 부트스트랩 매니페스트이며, 기존 공개 HTTP ALB Ingress를 대신한다. 인증서가 Ready가 된 뒤에만 Actions 변수 ARGOCD_SERVER를 설정한다. 클러스터를 다시 만들 때 재적용한다.

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/argocd/argocd-traefik-https-ingress.yaml
kubectl wait certificate/argocd-traefik-tls -n argocd --for=condition=Ready --timeout=10m
curl -fsS -o /dev/null -w '%{http_code}\n' https://argocd.bidservice.store/
~~~

이 Ingress는 인터넷에서 HTTPS로 접속할 수 있다. 관리 UI 접근 제한은 별도로 설계해야 하며, 기존 HTTP ALB Ingress가 남아 있는 환경에서는 해당 Ingress도 제거한다.

## 10. main 병합과 첫 배포

1. PR의 변경 파일에 url-shortener/app/**, Dockerfile, requirements.txt, requirements.lock 중 하나가 포함됐는지 확인한다. 이번 개선 PR에는 포함돼 있다.
2. Argo CD의 안전한 접속 경로와 GitHub Actions의 ARGOCD_SERVER, ARGOCD_AUTH_TOKEN, main push 권한을 확인한다.
3. 사람이 개선 PR을 main에 병합한다. 이때 main push가 배포 워크플로를 시작한다. develop 병합만으로는 시작하지 않는다.
4. Actions에서 이미지 빌드·ECR push·values.prod.yaml 커밋, Argo CD 동기화, 클러스터 내부 PostSync Job 완료를 확인한다.
5. 첫 배포 전에 이전 ECR 이미지가 없었다면 실패 시 자동 롤백은 건너뛰며 원인을 수동으로 조사한다. 이전 이미지가 있으면 이전 태그로 GitOps 롤백하고 복구를 검증한다.
6. Actions의 GITHUB_TOKEN이 만든 태그 커밋은 새 배포 실행을 다시 만들지 않는다.

~~~bash
kubectl get application url-shortener -n argocd
kubectl get pods,deploy,hpa,networkpolicy -n url-shortener
kubectl get certificate url-shortener-tls -n url-shortener
curl -fsS https://api.bidservice.store/readyz
curl -fsS https://api.bidservice.store/items
~~~

외부 curl은 호출자 IP가 허용된 경우에만 통과한다. PostSync Job은 클러스터 내부 Service로 호출하므로 외부 IP 제한과 독립적으로 앱·DB 읽기 경로를 검사한다.

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

그 뒤 Karpenter Application이 정상 동기화되고 Deployment가 생성됐는지 확인한 다음 NodeClass와 NodePool을 적용한다.

~~~bash
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/cluster-addons/karpenter/application.yaml
kubectl get application karpenter -n argocd
kubectl wait -n karpenter --for=condition=Available deployment/karpenter --timeout=10m
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/karpenter/ec2nodeclass.yaml
kubectl apply -f /Users/jounghyeon/dev/Url-Shortener-EKS-Platform/url-shortener/k8s/karpenter/nodepool.yaml
kubectl get ec2nodeclass,nodepool
~~~

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

RDS/ ECR 보존, state, 비밀번호 회전, 의존성 갱신의 자세한 절차는 같은 디렉터리의 30~33번 runbook을 따른다. 34·35·39번은 아직 완료 항목이 아니다.

## 13. 정리할 때

운영 RDS에는 deletion_protection이 있고 운영 ECR에는 강제 삭제 금지가 있다. Terraform destroy를 바로 실행하지 않는다. 정리 순서와 Argo CD HTTPS Ingress·인증서, Traefik NLB, Prometheus Application, PVC, DNS 처리 방법은 `docs/runbooks/infrastructure-cleanup.md`를 따른다. RDS 삭제 보호 해제는 별도 Terraform apply로 진행하고 ECR 이미지와 최종 스냅샷의 보존 대상을 확인한다. `docs/runbooks/30-rds-ecr-retention.md`의 보존 정책을 적용한다.
