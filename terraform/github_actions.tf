# GitHub OIDC Provider 등록 (AWS가 GitHub Actions 토큰을 신뢰하게 됨)
module "github_oidc_provider" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-oidc-provider"
  version = "~> 6.0"

  url = "https://token.actions.githubusercontent.com" # ← 추가 (thumbprint 자동 계산)

  tags = {
    Name = "${var.project_name}-github-oidc"
  }
}

# GitHub Actions가 assume할 IAM Role
module "github_actions_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role" # GitHub Actions용 IAM Role을 생성하는 모듈
  version = "~> 6.0"

  name            = "${var.project_name}-github-actions-role"
  use_name_prefix = false  # 타임스탬프 suffix 방지

  enable_github_oidc = true # ← 추가 (GitHub OIDC 신뢰 관계 자동 구성)

  # 보안: 특정 레포지토리의 main 브랜치만 허용
  # pull_request 이벤트까지 허용하려면 "repo:org/repo:*"으로 변경
  # TODO: dev, prod 환경 구분하기
  oidc_wildcard_subjects = [
    # "repo:f-lab-edu/Url-Shortener-EKS-Platform:ref:refs/heads/main",
    "repo:f-lab-edu/Url-Shortener-EKS-Platform:ref:refs/heads/feat/week6to7",
    "repo:f-lab-edu/Url-Shortener-EKS-Platform:pull_request"
  ]

  policies = {
    ECRPush = aws_iam_policy.github_actions_ecr.arn # 이 Role에 부여할 권한을 설정해야 함
  }

  tags = {
    Name = "${var.project_name}-github-actions-role"
  }
}

# ECR push 권한 (모듈이 기본 제공하지 않으므로 직접 정의)
resource "aws_iam_policy" "github_actions_ecr" {
  name        = "${var.project_name}-github-actions-ecr-policy"
  description = "GitHub Actions CI/CD Pipeline - ECR push 권한 설정"

  policy = jsonencode({
    Version = "2012-10-17" # IAM 정책 문법 버전 -> TODO: 현재 유일한 유효 버전이라는데 재점검 필요
    Statement = [
      # ECR login 시 발급되는 권한에 대한 설정
      {
        Sid      = "AllowECRAuth"
        Effect   = "Allow" # 아래 Action의 권한이 허용/거부에 대한 내용인지 결정
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*" # 권한 적용 대상 - ECR 인증은 레포지토리 단위가 아닌 계정 단위로 동작함!
      },
      # 실제 이미지 push 시 필요한 권한에 대해 명세
      {
        Sid    = "AllowECRPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", # 레이어 존재 여부 확인 (중복 업로드 방지)
          "ecr:GetDownloadUrlForLayer",      # 레이어 다운로드 URL 조회
          "ecr:BatchGetImage",               # 이미지 메타데이터 조회
          "ecr:PutImage",                    # 이미지 push
          "ecr:InitiateLayerUpload",         # 레이어 업로드 시작
          "ecr:UploadLayerPart",             # 레이어 분할 업로드
          "ecr:CompleteLayerUpload",         # 레이어 업로드 완료
          "ecr:DescribeRepositories",        # 레포지토리 정보 조회
          "ecr:ListImages",                  # 이미지 목록 조회
          "ecr:DescribeImages"               # 이미지 상세 정보 조회
        ]
        Resource = "arn:aws:ecr:${var.aws_region}:*:repository/${var.project_name}"
      }
    ]
  })
}
