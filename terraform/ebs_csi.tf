# EBS CSI Driver IRSA Role
# EBS CSI Controller가 EBS 볼륨을 생성/삭제할 수 있는 IAM 권한
# KS 1.32에서 kubernetes.io/aws-ebs (in-tree) → 이미 deprecated, 동작 안 함
# 해결책은 버전 변경이 아니라 EBS CSI 드라이버 addon 설치

## --- 수동 직접 설계 -----------------------------
# data "aws_iam_policy_document" "ebs_csi_assume_role" {
#   statement {
#     effect = "Allow"
#
#     principals {
#       type        = "Federated"
#       identifiers = [aws_iam_openid_connect_provider.eks.arn]
#     }
#
#     actions = ["sts:AssumeRoleWithWebIdentity"]
#
#     condition {
#       test     = "StringEquals"
#       variable = "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:sub"
#       values   = ["system:serviceaccount:kube-system:ebs-csi-controller-sa"]
#     }
#
#     condition {
#       test     = "StringEquals"
#       variable = "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:aud"
#       values   = ["sts.amazonaws.com"]
#     }
#   }
# }
#
# resource "aws_iam_role" "ebs_csi" {
#   name               = "${var.project_name}-ebs-csi-role"
#   assume_role_policy = data.aws_iam_policy_document.ebs_csi_assume_role.json
# }
#
# resource "aws_iam_role_policy_attachment" "ebs_csi" {
#   policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
#   role       = aws_iam_role.ebs_csi.name
# }
#
# # EBS CSI Driver EKS Addon
# resource "aws_eks_addon" "ebs_csi" {
#   cluster_name             = aws_eks_cluster.main.name
#   addon_name               = "aws-ebs-csi-driver"
#   service_account_role_arn = aws_iam_role.ebs_csi.arn
#
#   tags = {
#     Name = "${var.project_name}-ebs-csi"
#   }
# }

## --- 모듈 사용 ver.-------------------------
# EBS CSI Driver IRSA — terraform-aws-modules/iam 서브모듈 사용
module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts"
  version = "~> 6.0"

  name = "${var.project_name}-ebs-csi-role"

  # EBS CSI 전용 관리형 정책 자동 연결
  attach_ebs_csi_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}

# EKS Addon 등록 — 직접 리소스 유지 (모듈과 순환 의존성 방지)
resource "aws_eks_addon" "ebs_csi" {
  cluster_name             = module.eks.cluster_name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = module.ebs_csi_irsa.arn

  tags = {
    Name = "${var.project_name}-ebs-csi"
  }
}