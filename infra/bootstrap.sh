#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────
# CloudiQS Engine — One-time stack bootstrap.
#
# Does everything needed to go from zero to a running stack:
#   1. Deploys the CloudFormation stack (VPC, EC2, IAM, S3, EIP)
#   2. Creates all Secrets Manager secrets (via infra/secrets-init.sh)
#   3. Prompts you to update each secret with real values
#   4. Prints the GitHub Actions secrets you need to configure
#
# Prerequisites:
#   - AWS CLI configured for account 736956442878, region eu-west-1
#   - Sufficient IAM permissions (CloudFormation, EC2, IAM, S3, SecretsManager)
#   - A GitHub deploy token with read access to the repo
#
# Usage:
#   bash infra/bootstrap.sh
#   STACK_NAME=cloudiqs-staging bash infra/bootstrap.sh
#
# ────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Windows: Git Bash doesn't inherit the system PATH where AWS CLI is installed
if ! command -v aws >/dev/null 2>&1; then
    export PATH="$PATH:/c/Program Files/Amazon/AWSCLIV2"
fi

STACK_NAME="${STACK_NAME:-cloudiqs-engine}"
REGION="${AWS_REGION:-eu-west-1}"
TEMPLATE="infra/cloudformation.yml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
CYAN="\033[0;36m"
RED="\033[0;31m"
BOLD="\033[1m"
NC="\033[0m"

header() { echo -e "\n${BOLD}${CYAN}══ $1 ══${NC}"; }
ok()     { echo -e "  ${GREEN}OK${NC}  $1"; }
warn()   { echo -e "  ${YELLOW}WARN${NC} $1"; }
fail()   { echo -e "  ${RED}FAIL${NC} $1"; exit 1; }
prompt() {
    local var="$1" msg="$2" default="${3:-}"
    if [ -n "$default" ]; then
        read -rp "  $msg [$default]: " _input
        eval "$var=\"${_input:-$default}\""
    else
        read -rp "  $msg: " _input
        eval "$var=\"$_input\""
    fi
}
prompt_secret() {
    local var="$1" msg="$2"
    read -rsp "  $msg (hidden): " _input
    echo ""
    eval "$var=\"$_input\""
}

# ── Banner ─────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}CloudiQS Engine — Bootstrap${NC}"
echo "Stack: ${STACK_NAME} | Region: ${REGION}"
echo ""
echo "This script deploys the CloudFormation stack and initialises all secrets."
echo "It will prompt you for required values. Have the following ready:"
echo "  - GitHub deploy token (fine-grained, read-only to this repo)"
echo "  - OpenClaw install command (from your OpenClaw account)"
echo "  - All API keys (HubSpot, Instantly, Brave, Teams, Companies House)"
echo ""
read -rp "Press Enter to continue, or Ctrl+C to abort..."

# ── 0. Preflight ───────────────────────────────────────────────────────────

header "0. Preflight checks"

command -v aws  >/dev/null 2>&1 || fail "aws CLI not found. Install from https://aws.amazon.com/cli/"
command -v jq   >/dev/null 2>&1 || fail "jq not found. Install with: apt install jq / brew install jq"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || fail "AWS credentials not configured. Run 'aws configure' or set AWS_PROFILE."
ok "AWS account: ${ACCOUNT} | Region: ${REGION}"

[ -f "${REPO_ROOT}/${TEMPLATE}" ] || fail "Template not found: ${TEMPLATE}"
ok "Template found: ${TEMPLATE}"

# ── 1. Collect deploy parameters ──────────────────────────────────────────

header "1. Deploy parameters"

prompt_secret DEPLOY_TOKEN "GitHub deploy token"
[ -n "${DEPLOY_TOKEN}" ] || fail "Deploy token is required."

OPENCLAW_DEFAULT="echo 'OpenClaw install command not provided — install manually via SSM'"
prompt OPENCLAW_CMD "OpenClaw install command" "${OPENCLAW_DEFAULT}"

prompt INSTANCE_TYPE "Instance type" "t3.large"

prompt GITHUB_REPO "GitHub org/repo" "CloudiQS/cloudiqs-agents"

# ── 2. Deploy CloudFormation stack ────────────────────────────────────────

header "2. Deploy CloudFormation stack"

STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].StackStatus" \
  --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [ "${STACK_STATUS}" = "DOES_NOT_EXIST" ]; then
  echo "  Stack does not exist — creating..."
  ACTION="create-stack"
elif [[ "${STACK_STATUS}" == *ROLLBACK* ]] || [[ "${STACK_STATUS}" == *FAILED* ]]; then
  fail "Stack is in state ${STACK_STATUS}. Manually delete it first: aws cloudformation delete-stack --stack-name ${STACK_NAME} --region ${REGION}"
else
  echo "  Stack exists (${STACK_STATUS}) — updating..."
  ACTION="update-stack"
fi

aws cloudformation deploy \
  --template-file "${REPO_ROOT}/${TEMPLATE}" \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    StackName="${STACK_NAME}" \
    InstanceType="${INSTANCE_TYPE}" \
    GithubOrgRepo="${GITHUB_REPO}" \
    DeployToken="${DEPLOY_TOKEN}" \
    OpenClawInstallCmd="${OPENCLAW_CMD}"

ok "Stack deployed"

# ── 3. Fetch stack outputs ─────────────────────────────────────────────────

header "3. Stack outputs"

OUTPUTS=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs" \
  --output json)

get_output() {
  echo "${OUTPUTS}" | jq -r ".[] | select(.OutputKey==\"$1\") | .OutputValue"
}

INSTANCE_ID=$(get_output "InstanceId")
ROLE_ARN=$(get_output "GitHubDeployRoleArn")
EIP=$(get_output "ElasticIPAddress")
BUCKET=$(get_output "UploadsBucketName")

echo "  Instance ID:   ${INSTANCE_ID}"
echo "  EIP:           ${EIP}"
echo "  Uploads bucket: ${BUCKET}"

# ── 4. Initialise Secrets Manager (DUMMY values) ──────────────────────────

header "4. Initialise Secrets Manager"

STACK_NAME="${STACK_NAME}" AWS_REGION="${REGION}" \
  bash "${SCRIPT_DIR}/secrets-init.sh" "${STACK_NAME}" "${REGION}"

# ── 5. Populate secrets interactively ─────────────────────────────────────

header "5. Populate secrets with real values"

PREFIX="cloudiqs/${STACK_NAME}"

put_secret() {
  local key="$1" value="$2"
  aws secretsmanager put-secret-value \
    --secret-id "${PREFIX}/${key}" \
    --secret-string "${value}" \
    --region "${REGION}" \
    --output text --query "ARN" >/dev/null
  ok "${PREFIX}/${key}"
}

echo "  Enter real values for each secret. Press Enter to skip (keeps DUMMY)."
echo ""

prompt_secret VAL "HubSpot API key (hubspot/api-key)"
[ -n "${VAL}" ] && put_secret "hubspot/api-key" "${VAL}"

prompt_secret VAL "Instantly API key (instantly/api-key)"
[ -n "${VAL}" ] && put_secret "instantly/api-key" "${VAL}"

prompt_secret VAL "Brave Search API key (brave/api-key)"
[ -n "${VAL}" ] && put_secret "brave/api-key" "${VAL}"

prompt_secret VAL "Teams webhook URL (teams/webhook-url)"
[ -n "${VAL}" ] && put_secret "teams/webhook-url" "${VAL}"

prompt_secret VAL "Companies House API key (companies-house/api-key)"
[ -n "${VAL}" ] && put_secret "companies-house/api-key" "${VAL}"

# Generate bridge API key automatically
BRIDGE_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
put_secret "bridge/api-key" "${BRIDGE_KEY}"
echo "  Generated bridge/api-key: ${BRIDGE_KEY}"

echo ""
echo "  Instantly campaign IDs — skip now and add via AWS Console after creating campaigns."
prompt_secret VAL "Instantly VMware campaign ID (or Enter to skip)"
[ -n "${VAL}" ] && put_secret "instantly/vmware-campaign-id" "${VAL}"

prompt_secret VAL "Instantly MSP campaign ID (or Enter to skip)"
[ -n "${VAL}" ] && put_secret "instantly/msp-campaign-id" "${VAL}"

# ── 6. Wait for EC2 bootstrap to complete ─────────────────────────────────

header "6. Wait for EC2 bootstrap"

echo "  Waiting for instance to pass health checks (may take 3-5 minutes)..."
aws ec2 wait instance-status-ok \
  --instance-ids "${INSTANCE_ID}" \
  --region "${REGION}" \
  && ok "Instance healthy" \
  || warn "Instance health check timed out — check /var/log/user-data.log via SSM"

# ── 7. Print GitHub Actions secrets ───────────────────────────────────────

header "7. GitHub Actions secrets to configure"

echo ""
echo -e "  Go to: ${CYAN}https://github.com/${GITHUB_REPO}/settings/secrets/actions${NC}"
echo ""
echo -e "  ${BOLD}AWS_DEPLOY_ROLE_ARN${NC}"
echo "    ${ROLE_ARN}"
echo ""
echo -e "  ${BOLD}EC2_INSTANCE_ID${NC}"
echo "    ${INSTANCE_ID}"
echo ""
echo -e "  ${BOLD}Elastic IP (for DNS / webhook allowlists):${NC}"
echo "    ${EIP}"
echo ""
echo "  Add these two secrets, then push any commit to trigger a deploy."
echo ""
echo -e "${GREEN}Bootstrap complete.${NC}"
echo ""
echo "Next steps:"
echo "  1. Add GitHub Actions secrets above"
echo "  2. Populate remaining Instantly campaign IDs in AWS Console"
echo "  3. Update partner-central/role-arn if using ACE (see docs/AWS-MCP-SETUP.md)"
echo "  4. Push a commit to trigger the first automated deploy"
echo "  5. Run: curl http://${EIP}:8787/health  (after deploy completes)"
echo ""
