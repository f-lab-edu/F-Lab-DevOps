# AI 저장소·등록 역할은 검토 뒤 명시적으로 활성화한다. 기존 앱 리소스는 변경하지 않는다.
resource "aws_ecr_repository" "ai_ops" {
  count                = var.enable_ai_ops_registry ? 1 : 0
  name                 = "${var.project_name}-ai-ops"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
  tags = {
    Name        = "${var.project_name}-ai-ops"
    Environment = var.environment
  }
}

resource "aws_ecr_lifecycle_policy" "ai_ops" {
  count      = var.enable_ai_ops_registry ? 1 : 0
  repository = aws_ecr_repository.ai_ops[0].name
  # 배포 digest와 롤백 후보가 사라지지 않도록 태그가 있는 이미지는 자동 삭제하지 않는다.
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "태그 없는 AI 이미지 1일 후 삭제"
      selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 1 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_iam_role" "ai_ops_image" {
  count                = var.enable_ai_ops_registry ? 1 : 0
  name                 = "${var.project_name}-ai-ops-image-role"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = module.github_oidc_provider.arn }
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        # 이 GitHub environment에는 main만 허용하는 배포 규칙·검토자를 별도로 설정해야 한다.
        "token.actions.githubusercontent.com:sub" = "repo:f-lab-edu/F-Lab-DevOps:environment:ai-ops-image"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "ai_ops_image" {
  count = var.enable_ai_ops_registry ? 1 : 0
  name  = "ai-ops-image-only"
  role  = aws_iam_role.ai_ops_image[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage",
          "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
          "ecr:DescribeImages", "ecr:DescribeRepositories"
        ]
        Resource = aws_ecr_repository.ai_ops[0].arn
      }
    ]
  })
}
