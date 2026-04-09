# AWS 리소스(VPC, EKS 등)를 실제로 생성하는 파일

# ## --- 수동 리소스 작업 ------------------------
# # VPC
# resource "aws_vpc" "main" {
#   cidr_block           = var.vpc_cidr
#   enable_dns_hostnames = true # EC2/Node에 퍼블릭 DNS 이름을 부여
#   enable_dns_support   = true # VPC 내부에서 AWS DNS 서버를 사용할 수 있게 함
#
#   tags = {
#     Name = "${var.project_name}-vpc"
#     # EKS가 이 VPC를 인식하려면 아래 태그가 필요함
#     "kubernetes.io/cluster/${var.project_name}" = "shared" # 모든 클러스터가 공유
#   }
# }
#
# # Public Subnet: az 개수만큼 서브넷 생성 / 서브넷과 vpc 연결 / EC2 생성 시 IP 자동 할당
# resource "aws_subnet" "public" {
#   count  = length(var.availability_zones)
#   vpc_id = aws_vpc.main.id
#   # cidrsubnet(prefix, newbits, netnum)
#   cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
#   availability_zone = var.availability_zones[count.index] # count.index는 0부터 시작함
#
#   # Public Subnet은 EC2 생성 시 퍼블릭 IP를 자동 할당함
#   map_public_ip_on_launch = true
#
#   tags = {
#     Name = "${var.project_name}-public-${var.availability_zones[count.index]}"
#     # EKS가 LB를 Public Subnet에 붙일려면 이 태그가 필요함
#     "kubernetes.io/cluster/${var.project_name}" = "shared"
#     # External NLB
#     "kubernetes.io/role/elb"                    = "1"
#   }
# }
#
# # Private Subnet
# resource "aws_subnet" "private" {
#   count             = length(var.availability_zones)
#   vpc_id            = aws_vpc.main.id
#   cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
#   availability_zone = var.availability_zones[count.index]
#
#   tags = {
#     Name = "${var.project_name}-private-${var.availability_zones[count.index]}"
#     # EKS가 내부 로드밸런서를 Private Subnet에 붙이려면 이 태그 필요
#     # 이 Subnet이 어느 클러스터 소속인가를 나타내는 코드임
#     "kubernetes.io/cluster/${var.project_name}" = "owned"
#     # 이 Subnet이 어떤 용도인가를 나타내는 코드임
#     # "kubernetes.io/role/internal-elb" = "1"
#   }
# }
#
# # Internet Gateway
# resource "aws_internet_gateway" "main" { # 타입이 다르면 이름이 중복되어도 괜찮음
#   vpc_id = aws_vpc.main.id
#
#   tags = {
#     Name = "${var.project_name}-igw"
#   }
# }
#
# # Public Route Table: Public Subnet의 트래피을 IGW로 보낼 때 사용하는 규칙
# # 1. (Public Subnet -> IGW -> 인터넷) 구조
# # 라우트 테이블(규칙) 생성과 연결은 설정이 분리되어 있음
# resource "aws_route_table" "public" {
#   vpc_id = aws_vpc.main.id
#
#   route {
#     cidr_block = "0.0.0.0/0"                  # 모든 트랙픽을
#     gateway_id = aws_internet_gateway.main.id # IGW로 보냄
#   }
#
#   tags = {
#     Name = "${var.project_name}-public-rt"
#   }
# }
#
# # 2. Route Table을 Public Subnet에 연결
# resource "aws_route_table_association" "public" {
#   count          = length(var.availability_zones)
#   subnet_id      = aws_subnet.public[count.index].id
#   route_table_id = aws_route_table.public.id # terraform이 라우트 테이블을 생성하면 해당 테이블의 ID를 반환함
# }
#
# # NAT Gateway: private subnet이 인터넷을 사용하게 하기 위해 사용함(아웃바운드 허용, 인바운드 비허용)
# # 1. Elastic IP (NAT Gateway에 고정 IP 할당)
# resource "aws_eip" "nat" {
#   domain = "vpc" # VPC용 EIP임을 지정
#
#   tags = {
#     Name = "${var.project_name}-nat-eip"
#   }
#
#   # IGW가 먼저 만들어져야 EIP 할당 가능
#   depends_on = [aws_internet_gateway.main]
# }
#
# # 2. NAT GW (Public Subnet에 위치) - 1개
# resource "aws_nat_gateway" "main" {
#   allocation_id = aws_eip.nat.id          # NAT GW가 인터넷으로 나갈 때 사용할 EIP 연결
#   subnet_id     = aws_subnet.public[0].id # 첫 번째 Public Subnet에 배치
#
#   tags = {
#     Name = "${var.project_name}-nat"
#   }
#
#   depends_on = [aws_internet_gateway.main]
# }
#
# # Private Route Table
# resource "aws_route_table" "private" {
#   vpc_id = aws_vpc.main.id
#
#   route {
#     cidr_block     = "0.0.0.0/0"
#     nat_gateway_id = aws_nat_gateway.main.id
#   }
#
#   tags = {
#     Name = "${var.project_name}-private-rt"
#   }
# }
#
# # Route Table을 Private Subnet에 연결
# resource "aws_route_table_association" "private" {
#   count          = length(var.availability_zones)
#   subnet_id      = aws_subnet.private[count.index].id
#   route_table_id = aws_route_table.private.id
# }
#
# # ----- I AM Role for EKS Cluster -----------------------------------------
# # EKS Control Plane이 AWS 서비스를 호출할 수 있는 권한
#
# # 누가(주체) 맡을 수 있는 Role 정의 - 권한 자체는 없음
# resource "aws_iam_role" "eks_cluster" {
#   name = "${var.project_name}-eks-cluster-role"
#
#   # 신뢰 정책 구조
#   assume_role_policy = jsonencode({
#     Version = "2012-10-17"
#     # 허용/거부 규칙 배열: 허용/거부 -> 누가 -> 어떤 액션을?
#     Statement = [{
#       Effect    = "Allow"                          # 허용/거부
#       Principal = { Service = "eks.amazonaws.com" } # 누가
#       Action    = "sts:AssumeRole"                 # 허용할 AWS API 액션
#     }]
#   })
# }
#
# # Role에 권한을 붙이기 위해 만든 "권한"
# resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
#   policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy" # AWS의 관리형 정책(권한 모음집)
#   role       = aws_iam_role.eks_cluster.name                    # 이 관리형 정책을 누구에게 붙일 것인가?
# }
#
# # ----- I AM Role for EKS NodeGroup -----------------------------------------
# # Worker Node(EC2)가 EKS/ECR/VPC 등을 사용할 수 있는 권한
# resource "aws_iam_role" "eks_node" {
#   name = "${var.project_name}-eks-node-role"
#
#   assume_role_policy = jsonencode({
#     Version = "2012-10-17"
#     Statement = [
#       {
#         Effect    = "Allow"
#         Principal = { Service = "ec2.amazonaws.com" }
#         Action    = "sts:AssumeRole"
#       }
#     ]
#   })
# }
#
# # 필수 정책 3개 연결
#
# # Worker Node가 EKS Control Plane과 토신
# resource "aws_iam_role_policy_attachment" "eks_worker_node" {
#   policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
#   role       = aws_iam_role.eks_node.name
# }
# # aws-node(CNI)가 ENI를 조작하는 권한
# resource "aws_iam_role_policy_attachment" "eks_cni" {
#   policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
#   role       = aws_iam_role.eks_node.name
# }
# # ECR에서 이미지를 pull하는 권한
# resource "aws_iam_role_policy_attachment" "ecr_read" {
#   policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
#   role       = aws_iam_role.eks_node.name
# }
#
# # ----- EKS Cluster * Control Plane * -----------------------------------------
# # 구성 요소: VPC / Subnet / IAM Role / Cluster Endpoint / Security Group
# resource "aws_eks_cluster" "main" {
#   name     = var.project_name
#   role_arn = aws_iam_role.eks_cluster.arn
#   version  = var.eks_cluster_version
#
#   vpc_config {
#     # Worker Node가 위치할 Subnet (Private)
#     subnet_ids = aws_subnet.private[*].id
#
#     # Public: kubectl이 외부에서 API Server 접근 가능 - 로컬에서 kubectl하려면 필요함
#     endpoint_public_access = true
#     # Private: VPC 내부에서도 API Server 접근 가능 - worker 노드가 control plane과 통신시 VPC를 사용함
#     endpoint_private_access = true
#   }
#
#   tags = {
#     Name = "${var.project_name}-eks"
#   }
#
#   depends_on = [
#     aws_iam_role_policy_attachment.eks_cluster_policy
#   ]
# }
#
# # ----- EKS Managed NodeGroup * Worker Node * -----------------------------------------
# # 구성 요소: Subnet / IAM Role / Instance Type / 노드 수 / EKS Cluster 이름 / Scaling 설
# resource "aws_eks_node_group" "main" {
#   cluster_name    = aws_eks_cluster.main.name
#   node_group_name = "${var.project_name}-nodegroup"
#   node_role_arn   = aws_iam_role.eks_node.arn
#
#   # Worker Node는 Private Subnet에 배치
#   subnet_ids = aws_subnet.private[*].id
#
#   instance_types = [var.node_instance_type]
#
#   scaling_config {
#     desired_size = var.node_desired_size
#     min_size     = var.node_min_size
#     max_size     = var.node_max_size
#   }
#
#   # 노드 업데이트 방식: 최대 1개씩 교체 (서비스 무중단 배포 목적)
#   update_config {
#     max_unavailable = 1
#   }
#
#   tags = {
#     Name = "${var.project_name}-node"
#   }
#
#   depends_on = [
#     aws_iam_role_policy_attachment.eks_worker_node,
#     aws_iam_role_policy_attachment.eks_cni,
#     aws_iam_role_policy_attachment.ecr_read,
#   ]
# }

## --- 모듈 사용 ver.-------------------------
# VPC — terraform-aws-modules/vpc/aws
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${var.project_name}-vpc"
  cidr = var.vpc_cidr

  azs = var.availability_zones

  # 기존 cidrsubnet 로직과 동일한 결과
  public_subnets  = [for i, az in var.availability_zones : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnets = [for i, az in var.availability_zones : cidrsubnet(var.vpc_cidr, 8, i + 10)]

  enable_nat_gateway   = true
  single_nat_gateway   = true # NAT GW 1개로 비용 절감 (기존과 동일)
  enable_dns_hostnames = true
  enable_dns_support   = true

  # EKS ALB Ingress를 위한 서브넷 태그
  # 각 AZ의 모든 public, subnet 대상. 각각 다로 설정하고 싶다면 subnet 이름으로 명시 설정 필요
  public_subnet_tags = {
    "kubernetes.io/role/elb"                    = "1" # 1은 활성화, 0은 비활성화를 의미
    "kubernetes.io/cluster/${var.project_name}" = "shared"
  }

  private_subnet_tags = {
    "kubernetes.io/cluster/${var.project_name}" = "owned"
    "karpenter.sh/discovery"                    = var.project_name
  }

  tags = {
    Name = "${var.project_name}-vpc"
  }
}

# EKS — terraform-aws-modules/eks/aws
module "eks" {
  source  = "terraform-aws-modules/eks/aws" # Terraform Registry에 있는 공식 EKS 모듈 다운로드
  version = "~> 20.0"

  cluster_name    = var.project_name
  cluster_version = var.eks_cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access  = true
  cluster_endpoint_private_access = true

  # IRSA용 OIDC Provider 자동 생성 (iam_oidc.tf 대체)
  enable_irsa = true

  # 관리형 노드 그룹
  eks_managed_node_groups = {
    main = {
      name           = "${var.project_name}-nodegroup"
      instance_types = [var.node_instance_type]

      min_size     = var.node_min_size
      max_size     = var.node_max_size
      desired_size = var.node_desired_size

      update_config = {
        max_unavailable = 1
      }

      tags = {
        Name = "${var.project_name}-node"
      }
    }
  }

  # 클러스터 생성자에게 AWS 리소스에 접근할 수 있는 Role을 부여하기 위한 코드: helm으로 ALB Controller를 직접 설치하기 위해 필요
  access_entries = {
    admin = {
      principal_arn = "arn:aws:iam::716174522908:user/IAM-jounghyeon"
      policy_associations = {
        admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }

  # EKS 기본 애드온 (EBS CSI는 IRSA 의존성으로 ebs_csi.tf에서 별도 관리)
  cluster_addons = {
    coredns    = {}
    kube-proxy = {}
    vpc-cni    = {}
  }

  # SecurityGroup에 태그 전파 (노드 네트워크 설정)
  cluster_tags = {
    "karpenter.sh/discovery" = var.project_name
  }

  # AWS Console에서 EKS 클러스터 리소스의 이름 표시용
  tags = {
    Name = "${var.project_name}-eks"
  }
}
