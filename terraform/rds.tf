# --- Security Group -----------------------
# EKS 노드에서만 5432 포트로 접근 허용
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds-sg"
  description = "RDS PostgreSQL - Allow access from EKS nodes only"
  vpc_id      = module.vpc.vpc_id

  # 인바운드 규칙 - EKS 노드에서만 허용 규칙 생성
  ingress {
    description     = "PostgreSQL from EKS nodes"
    from_port       = 5432 # 허용할 포트 시작
    to_port         = 5432 # 허용할 포트 끝
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  # 아웃바운드 규칙: RDS에서 모든 포트로 외부로 접근 허용
  egress {
    from_port   = 0 # 모든 포트
    to_port     = 0
    protocol    = "-1" # 모든 프로토콜
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-rds-sg" }
}

# --- Enhanced Monitoring IAM Role ---------------------
resource "aws_iam_role" "rds_enhanced_monitoring" {
  name = "${var.project_name}-rds-enhanced-monitoring"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_enhanced_monitoring" {
  role       = aws_iam_role.rds_enhanced_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# --- RDS Primary --------------------------
# terraform-aws-modules/rds: Subnet Group · Parameter Group · DB Instance 통합 생성
module "rds" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~>7.2.0"

  identifier = "${var.project_name}-postgres"

  # 엔진
  engine         = "postgres"
  engine_version = "17.9"

  # 인스턴스 스펙 & 스토리지
  instance_class        = "db.t3.micro"
  allocated_storage     = 20  # 초기
  max_allocated_storage = 100 # 자동 확장 상한
  storage_type          = "gp3"
  storage_encrypted     = true

  # DB
  db_name  = "urldb"
  username = "postgres"
  port     = 5432
  # Read Replica를 사용하기 위해 Secrets Manager 자동관리(ManageMasterUserPassword)는 비활성화
  manage_master_user_password = false
  password_wo         = var.db_password
  password_wo_version = 1 # 비밀번호 버전 1로 설정

  iam_database_authentication_enabled = true # IAM 인증 -> 일반 계정 사용자를 위한 접근 방법

  # 네트워크
  # DB Subnet Group을 명시적으로 생성/사용해서 "기본 VPC"로 떨어지는 것을 방지
  create_db_subnet_group = true
  subnet_ids             = module.vpc.private_subnets # EKS와 동일한 private subnet
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false # 인터넷 노출 금지

  # Parameter Group
  # pg_stat_statements: 느린 쿼리 분석용
  family = "postgres17"
  create_db_option_group = false

  parameters = [
    {
      name         = "shared_preload_libraries"
      value        = "pg_stat_statements" # 
      apply_method = "pending-reboot" # 파라미터 변경이 재부팅 후 적용되도록 설정
    },
    {
      name         = "pg_stat_statements.track"
      value        = "ALL" # 모든 쿼리 추적 (top: 상위 쿼리만)
      apply_method = "pending-reboot"
    }
  ]

  # 백업
  backup_retention_period = 7             # 백업 보관 기간
  backup_window           = "03:00-04:00" # 백업 시작 시간 설정

  # 유지보수 윈도우: AWS가 RDS에 패치, 업그레이드, 설정 변경 등을 적용하는 시간대 설정
  maintenance_window = "sun:18:00-sun:19:00"

  # Enhanced Monitoring: RDS 인스턴스가 올라가 있는 EC2 호스트의 메트릭 수집 / 대상: OS
  monitoring_interval = 60                                         # 메트릭 수집 간격 설정
  monitoring_role_arn = aws_iam_role.rds_enhanced_monitoring.arn # 메트릭 수집 대상

  # Performance insights: RDS에서 어떤 쿼리가 DB를 얼마나 점유하고 있는지 시각화 / 대상: DB 엔진 내부
  performance_insights_enabled          = true
  performance_insights_retention_period = 7 # 보관 기간

  # CloudWatch 로그 내보내기
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  # 자동 페일오버 설정
  multi_az = false # 비용 때문에 false로 설정 -> 운영환경에서는 true로 설정

  # 삭제 보호 설정
  deletion_protection = false # 비용 때문에 false로 설정 -> 운영환경에서는 true로 설정

  # 삭제 시 백업 생성
  skip_final_snapshot              = true # 비용 때문에 true로 설정 -> 운영환경에서는 false로 설정
  final_snapshot_identifier_prefix = "${var.project_name}-postgres-final-snapshot"

  tags = { Name = "${var.project_name}-postgres-primary" }
}

# --- RDS Read Replica ---------------------
# replicate_source_db 한 줄이 Primary → Replica 복제를 설정
module "rds_replica" {
  source = "terraform-aws-modules/rds/aws"
  version = "~> 7.2.0"

  identifier = "${var.project_name}-postgres-replica"

  # Subnet group을 지정하는 경우, AWS 제약상 replicate_source_db는 identifier가 아니라 ARN이어야 함
  replicate_source_db = module.rds.db_instance_arn
  
  instance_class = "db.t3.micro"
  storage_type = "gp3"

  vpc_security_group_ids = [aws_security_group.rds.id]

  # Replica도 동일 VPC(private subnet)에서 Subnet Group을 명시적으로 생성/사용
  create_db_subnet_group = true
  subnet_ids             = module.vpc.private_subnets
  create_db_parameter_group = false # Primary에서 상속 여부 설정
  create_db_option_group = false # Primary에서 상속 여부 설정

  # 백업
  backup_retention_period = 0
  skip_final_snapshot = true

  tags = { Name = "${var.project_name}-postgres-replica" }
}