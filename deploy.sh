#!/usr/bin/env bash
# Deploy the Cloud Cost Optimizer frontend and backend from a clean checkout.
# Safe to re-run. Does not print credentials.
#
#   ./deploy.sh
#
# Optional environment:
#   PROJECT_NAME     default cloud-cost-optimizer
#   AWS_REGION       default us-west-2
#   BACKEND_STACK    default $PROJECT_NAME-backend
#   FRONTEND_STACK   default $PROJECT_NAME-frontend
#   OPENAI_API_KEY   if set, store it in SSM and deploy with OpenAI instead of Bedrock
#   OPENAI_MODEL     default gpt-4o-mini, passed as OpenAIModel when deploying
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-cloud-cost-optimizer}"
AWS_REGION="${AWS_REGION:-us-west-2}"
BACKEND_STACK="${BACKEND_STACK:-$PROJECT_NAME-backend}"
FRONTEND_STACK="${FRONTEND_STACK:-$PROJECT_NAME-frontend}"
# Same default as BedrockModelId in backend/infrastructure/template.yaml.
BEDROCK_MODEL_ID="us.anthropic.claude-haiku-4-5-20251001-v1:0"
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}"
OPENAI_API_KEY_PARAMETER="${OPENAI_API_KEY_PARAMETER:-/${PROJECT_NAME}/openai-api-key}"
if [ -n "${OPENAI_API_KEY:-}" ]; then
  AI_PROVIDER="openai"
else
  AI_PROVIDER="bedrock"
fi

# Temp files that hold the API key. Deleted on exit, including after a failure.
SECRET_FILES=()
cleanup_secret_files() {
  local status=$?
  local path
  if [ "${#SECRET_FILES[@]}" -gt 0 ]; then
    for path in "${SECRET_FILES[@]}"; do
      rm -f "$path"
    done
  fi
  return "$status"
}
trap cleanup_secret_files EXIT

# Sets SECRET_FILE. Must run in the current shell: a command substitution would
# drop the path from SECRET_FILES, and the exit trap would not delete it.
make_secret_file() {
  local path
  path="$(mktemp)"
  chmod 600 "$path"
  SECRET_FILES+=("$path")
  SECRET_FILE="$path"
}

cd "$(dirname "$0")"

die() {
  echo "Error: $*" >&2
  exit 1
}

require_cmd() {
  local name="$1"
  local hint="$2"
  if ! command -v "$name" >/dev/null 2>&1; then
    die "$name is not installed. $hint"
  fi
}

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
  aws cloudformation describe-stacks \
    --region "$AWS_REGION" \
    --stack-name "$name" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue" \
    --output text
}

# A failed first create leaves the stack in ROLLBACK_COMPLETE, which cannot be updated.
prepare_stack() {
  local name="$1"
  local status
  status="$(stack_status "$name")"
  if [ "$status" = "ROLLBACK_COMPLETE" ]; then
    echo "Stack $name is ROLLBACK_COMPLETE. Deleting it so it can be created again..."
    aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$name"
    aws cloudformation wait stack-delete-complete --region "$AWS_REGION" --stack-name "$name"
  fi
}

deploy_stack() {
  local name="$1"
  shift
  if ! aws cloudformation deploy --region "$AWS_REGION" --stack-name "$name" "$@"; then
    echo "Deployment of stack $name failed. Failed resources:" >&2
    aws cloudformation describe-stack-events \
      --region "$AWS_REGION" \
      --stack-name "$name" \
      --query "StackEvents[?contains(ResourceStatus, 'FAILED')].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" \
      --output text >&2 || true
    exit 1
  fi
}

echo "1/8 Checking prerequisites..."
require_cmd aws "Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
require_cmd node "Install Node.js 18 or newer from https://nodejs.org."
require_cmd npm "npm ships with Node.js 18+. Reinstall Node.js if npm is missing."
require_cmd curl "Install curl. The smoke test calls the API with it."

aws_version="$(aws --version 2>&1)"
case "$aws_version" in
  aws-cli/2*) ;;
  *) die "AWS CLI v2 is required. Found: $aws_version" ;;
esac

node_major="$(node -p "process.versions.node.split('.')[0]")"
if [ "$node_major" -lt 18 ]; then
  die "Node.js 18 or newer is required. Found $(node -v)."
fi

if ! aws sts get-caller-identity --region "$AWS_REGION" >/dev/null; then
  die "AWS credentials are not working. Run 'aws configure', or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
fi
ACCOUNT="$(aws sts get-caller-identity --region "$AWS_REGION" --query Account --output text)"
echo "Using account $ACCOUNT in $AWS_REGION."

echo "2/8 Deploying frontend infrastructure ($FRONTEND_STACK)..."
prepare_stack "$FRONTEND_STACK"
deploy_stack "$FRONTEND_STACK" \
  --template-file frontend/infrastructure/frontend-template.yaml \
  --parameter-overrides "ProjectName=${PROJECT_NAME}" \
  --no-fail-on-empty-changeset
FRONTEND_URL="$(stack_output "$FRONTEND_STACK" FrontendUrl)"
FRONTEND_URL="${FRONTEND_URL%/}"
if [ -z "$FRONTEND_URL" ] || [ "$FRONTEND_URL" = "None" ]; then
  die "Stack $FRONTEND_STACK did not return a FrontendUrl output."
fi
echo "Frontend URL: $FRONTEND_URL"

echo "3/8 Preparing the artifact bucket..."
ARTIFACT_BUCKET="${PROJECT_NAME}-artifacts-${ACCOUNT}-${AWS_REGION}"
if ! aws s3api head-bucket --bucket "$ARTIFACT_BUCKET" --region "$AWS_REGION" 2>/dev/null; then
  echo "Creating s3://$ARTIFACT_BUCKET ..."
  create_args=(--bucket "$ARTIFACT_BUCKET" --region "$AWS_REGION")
  if [ "$AWS_REGION" != "us-east-1" ]; then
    create_args+=(--create-bucket-configuration "LocationConstraint=${AWS_REGION}")
  fi
  if ! create_output="$(aws s3api create-bucket "${create_args[@]}" 2>&1)"; then
    case "$create_output" in
      *BucketAlreadyOwnedByYou*|*BucketAlreadyExists*)
        echo "Artifact bucket already exists."
        ;;
      *)
        echo "$create_output" >&2
        die "Could not create artifact bucket $ARTIFACT_BUCKET."
        ;;
    esac
  fi
fi
aws s3api put-public-access-block \
  --bucket "$ARTIFACT_BUCKET" \
  --region "$AWS_REGION" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption \
  --bucket "$ARTIFACT_BUCKET" \
  --region "$AWS_REGION" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

echo "4/8 Packaging and deploying the backend ($BACKEND_STACK)..."
if [ "$AI_PROVIDER" = "openai" ]; then
  echo "Storing the OpenAI API key in SSM parameter ${OPENAI_API_KEY_PARAMETER}."
  make_secret_file
  printf '%s' "$OPENAI_API_KEY" > "$SECRET_FILE"
  aws ssm put-parameter \
    --region "$AWS_REGION" \
    --name "$OPENAI_API_KEY_PARAMETER" \
    --type SecureString \
    --value "file://${SECRET_FILE}" \
    --overwrite >/dev/null
  rm -f "$SECRET_FILE"
fi
prepare_stack "$BACKEND_STACK"
if ! aws cloudformation package \
    --region "$AWS_REGION" \
    --template-file backend/infrastructure/template.yaml \
    --s3-bucket "$ARTIFACT_BUCKET" \
    --s3-prefix cloudformation \
    --output-template-file backend/infrastructure/.packaged.yaml; then
  die "Packaging the backend template failed."
fi
deploy_stack "$BACKEND_STACK" \
  --template-file backend/infrastructure/.packaged.yaml \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides \
    "ProjectName=${PROJECT_NAME}" \
    "FrontendUrl=${FRONTEND_URL}" \
    "AiProvider=${AI_PROVIDER}" \
    "OpenAIModel=${OPENAI_MODEL}" \
    "OpenAIApiKeyParameterName=${OPENAI_API_KEY_PARAMETER}" \
  --no-fail-on-empty-changeset

API_URL="$(stack_output "$BACKEND_STACK" ApiUrl)"
API_URL="${API_URL%/}"
USER_POOL_ID="$(stack_output "$BACKEND_STACK" UserPoolId)"
USER_POOL_CLIENT_ID="$(stack_output "$BACKEND_STACK" UserPoolClientId)"
if [ -z "$API_URL" ] || [ "$API_URL" = "None" ] \
    || [ -z "$USER_POOL_ID" ] || [ "$USER_POOL_ID" = "None" ] \
    || [ -z "$USER_POOL_CLIENT_ID" ] || [ "$USER_POOL_CLIENT_ID" = "None" ]; then
  die "Stack $BACKEND_STACK is missing ApiUrl, UserPoolId, or UserPoolClientId."
fi

echo "5/8 Writing frontend/.env ..."
cat > frontend/.env <<EOF
VITE_AWS_REGION=${AWS_REGION}
VITE_COGNITO_USER_POOL_ID=${USER_POOL_ID}
VITE_COGNITO_CLIENT_ID=${USER_POOL_CLIENT_ID}
VITE_API_BASE_URL=${API_URL}
EOF

echo "6/8 Building and uploading the frontend..."
(cd frontend && STACK_NAME="$FRONTEND_STACK" AWS_REGION="$AWS_REGION" ./deploy.sh)

echo "7/8 Running smoke tests..."
health_file="$(mktemp)"
if ! curl -fsS "$API_URL/health" -o "$health_file"; then
  rm -f "$health_file"
  die "Health check failed: GET $API_URL/health did not succeed."
fi
health_body="$(tr -d '[:space:]' < "$health_file")"
rm -f "$health_file"
if [ "$health_body" != '{"status":"ok"}' ]; then
  die "Health check returned unexpected body: $health_body"
fi
echo "Health check returned {\"status\": \"ok\"}."

auth_code="$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/analyses/test" || true)"
if [ "$auth_code" = "401" ]; then
  echo "Unauthenticated GET /analyses/test returned 401."
else
  echo "Warning: GET /analyses/test without a token returned $auth_code, expected 401." >&2
fi

if [ "$AI_PROVIDER" = "openai" ]; then
  make_secret_file
  hdrfile="$SECRET_FILE"
  printf 'Authorization: Bearer %s\n' "$OPENAI_API_KEY" > "$hdrfile"
  openai_code="$(curl -sS -o /dev/null -w "%{http_code}" -H @"$hdrfile" "https://api.openai.com/v1/models" || true)"
  rm -f "$hdrfile"
  if [ "$openai_code" != "200" ]; then
    echo "Warning: OpenAI API check returned ${openai_code:-no response}. The app still works with fallback text until the key can call the API." >&2
  else
    echo "OpenAI API accepted the key."
  fi
elif ! aws bedrock-runtime converse \
    --region "$AWS_REGION" \
    --model-id "$BEDROCK_MODEL_ID" \
    --messages '[{"role":"user","content":[{"text":"Reply with OK"}]}]' \
    --inference-config maxTokens=5 >/dev/null; then
  echo "Warning: Bedrock access to Claude Haiku 4.5 is not enabled in us-west-2. Enable it once in the Bedrock console (Anthropic asks for a short use-case form). The app still works with fallback text until then." >&2
fi

echo "8/8 Summary"
echo "Site: $FRONTEND_URL"
echo "API:  $API_URL"
echo "AI provider: $AI_PROVIDER"
echo "A new CloudFront distribution can take 5-10 minutes before the site loads."
echo "Manual test:"
echo "  1. Open the site, sign up, and sign in."
echo "  2. Upload data/sample_billing.csv. Expect a total of \$2,840.00 and 4 recommendations."
echo "  3. Ask a chat question about that bill."
echo "  4. Upload data/sample_billing_invalid.csv. Expect an error about usageDate."
