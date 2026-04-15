# 어떤 클라우드 사용할지 정의
# AWS provider 및 region 설정 파일

## ---수동 리소스 작성-----------------------
# terraform {
#   required_version = ">= 1.5.0" # terraform cli 버전은 최소 버전만 지정하면 됨.
#
#   required_providers {
#     aws = { # provider를 AWS로 지정
#       source = "hashicorp/aws"
#       version = "~> 5.0" # 5.x 버전만 허용(고정) why?: 다른 환경에서 실행할 때 provider 버전이 달라저 발생하는 문제 방지
#     }
#     tls = {
#       source  = "hashicorp/tls"
#       version = "~> 4.0"
#     }
#   }
# }
#
# provider "aws" {
#   # AWS에 실제로 어떻게 연결한건지 정의
#   region = var.aws_region # 같은 terraform 코드로 다른 리전에 쉽게 배포 가능 목적(dev, prod 구분 목적도 있음)
# }

## --- 모듈 사용 ver.-------------------------
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # tls provider 제거 — EKS 모듈이 OIDC 처리, tls 직접 사용 불필요
    # AWS IAM에 OIDC Provider를 등록할 때 OIDC 서버 인증서의 지문(thumbprint) 을 제출해야 함
    # tls provider는 그 지문을 자동으로 가져오는 도구로 사용했음.
    # 하지만, 모듈 사용 시, 이 기능이 내장되어 있어 별도의 선언이 불필요 해짐. 그래서 삭제
  }
}

provider "aws" {
  region = var.aws_region
}