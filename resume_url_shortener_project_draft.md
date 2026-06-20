# Url-Shortener-EKS-Platform Resume Draft

## 작성 기준

- 기존 이력서 형식: Summary, Education, Experience, Skills, Talent의 2컬럼형 구조
- 기존 Experience 문체: 프로젝트 설명 1문장 + 성과/구현 중심 bullet 5~6개
- 이번 프로젝트 포지셔닝: Backend 중심보다 DevOps / Cloud Infrastructure / Platform Engineering 역량 강조
- 민감 정보: AWS Account ID, 실제 RDS Endpoint, Secret 값, Webhook URL 등은 이력서 본문에서 제외

---

## Header

**박종현**  
DevOps Engineer  
Seoul, South Korea  
jounghyeon123@gmail.com  
https://github.com/joungGo  

---

## Summary

AWS EKS 기반 애플리케이션 운영 환경을 Terraform, Kubernetes, Helm, ArgoCD로 구성하며 인프라 자동화와 GitOps 배포 흐름을 실습했습니다. FastAPI 애플리케이션을 ECR, EKS, RDS, Redis, Prometheus/Grafana, Loki, Karpenter와 연동해 배포, 관측, 알림, 오토스케일링, 장애 대응까지 이어지는 운영 관점의 전체 흐름을 구축한 경험이 있습니다.

### English Version

Built an AWS EKS-based application platform using Terraform, Kubernetes, Helm, and ArgoCD. Designed an end-to-end DevOps workflow covering container image delivery, GitOps deployment, RDS primary/replica connectivity, Redis caching, Prometheus/Grafana observability, Loki log analysis, Karpenter-based node scaling, and operational runbooks.

---

## Experience

### Url-Shortener-EKS-Platform

**DevOps / Backend Infrastructure Project**  
**Personal Project**  
**2026.04 - 2026.05**

FastAPI 기반 URL Shortener API를 AWS EKS 위에 배포하고, Terraform IaC, GitHub Actions, ArgoCD GitOps, RDS, Redis, Prometheus/Grafana, Loki, Karpenter를 연동하여 클라우드 네이티브 운영 환경을 구축한 프로젝트

- Terraform module 기반으로 VPC, public/private subnet, NAT Gateway, EKS, ECR, RDS Primary/Read Replica, IRSA Role, Karpenter Role, GitHub Actions OIDC Role을 코드화
- GitHub Actions에서 AWS OIDC 인증을 사용해 Access Key 없이 ECR 이미지 빌드/푸시 후 Helm `values.prod.yaml`의 이미지 태그를 자동 갱신하는 CI/CD 파이프라인 구성
- ArgoCD Application과 Helm Chart를 이용해 애플리케이션, Metrics Server, ALB Controller, Redis, Prometheus/Grafana, Karpenter 등 클러스터 리소스를 GitOps 방식으로 배포 및 동기화
- FastAPI 애플리케이션에 SQLAlchemy 기반 Write/Read DB 세션을 분리하여 RDS Primary에는 쓰기, Read Replica에는 조회 트래픽이 연결되도록 구성하고 `/items/_db` 진단 엔드포인트로 검증
- Redis Cache-Aside 패턴을 적용해 단건/목록 조회 캐시, TTL, 캐시 무효화, Redis 장애 시 DB 직접 조회로 전환되는 graceful degradation 구조 구현
- Prometheus `/metrics`, ServiceMonitor, PrometheusRule을 구성하여 HTTP 5xx 비율, 캐시 히트율, DB P99 latency 지표를 수집하고 Alertmanager Slack 알림 경로 설계
- Grafana, Loki/Grafana Alloy 기반으로 메트릭과 로그를 함께 확인하는 장애 분석 흐름을 문서화하고 Alert 발생 시점과 로그 타임라인을 대조하는 운영 런북 작성
- HPA와 Karpenter NodePool/EC2NodeClass를 구성하여 CPU 기반 Pod scale-out과 Spot/On-demand 노드 프로비저닝 구조를 설계하고, k6 기반 100 -> 1,000 -> 5,000 RPS 부하 테스트 시나리오 작성

### 압축형 버전

FastAPI 기반 URL Shortener API를 AWS EKS에 배포하고 Terraform, Helm, ArgoCD, GitHub Actions, RDS, Redis, Prometheus/Grafana, Loki, Karpenter를 연동해 GitOps 기반 운영 환경을 구축한 프로젝트

- Terraform으로 VPC, EKS, ECR, RDS Primary/Read Replica, IRSA, Karpenter, GitHub Actions OIDC Role을 IaC로 구성
- GitHub Actions OIDC 인증, Docker Buildx, ECR Push, Helm values 이미지 태그 자동 갱신 기반 CI/CD 파이프라인 구축
- ArgoCD Application과 Helm Chart로 애플리케이션 및 클러스터 애드온을 GitOps 방식으로 배포하고 self-heal/prune 정책 적용
- RDS Primary/Replica write-read 분리, Redis Cache-Aside, cache hit/miss metric, 장애 시 graceful degradation 구조 구현
- Prometheus/Grafana/Alertmanager/Loki 기반 관측 환경과 Slack 알림, 장애 분석 런북, k6 부하 테스트 및 HPA/Karpenter 검증 시나리오 작성

---

## Skills

### Programming Skills

Python, FastAPI, SQLAlchemy, Pydantic

### Databases & Cloud Services

AWS, EKS, ECR, RDS PostgreSQL, RDS Read Replica, Redis, VPC, IAM, OIDC, IRSA

### Infrastructure & Tools

Terraform, Docker, Kubernetes, Helm, ArgoCD, GitHub Actions, Karpenter, AWS Load Balancer Controller, Metrics Server, cert-manager

### Observability & Operations

Prometheus, Grafana, Alertmanager, Loki, Grafana Alloy, ServiceMonitor, PrometheusRule, k6, HPA, Runbook

---

## Talent

### Operational Thinking

장애가 발생한 뒤의 복구만이 아니라, 메트릭/로그/알림/런북을 통해 문제를 빠르게 좁혀갈 수 있는 운영 흐름을 함께 설계합니다.

### Documentation

인프라 설치, 검증, 장애 주입, 정리 절차를 단계별 문서로 남겨 재현 가능한 실습과 운영 기준을 만드는 데 강점이 있습니다.

---

## 면접에서 강조할 포인트

- 단순히 EKS에 앱을 띄운 프로젝트가 아니라, 배포 자동화, GitOps, 관측성, 알림, 오토스케일링, 장애 대응까지 연결한 운영형 프로젝트임
- GitHub Actions에서 Access Key 대신 OIDC 기반 IAM Role Assume 방식을 사용한 점
- RDS Primary/Replica, Redis Cache-Aside, Prometheus metric, Loki log를 애플리케이션 코드와 인프라에 같이 녹인 점
- Karpenter와 HPA를 별도 키워드로 나열하는 데 그치지 않고, k6 부하 테스트와 운영 검증 흐름까지 설계한 점
- Secret을 Git에 직접 넣지 않고 Kubernetes Secret, IRSA, values 분리 전략을 고려한 점

