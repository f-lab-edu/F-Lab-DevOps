#!/usr/bin/env bash
set -euo pipefail

expected_account="716174522908"
region="ap-northeast-2"
bucket="urlshortener-tfstate-${expected_account}-${region}"
actual_account="$(aws sts get-caller-identity --query Account --output text)"

if [[ "${actual_account}" != "${expected_account}" ]]; then
  echo "Unexpected AWS account: ${actual_account}" >&2
  exit 1
fi

if ! aws s3api head-bucket --bucket "${bucket}" 2>/dev/null; then
  aws s3api create-bucket \
    --bucket "${bucket}" \
    --region "${region}" \
    --create-bucket-configuration "LocationConstraint=${region}"
fi

aws s3api put-public-access-block \
  --bucket "${bucket}" \
  --public-access-block-configuration \
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'

aws s3api put-bucket-ownership-controls \
  --bucket "${bucket}" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'

aws s3api put-bucket-versioning \
  --bucket "${bucket}" \
  --versioning-configuration 'Status=Enabled'

aws s3api put-bucket-encryption \
  --bucket "${bucket}" \
  --server-side-encryption-configuration \
    'Rules=[{ApplyServerSideEncryptionByDefault={SSEAlgorithm=AES256},BucketKeyEnabled=false}]'

policy="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DenyInsecureTransport",
    "Effect": "Deny",
    "Principal": "*",
    "Action": "s3:*",
    "Resource": ["arn:aws:s3:::${bucket}", "arn:aws:s3:::${bucket}/*"],
    "Condition": {"Bool": {"aws:SecureTransport": "false"}}
  }]
}
JSON
)"
aws s3api put-bucket-policy --bucket "${bucket}" --policy "${policy}"

echo "State bucket configured: ${bucket}"
