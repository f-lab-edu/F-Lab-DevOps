# Terraform에서 사용할 변수 선언 파일

variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "expected_account_id" {
  description = "Terraform 실행을 허용할 AWS 계정 ID"
  type        = string
  default     = "716174522908"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id는 숫자 12자리여야 합니다."
  }
}

variable "eks_admin_principal_arn" {
  description = "EKS 관리자 접근 항목에 등록할 같은 계정의 IAM 사용자 또는 역할 ARN"
  type        = string
  default     = "arn:aws:iam::716174522908:user/IAM-jounghyeon"

  validation {
    condition     = can(regex("^arn:aws:iam::${var.expected_account_id}:(user|role)/[A-Za-z0-9+=,.@_/-]+$", var.eks_admin_principal_arn))
    error_message = "eks_admin_principal_arn은 expected_account_id와 같은 계정의 IAM 사용자 또는 역할이어야 합니다."
  }
}

variable "enable_project_operator" {
  description = "새 계정에서 프로젝트 전용 IAM 사용자와 EKS 관리자 접근 항목 생성 여부"
  type        = bool
  default     = false
}

variable "project_name" {
  description = "프로젝트 이름 - 리소스의 prefix로 사용"
  type        = string
  default     = "urlshortener"
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
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "사용할 가용 영역 목록"
  type        = list(string)
  default     = ["ap-northeast-2a", "ap-northeast-2c"]
}

variable "eks_cluster_version" {
  description = "EKS Kubernetes 버전"
  type        = string
  default     = "1.35"
}

variable "node_instance_type" {
  description = "EKS Worker Node EC2 인스턴스 타입"
  type        = string
  default     = "t3.large"
}

# terraform apply 시 생성되는 노드 수
variable "node_desired_size" {
  description = "NodeGroup 기본 노드 수"
  type        = number
  default     = 3
}

# 스케일 다운 시 보장되는 최소 노드 수
variable "node_min_size" {
  description = "NodeGroup 최소 노드 수"
  type        = number
  default     = 1
}

# 스케일 업 시 보장되는 최대 노드 수
variable "node_max_size" {
  description = "NodeGroup 최대 노드 수"
  type        = number
  default     = 4
}

variable "db_password" {
  description = "신규 RDS 생성 또는 스냅샷 복원 후 회전에 필요한 master(user=postgres) 비밀번호"
  type        = string
  sensitive   = true
  ephemeral   = true
  default     = null

  validation {
    condition     = var.rds_restore_snapshot_identifier == null ? (var.db_password != null && var.db_password_rotation_version != null) : ((var.db_password == null) == (var.db_password_rotation_version == null))
    error_message = "신규 DB 생성에는 비밀번호와 회전 버전이 필요하고 스냅샷 복원 후 회전에는 두 값을 함께 지정해야 합니다."
  }
}

variable "db_password_rotation_version" {
  description = "신규 RDS 생성 또는 스냅샷 복원 후 회전에 필요한 master 비밀번호 회전 버전"
  type        = number
  default     = null

  validation {
    condition     = var.db_password_rotation_version == null || (var.db_password_rotation_version >= 1 && floor(var.db_password_rotation_version) == var.db_password_rotation_version)
    error_message = "db_password_rotation_version은 null 또는 1 이상의 정수여야 합니다."
  }
}

variable "rds_restore_snapshot_identifier" {
  description = "새 계정에서 이미 복사해 소유한 RDS 수동 스냅샷 이름. null이면 신규 DB 생성"
  type        = string
  default     = null

  validation {
    condition     = var.rds_restore_snapshot_identifier == null || can(regex("^[a-z][a-z0-9-]{0,254}$", var.rds_restore_snapshot_identifier))
    error_message = "rds_restore_snapshot_identifier는 같은 계정의 스냅샷 이름만 허용합니다."
  }
}
