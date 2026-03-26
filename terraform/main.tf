# AWS 리소스(VPC, EKS 등)를 실제로 생성하는 파일

# VPC
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true # EC2/Node에 퍼블릭 DNS 이름을 부여
  enable_dns_support   = true # VPC 내부에서 AWS DNS 서버를 사용할 수 있게 함

  tags = {
    Name = "${var.project_name}-vpc"
    #EKS가 이 VPC를 인식하려면 아래 태그가 필요함
    "kubernetes.io/cluster/${var.project_name}" = "shared" # 모든 클러스터가 공유
  }
}

# Public Subnet: az 개수만큼 서브넷 생성 / 서브넷과 vpc 연결 / EC2 생성 시 IP 자동 할당
resource "aws_subnet" "public" {
  count  = length(var.availability_zones)
  vpc_id = aws_vpc.main.id
  # cidrsubnet(prefix, newbits, netnum)
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = var.availability_zones[count.index] # count.index는 0부터 시작함

  # Public Subnet은 EC2 생성 시 퍼블릭 IP를 자동 할당함
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.project_name}-public-${var.availability_zones[count.index]}"
    # EKS가 LB를 Public Subnet에 붙일려면 이 태그가 필요함
    "kubernetes.io/cluster/${var.project_name}" = "shared"
    "kubernetes.io/role/elb"                    = "1"
  }
}

# Private Subnet
resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "${var.project_name}-private-${var.availability_zones[count.index]}"
    # EKS가 내부 로드밸런서를 Private Subnet에 붙이려면 이 태그 필요
    "kubernetes.io/cluster/${var.project_name}" = "owned"
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" { # 타입이 다르면 이름이 중복되어도 괜찮음
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-igw"
  }
}

# Public Route Table: Public Subnet의 트래피을 IGW로 보낼 때 사용하는 규칙
# 1. (Public Subnet -> IGW -> 인터넷) 구조
# 라우트 테이블(규칙) 생성과 연결은 설정이 분리되어 있음
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"                  # 모든 트랙픽을
    gateway_id = aws_internet_gateway.main.id # IGW로 보냄
  }

  tags = {
    Name = "${var.project_name}-public-rt"
  }
}

# 2. Route Table을 Public Subnet에 연결
resource "aws_route_table_association" "public" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id # terraform이 라우트 테이블을 생성하면 해당 테이블의 ID를 반환함
}

# NAT Gateway: private subnet이 인터넷을 사용하게 하기 위해 사용함(아웃바운드 허용, 인바운드 비허용)
# 1. Elastic IP (NAT Gateway에 고정 IP 할당)
resource "aws_eip" "nat" {
  domain = "vpc" # VPC용 EIP임을 지정

  tags = {
    Name = "${var.project_name}-nat-eip"
  }

  # IGW가 먼저 만들어져야 EIP 할당 가능
  depends_on = [aws_internet_gateway.main]
}

# 2. NAT GW (Public Subnet에 위치) - 1개
resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id          # NAT GW가 인터넷으로 나갈 때 사용할 EIP 연결
  subnet_id     = aws_subnet.public[0].id # 첫 번째 Public Subnet에 배치

  tags = {
    Name = "${var.project_name}-nat"
  }

  depends_on = [aws_internet_gateway.main]
}

# Private Route Table
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = {
    Name = "${var.project_name}-private-rt"
  }
}

# Route Table을 Private Subnet에 연결
resource "aws_route_table_association" "private" {
  count          = length(var.availability_zones)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

