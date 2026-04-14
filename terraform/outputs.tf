# 생성된 리소스 값을 출력하는 파일 - main.tf에서 값을 가져옴

# output "vpc_id" {
#   description = "생성된 VPC ID"
#   value = aws_vpc.main.id
# }
#
# output "eks_cluster_name" {
#   description = "EKS 클러스터 이름"
#   value = aws_eks_cluster.main.name
# }
#
# output "instance_type" {
#   value = aws_eks_node_group.main.instance_types
# }
#
# output "eks_cluster_endpoint" {
#   description = "EKS API Server 엔드포인트"
#   value = aws_eks_cluster.main.endpoint
# }
#
# output "eks_cluster_version" {
#   description = "EKS Kuberenetes 버전"
#   value = aws_eks_cluster.main.version
# }
#
# output "update_kubeconfig_command" {
#   description = "kubeconfig 업데이트 명령어"
#   value = "aws eks update-kubeconfig --region ${var.aws_region} --name ${aws_eks_cluster.main.name}"
# }
#
# output "ecr_repository_url" {
#   description = "ECR Repository URL (docker push에 사용 목적)"
#   value = aws_ecr_repository.app.repository_url
# }

# --- 모듈 사용 ver.-------------------------
output "vpc_id" {
  description = "생성된 VPC ID"
  value       = module.vpc.vpc_id
}

output "eks_cluster_name" {
  description = "EKS 클러스터 이름"
  value       = module.eks.cluster_name
}

output "instance_type" {
  description = "EKS Worker Node 인스턴스 타입"
  value       = var.node_instance_type
}

output "eks_cluster_endpoint" {
  description = "EKS API Server 엔드포인트"
  value       = module.eks.cluster_endpoint
}

output "eks_cluster_version" {
  description = "EKS Kubernetes 버전"
  value       = module.eks.cluster_version
}

output "update_kubeconfig_command" {
  description = "kubeconfig 업데이트 명령어"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

## --- github action 관련 output 추가 ------------------------
output "alb_controller_role_arn" {
  description = "ALB Controller IRSA Role ARN"
  value       = module.alb_controller_irsa.arn
}

output "github_actions_role_arn" {
  description = "GitHub Actions OIDC Role ARN"
  value       = module.github_actions_role.arn
}

output "karpenter_controller_role_arn" {
  description = "Karpenter Controller IRSA Role ARN"
  value       = module.karpenter.iam_role_arn
}

output "karpenter_node_role_arn" {
  description = "Karpenter Node IAM Role ARN"
  value       = module.karpenter.node_iam_role_arn
}

output "karpenter_queue_name" {
  description = "Karpenter Interruption SQS Queue 이름"
  value       = module.karpenter.queue_name
}

output "ecr_repository_url" {
  description = "ECR Repository URL (docker push에 사용 목적)"
  value       = module.ecr.repository_url
}