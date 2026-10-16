output "ai_ops_ecr_repository_url" {
  description = "활성화한 독립 AI 이미지 저장소 주소"
  value       = try(aws_ecr_repository.ai_ops[0].repository_url, null)
}

output "ai_ops_image_role_arn" {
  description = "GitHub ai-ops-image 환경의 이미지 등록 전용 역할"
  value       = try(aws_iam_role.ai_ops_image[0].arn, null)
}
