terraform {
  # 버킷은 이 root의 init 전에 별도 부트스트랩한다.
  # 환경별 bucket/key/region/use_lockfile은 backend.*.hcl에서 제공한다.
  backend "s3" {}
}
