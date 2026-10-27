# 정상 관측 역할은 기존 S10 배포·원복 역할과 권한을 공유하지 않는다.
variable "enable_b6_normal_ci" {
  description = "격리 정상 관측 CI 전용 OIDC 역할과 EKS 그룹 연결 활성화"
  type        = bool
  default     = false
  validation {
    condition     = !var.enable_b6_normal_ci || var.enable_ai_ops_registry
    error_message = "정상 관측 CI는 AI 등록 기반과 함께 활성화해야 합니다."
  }
}

data "aws_eks_cluster" "normal_ci" {
  count = var.enable_b6_normal_ci ? 1 : 0
  name  = "urlshortener"
}

resource "aws_iam_role" "normal_ci" {
  count = var.enable_b6_normal_ci ? 1 : 0
  name  = "${var.project_name}-b6-normal-ci-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "repo:f-lab-edu/F-Lab-DevOps:environment:ai-ops-normal-ci"
      } }
    }]
  })
  tags = { Environment = var.environment, Component = "b6-normal-ci" }
}

resource "aws_iam_role_policy" "normal_ci" {
  count = var.enable_b6_normal_ci ? 1 : 0
  name  = "normal-lab-cluster-read"
  role  = aws_iam_role.normal_ci[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = data.aws_eks_cluster.normal_ci[0].arn
  }] })
}

resource "aws_eks_access_entry" "normal_ci" {
  count             = var.enable_b6_normal_ci ? 1 : 0
  cluster_name      = data.aws_eks_cluster.normal_ci[0].name
  principal_arn     = aws_iam_role.normal_ci[0].arn
  kubernetes_groups = ["b6-normal-coordinator"]
  type              = "STANDARD"
}

output "normal_ci_role_arn" {
  description = "격리 정상 관측 CI 전용 OIDC 역할"
  value       = try(aws_iam_role.normal_ci[0].arn, null)
}
