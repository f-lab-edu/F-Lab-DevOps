# ALB Controller IRSA — terraform-aws-modules/iam 서브모듈 사용
# attach_load_balancer_controller_policy = true 한 줄로 공식 전체 정책 자동 연결
# iam-role-for-service-accounts-eks 서브모듈을 사용하면 ALB Controller 전용 IAM 정책이 내장되어 있어 수백 줄의 정책 JSON을 직접 작성할 필요가 없음.

module "alb_controller_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${var.project_name}-alb-controller-role"

  # AWS Load Balancer Controller 전용 IAM 정책을 모듈이 자동으로 생성·연결
  attach_load_balancer_controller_policy = true

  # 어떤 ServiceAccount가 이 Role을 사용할 수 있는지 정의
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn # "이 클러스터에서만 사용 가능" 제한 = 이 EKS 클러스터에서 발급된 토큰만 신뢰
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"] # 설치한 namespace 와 동일해야 함!
    }
  }

  tags = {
    Name = "${var.project_name}-alb-controller-role"
  }
}
