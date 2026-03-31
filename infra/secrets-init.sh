#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────────────────
# CloudiQS Engine — Secrets Manager initialisation script.
#
# Creates all required Secrets Manager secrets with DUMMY placeholder values.
# Run once per environment BEFORE running deploy.sh.
# Then update each secret via AWS Console or CLI with the real values.
#
# Usage:
#   bash infra/secrets-init.sh                          # uses cloudiqs-engine, eu-west-1
#   bash infra/secrets-init.sh cloudiqs-staging         # staging environment
#   bash infra/secrets-init.sh cloudiqs-engine us-east-1
#
# ────────────────────────────────────────────────────────────────────────────
set -euo pipefail

STACK_NAME="${1:-cloudiqs-engine}"
AWS_REGION="${2:-eu-west-1}"
PREFIX="cloudiqs/${STACK_NAME}"

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
NC="\033[0m"

echo -e "${GREEN}CloudiQS Engine — Secrets Manager Init${NC}"
echo "Stack: ${STACK_NAME} | Region: ${AWS_REGION} | Prefix: ${PREFIX}"
echo ""

create_secret() {
    local name="$1"
    local description="$2"
    local full_name="${PREFIX}/${name}"

    # Check if secret already exists
    if aws secretsmanager describe-secret \
        --secret-id "${full_name}" \
        --region "${AWS_REGION}" \
        --query "ARN" \
        --output text 2>/dev/null; then
        echo -e "  ${YELLOW}EXISTS${NC}  ${full_name}"
        return 0
    fi

    aws secretsmanager create-secret \
        --name "${full_name}" \
        --description "${description}" \
        --secret-string "DUMMY" \
        --region "${AWS_REGION}" \
        --tags "Key=stack,Value=${STACK_NAME}" \
        --query "ARN" \
        --output text

    echo -e "  ${GREEN}CREATED${NC} ${full_name}"
}

echo "── External API Keys ────────────────────────────────────────────────────"
create_secret "hubspot/api-key"         "HubSpot CRM API key (Bearer token)"
create_secret "instantly/api-key"       "Instantly email platform API key"
create_secret "brave/api-key"           "Brave Search API key for web search"
create_secret "teams/webhook-url"       "Microsoft Teams incoming webhook URL"

echo ""
echo "── Bridge Security ──────────────────────────────────────────────────────"
create_secret "bridge/api-key"          "Bridge API key — agents send this as X-API-Key header"

echo ""
echo "── Companies House ──────────────────────────────────────────────────────"
create_secret "companies-house/api-key" "Companies House API key for UK company verification"

echo ""
echo "── AWS Partner Central / ACE ────────────────────────────────────────────"
create_secret "partner-central/role-arn" "Cross-account IAM role ARN: arn:aws:iam::349440382087:role/CloudiQS-PartnerCentral-MCP"
create_secret "partner-central/catalog"  "Partner Central catalog: AWS (production) or Sandbox (testing)"

echo ""
echo "── Instantly Campaign IDs (one per vertical) ────────────────────────────"
create_secret "instantly/vmware-campaign-id"      "Instantly campaign UUID for VMware vertical"
create_secret "instantly/msp-campaign-id"          "Instantly campaign UUID for MSP vertical"
create_secret "instantly/greenfield-campaign-id"   "Instantly campaign UUID for Greenfield vertical"
create_secret "instantly/startup-campaign-id"      "Instantly campaign UUID for Startup vertical"
create_secret "instantly/storage-campaign-id"      "Instantly campaign UUID for Storage vertical"
create_secret "instantly/smb-campaign-id"          "Instantly campaign UUID for SMB vertical"
create_secret "instantly/education-campaign-id"    "Instantly campaign UUID for Education vertical"
create_secret "instantly/agentbakery-campaign-id"  "Instantly campaign UUID for AgentBakery vertical"
create_secret "instantly/switcher-campaign-id"     "Instantly campaign UUID for Switcher vertical"
create_secret "instantly/awsfunding-campaign-id"   "Instantly campaign UUID for AWS Funding vertical"
create_secret "instantly/security-campaign-id"     "Instantly campaign UUID for Security vertical"

echo ""
echo -e "${GREEN}Done.${NC} All secrets created with DUMMY values."
echo ""
echo "Next steps:"
echo "  1. Update each secret with real values:"
echo "     aws secretsmanager put-secret-value \\"
echo "       --secret-id '${PREFIX}/hubspot/api-key' \\"
echo "       --secret-string 'YOUR_REAL_KEY' \\"
echo "       --region ${AWS_REGION}"
echo ""
echo "  2. Priority order:"
echo "     [ ] bridge/api-key                  — generate a random 32-char key"
echo "     [ ] hubspot/api-key                 — from HubSpot account settings"
echo "     [ ] instantly/api-key               — from Instantly account settings"
echo "     [ ] companies-house/api-key         — from Companies House API portal"
echo "     [ ] teams/webhook-url               — from Teams channel connector"
echo "     [ ] partner-central/role-arn        — see docs/AWS-MCP-SETUP.md"
echo "     [ ] instantly/*-campaign-id (x11)   — create campaigns in Instantly UI first"
echo ""
echo "  3. Generate a bridge API key:"
echo "     python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
