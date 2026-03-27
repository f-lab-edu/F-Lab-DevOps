# 어떤 클라우드 사용할지 정의
# AWS provider 및 region 설정 파일

terraform {
  required_version = ">= 1.5.0" # terraform cli 버전은 최소 버전만 지정하면 됨.

  required_providers {
    aws = { # provider를 AWS로 지정
      source = "hashicorp/aws"
      version = "~> 5.0" # 5.x 버전만 허용(고정) why?: 다른 환경에서 실행할 때 provider 버전이 달라저 발생하는 문제 방지
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  # AWS에 실제로 어떻게 연결한건지 정의
  region = var.aws_region # 같은 terraform 코드로 다른 리전에 쉽게 배포 가능 목적(dev, prod 구분 목적도 있음)
}