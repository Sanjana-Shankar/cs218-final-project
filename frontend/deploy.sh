#!/usr/bin/env bash
# Deploys the frontend: creates/updates the S3 + CloudFront stack, builds, uploads, clears the cache.
# Usage (from frontend/):  ./deploy.sh
# Needs: AWS CLI configured (aws configure), and a filled-in .env
set -euo pipefail

STACK_NAME="${STACK_NAME:-cloud-cost-optimizer-frontend}"
REGION="${AWS_REGION:-us-west-2}"

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Missing frontend/.env - copy .env.example and fill in the values first." >&2
  exit 1
fi

echo "1/4 Creating or updating the S3 + CloudFront stack ($STACK_NAME)..."
aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --template-file infrastructure/frontend-template.yaml \
  --no-fail-on-empty-changeset

output() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
BUCKET="$(output BucketName)"
DISTRIBUTION_ID="$(output DistributionId)"
URL="$(output FrontendUrl)"

echo "2/4 Building..."
npm ci
npm run build

echo "3/4 Uploading to s3://$BUCKET ..."
# Hashed assets never change, so cache them for a year; index.html must always be fresh
aws s3 sync dist/ "s3://$BUCKET" --delete \
  --exclude index.html \
  --cache-control "public, max-age=31536000, immutable"
aws s3 cp dist/index.html "s3://$BUCKET/index.html" \
  --cache-control "no-cache"

echo "4/4 Clearing the CloudFront cache..."
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" \
  --query "Invalidation.Id" --output text

echo
echo "Done! Frontend URL: $URL"
echo "(A brand-new distribution can take ~5-10 minutes before the URL works.)"
