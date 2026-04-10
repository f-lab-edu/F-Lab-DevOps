# Karpenter 인프라 — terraform-aws-modules/eks karpenter 서브모듈 사용
# Controller IRSA + Node Role + Instance Profile + SQS + EventBridge를 한 번에 생성

module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "~> 20.0"

  cluster_name      = module.eks.cluster_name

  enable_irsa = true
  irsa_oidc_provider_arn = module.eks.oidc_provider_arn

  # 노드 IAM Role 이름 (EC2NodeClass의 instanceProfile 필드에서 참조)
  node_iam_role_name            = "KarpenterNodeRole-${var.project_name}"
  node_iam_role_use_name_prefix = false

  # Instance Profile 자동 생성 (EC2에 IAM Role을 연결하려면 필수)
  create_instance_profile = true

  # Spot 인터럽션 처리용 SQS 큐 + EventBridge 규칙 3개 자동 생성
  # (Spot 인터럽션 경고 / EC2 상태 변경 / AWS Health 이벤트)
  enable_spot_termination = true
  queue_name              = "Karpenter-${var.project_name}"

  tags = {
    Name = "karpenter-${var.project_name}"
  }
}