# 프로젝트 전용 사용자는 AWS 리소스 조회·CLI 로그인과 EKS 클러스터 접근만 받는다.
resource "aws_iam_user" "project_operator" {
  count = var.enable_project_operator ? 1 : 0

  name          = "${var.project_name}-operator"
  force_destroy = false

  tags = {
    Project = var.project_name
    Purpose = "kubernetes-operator"
  }
}

resource "aws_iam_user_policy_attachment" "project_operator_local_login" {
  count = var.enable_project_operator ? 1 : 0

  user       = aws_iam_user.project_operator[0].name
  policy_arn = "arn:aws:iam::aws:policy/SignInLocalDevelopmentAccess"
}

resource "aws_iam_user_policy_attachment" "project_operator_change_password" {
  count = var.enable_project_operator ? 1 : 0

  user       = aws_iam_user.project_operator[0].name
  policy_arn = "arn:aws:iam::aws:policy/IAMUserChangePassword"
}

resource "aws_iam_user_policy" "project_operator_eks_describe" {
  count = var.enable_project_operator ? 1 : 0

  name = "${var.project_name}-eks-describe"
  user = aws_iam_user.project_operator[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["eks:DescribeCluster"]
      Resource = module.eks.cluster_arn
    }]
  })
}

resource "aws_eks_access_entry" "project_operator" {
  count = var.enable_project_operator ? 1 : 0

  cluster_name  = module.eks.cluster_name
  principal_arn = aws_iam_user.project_operator[0].arn
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "project_operator" {
  count = var.enable_project_operator ? 1 : 0

  cluster_name  = module.eks.cluster_name
  principal_arn = aws_eks_access_entry.project_operator[0].principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }
}
