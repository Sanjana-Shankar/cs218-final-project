#!/usr/bin/env bash
# Tear down the Cloud Cost Optimizer stacks.
#
#   ./destroy.sh           ask for the project name, then delete both stacks
#   ./destroy.sh --yes     skip the confirmation prompt
#   ./destroy.sh --all     also delete the artifact bucket
#   ./destroy.sh --yes --all
#
# Missing stacks, buckets, and the OpenAI API key parameter are skipped, so a
# partial deploy can still be cleaned up.
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-cloud-cost-optimizer}"
AWS_REGION="${AWS_REGION:-us-west-2}"
BACKEND_STACK="${BACKEND_STACK:-$PROJECT_NAME-backend}"
FRONTEND_STACK="${FRONTEND_STACK:-$PROJECT_NAME-frontend}"
OPENAI_API_KEY_PARAMETER="${OPENAI_API_KEY_PARAMETER:-/${PROJECT_NAME}/openai-api-key}"

YES=0
ALL=0
for arg in "$@"; do
  case "$arg" in
    --yes) YES=1 ;;
    --all) ALL=1 ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: ./destroy.sh [--yes] [--all]" >&2
      exit 1
      ;;
  esac
done

cd "$(dirname "$0")"

die() {
  echo "Error: $*" >&2
  exit 1
}

if ! command -v aws >/dev/null 2>&1; then
  die "aws is not installed. Install AWS CLI v2 before deleting stacks."
fi
if ! aws sts get-caller-identity --region "$AWS_REGION" >/dev/null; then
  die "AWS credentials are not working. Run 'aws configure' before deleting stacks."
fi

if [ "$YES" -ne 1 ]; then
  if [ ! -t 0 ]; then
    die "Refusing to delete without a confirmation prompt. Re-run with --yes."
  fi
  echo "This deletes the $PROJECT_NAME stacks in $AWS_REGION."
  read -r -p "Type the project name ($PROJECT_NAME) to confirm: " typed
  if [ "$typed" != "$PROJECT_NAME" ]; then
    die "Confirmation did not match. Nothing was deleted."
  fi
fi

ACCOUNT="$(aws sts get-caller-identity --region "$AWS_REGION" --query Account --output text)"

stack_status() {
  local name="$1"
  local status
  if ! status="$(aws cloudformation describe-stacks \
      --region "$AWS_REGION" \
      --stack-name "$name" \
      --query "Stacks[0].StackStatus" \
      --output text 2>/dev/null)"; then
    echo ""
    return 0
  fi
  echo "$status"
}

stack_output() {
  local name="$1"
  local key="$2"
  local value
  if ! value="$(aws cloudformation describe-stacks \
      --region "$AWS_REGION" \
      --stack-name "$name" \
      --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue" \
      --output text 2>/dev/null)"; then
    echo ""
    return 0
  fi
  if [ "$value" = "None" ]; then
    echo ""
    return 0
  fi
  echo "$value"
}

bucket_exists() {
  local bucket="$1"
  aws s3api head-bucket --bucket "$bucket" --region "$AWS_REGION" >/dev/null 2>&1
}

empty_bucket() {
  local bucket="$1"
  if [ -z "$bucket" ]; then
    echo "No bucket name; skipping."
    return 0
  fi
  if bucket_exists "$bucket"; then
    echo "Emptying s3://$bucket ..."
    aws s3 rm "s3://$bucket" --recursive --region "$AWS_REGION"
  else
    echo "Bucket $bucket does not exist or is not accessible; skipping."
  fi
}

delete_stack() {
  local name="$1"
  local status
  status="$(stack_status "$name")"
  if [ -z "$status" ]; then
    echo "Stack $name does not exist; skipping."
    return 0
  fi
  echo "Deleting stack $name ($status) ..."
  aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$name"
  aws cloudformation wait stack-delete-complete --region "$AWS_REGION" --stack-name "$name"
  echo "Deleted $name."
}

echo "1/5 Emptying the upload bucket and deleting $BACKEND_STACK ..."
UPLOAD_BUCKET="$(stack_output "$BACKEND_STACK" UploadBucketName)"
if [ -z "$UPLOAD_BUCKET" ]; then
  UPLOAD_BUCKET="${PROJECT_NAME}-uploads-${ACCOUNT}-${AWS_REGION}"
fi
empty_bucket "$UPLOAD_BUCKET"
delete_stack "$BACKEND_STACK"

echo "2/5 Emptying the frontend bucket and deleting $FRONTEND_STACK ..."
FRONTEND_BUCKET="$(stack_output "$FRONTEND_STACK" BucketName)"
empty_bucket "$FRONTEND_BUCKET"
delete_stack "$FRONTEND_STACK"

if [ "$ALL" -eq 1 ]; then
  echo "3/5 Deleting the artifact bucket..."
  ARTIFACT_BUCKET="${PROJECT_NAME}-artifacts-${ACCOUNT}-${AWS_REGION}"
  if bucket_exists "$ARTIFACT_BUCKET"; then
    echo "Emptying s3://$ARTIFACT_BUCKET ..."
    aws s3 rm "s3://$ARTIFACT_BUCKET" --recursive --region "$AWS_REGION"
    aws s3api delete-bucket --bucket "$ARTIFACT_BUCKET" --region "$AWS_REGION"
    echo "Deleted $ARTIFACT_BUCKET."
  else
    echo "Artifact bucket $ARTIFACT_BUCKET does not exist; skipping."
  fi
else
  echo "3/5 Leaving the artifact bucket in place. Re-run with --all to delete it."
fi

echo "4/5 Deleting the OpenAI API key parameter if it exists..."
ssm_err="$(mktemp)"
if aws ssm delete-parameter \
    --region "$AWS_REGION" \
    --name "$OPENAI_API_KEY_PARAMETER" 2>"$ssm_err"; then
  rm -f "$ssm_err"
  echo "Deleted SSM parameter $OPENAI_API_KEY_PARAMETER."
elif grep -q ParameterNotFound "$ssm_err"; then
  rm -f "$ssm_err"
  echo "SSM parameter $OPENAI_API_KEY_PARAMETER does not exist; skipping."
else
  cat "$ssm_err" >&2
  rm -f "$ssm_err"
  die "Could not delete SSM parameter $OPENAI_API_KEY_PARAMETER."
fi

echo "5/5 Done."
echo "Check Billing and Cost Management tomorrow to confirm charges have stopped."
