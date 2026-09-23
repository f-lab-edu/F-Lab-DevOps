# Terraform에서 사용할 변수 선언 파일

variable "aws_region" {
  description = "AWS 리전"
  type = string
  default = "ap-northeast-2"
}

variable "project_name" {
  description = "프로젝트 이름 - 리소스의 prefix로 사용"
  type = string
  default = "urlshortener"
}

# 실습 환경은 운영과 별도 state 및 project_name을 사용한다.
variable "environment" {
  description = "리소스 보존 정책을 적용할 환경 (production 또는 practice)"
  type        = string
  default     = "production"

  validation {
    condition     = contains(["production", "practice"], var.environment)
    error_message = "environment는 production 또는 practice여야 합니다."
  }
}

variable "vpc_cidr" {
  description = "VPC CIDR 블록"
  type = string
  default = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "사용할 가용 영역 목록"
  type = list(string)
  default = ["ap-northeast-2a", "ap-northeast-2c"]
}

variable "eks_cluster_version" {
  description = "EKS Kubernetes 버전"
  type = string
  default = "1.35"
}

variable "node_instance_type" {
  description = "EKS Worker Node EC2 인스턴스 타입"
  type = string
  default = "t3.large"
}

# terraform apply 시 생성되는 노드 수
variable "node_desired_size" {
  description = "NodeGroup 기본 노드 수"
  type = number
  default = 2
}

# 스케일 다운 시 보장되는 최소 노드 수
variable "node_min_size" {
  description = "NodeGroup 최소 노드 수"
  type = number
  default = 1
}

# 스케일 업 시 보장되는 최대 노드 수
variable "node_max_size" {
  description = "NodeGroup 최대 노드 수"
  type = number
  default = 4
}

variable "db_password" {
  description = "RDS master(user=postgres) password (manage_master_user_password=false 사용 시 필수)"
  type        = string
  sensitive   = true
}
