# 기존 인프라의 backend와 분리한다. 해당 폴더에서만 초기화한다.
terraform {
  backend "s3" {}
}
