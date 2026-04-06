# OIDC Provider
# EKS IRSA용 OIDC Provider 생성
# Pod → AWS 권한 연결할 때 필요

data "tls_certificate" "eks" {
  url = aws_eks_cluster.main.identity[0].oidc[0].issuer # EKS 클러스터가 가진 OIDC issuer URL
}

# 실제 AWS IAM의 OIDC Provider 리소스를 생성함
resource "aws_iam_openid_connect_provider" "eks" {
  client_id_list  = ["sts.amazonaws.com"] # 이 Provider 를 AWS STS 토큰 교환 용도로 쓰겠다는 의미
  thumbprint_list = [data.tls_certificate.eks.certificates[0].sha1_fingerprint] # OIDC issuer의 인증서 지문
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer
}
