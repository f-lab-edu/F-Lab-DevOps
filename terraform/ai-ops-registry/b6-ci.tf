# 실습 실행 역할은 이미지 등록 역할과 분리하며 클러스터 관리자 정책을 연결하지 않는다.
variable "enable_b6_lab_ci" {
  description = "S10 실습 전용 GitHub 역할과 EKS group 연결 활성화"
  type        = bool
  default     = false
  validation {
    condition     = !var.enable_b6_lab_ci || var.enable_ai_ops_registry
    error_message = "S10 CI는 AI 등록 기반과 함께 활성화해야 합니다."
  }
}

data "aws_eks_cluster" "b6" {
  count = var.enable_b6_lab_ci ? 1 : 0
  name  = "urlshortener"
}
resource "aws_iam_role" "b6_ci" {
  count = var.enable_b6_lab_ci ? 1 : 0
  name  = "${var.project_name}-b6-ci-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn },
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub" = "repo:f-lab-edu/F-Lab-DevOps:environment:ai-ops-lab-ci"
      } }
    }]
  })
  tags = { Environment = var.environment, Component = "b6-lab-ci" }
}
resource "aws_iam_role_policy" "b6_ci" {
  count = var.enable_b6_lab_ci ? 1 : 0
  name  = "b6-cluster-and-image-read"
  role  = aws_iam_role.b6_ci[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["eks:DescribeCluster"], Resource = data.aws_eks_cluster.b6[0].arn },
    { Effect = "Allow", Action = ["ecr:DescribeImages"], Resource = "arn:aws:ecr:${var.aws_region}:${var.expected_account_id}:repository/urlshortener" }
  ] })
}
resource "aws_eks_access_entry" "b6_ci" {
  count             = var.enable_b6_lab_ci ? 1 : 0
  cluster_name      = data.aws_eks_cluster.b6[0].name
  principal_arn     = aws_iam_role.b6_ci[0].arn
  kubernetes_groups = ["b6-s10-coordinator"]
  type              = "STANDARD"
}
output "b6_ci_role_arn" {
  description = "S10 실습 전용 OIDC 역할"
  value       = try(aws_iam_role.b6_ci[0].arn, null)
}
