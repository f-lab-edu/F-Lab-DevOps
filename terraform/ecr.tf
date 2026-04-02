# terraform ecr.tf

resource "aws_ecr_repository" "app" {
  name = var.project_name

  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true # push 시 자동 취약점 스캔
  }

  encryption_configuration {
    encryption_type = "AES256" #
  }

  force_delete = false

  tags = {
    Name      = "${var.project_name}-ecr"
    Eviroment = "production"
  }
}

# Lifecycle Policy
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "최신 30개 이미지 유지"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["sha-"] # Git SHA 태그만 대상
          countType     = "imageCountMoreThan" # 초과하면 삭제 시작
          countNumber   = 30
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "untagged 이미지 1일 후 삭제"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed" # push된 시간 기준으로 시간 계산
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      }
    ]
  })
}
