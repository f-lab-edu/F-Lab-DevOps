variable "aws_region" {
  description = "AI 이미지 저장소를 둘 서울 리전"
  type        = string
  default     = "ap-northeast-2"

  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "이 실습 구성은 ap-northeast-2에서만 실행합니다."
  }
}

variable "expected_account_id" {
  description = "접속을 허용할 AWS 계정. provider가 실제 인증 계정과 대조한다"
  type        = string
  default     = "716174522908"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "AWS 계정 ID는 숫자 12자리여야 합니다."
  }
}

variable "project_name" {
  description = "기존 앱과 구분하는 AI 실습 리소스 접두사"
  type        = string
  default     = "urlshortener-practice"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]*-practice$", var.project_name)) && length(var.project_name) <= 40
    error_message = "접두사는 영문 소문자로 시작하고 영문 소문자·숫자·하이픈만 사용하며 -practice로 끝나는 40자 이하여야 합니다."
  }
}

variable "environment" {
  description = "AI 등록 리소스의 실습 환경 태그. 클러스터 환경을 변경하지 않는다"
  type        = string
  default     = "practice"

  validation {
    condition     = var.environment == "practice"
    error_message = "이 전용 root는 practice 환경만 관리합니다."
  }
}

variable "enable_ai_ops_registry" {
  description = "독립 AI 이미지 저장소와 등록 전용 역할 생성 여부. 기본 비활성"
  type        = bool
  default     = false
}
