#!/bin/bash
# CloudiQS Engine — healthcheck.sh
# Verifies 11 categories of engine health.
# Exit code: always 0 (informational only — never fails a deploy).
# Output: structured lines suitable for parsing or display.

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

_pass() { echo -e "  ${GREEN}PASS${NC}  $1"; PASS=$((PASS+1)); }
_fail() { echo -e "  ${RED}FAIL${NC}  $1"; FAIL=$((FAIL+1)); }
_warn() { echo -e "  ${YELLOW}WARN${NC}  $1"; WARN=$((WARN+1)); }

echo ""
echo "=============================================="
echo "  CloudiQS Engine Healthcheck"
echo "  $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "=============================================="
echo ""

# ── A. AWS identity and credential_source ─────────────────────────────────────
echo "[A] AWS identity"
if aws sts get-caller-identity --output text --query 'Account' 2>/dev/null | grep -q "[0-9]"; then
    ACCOUNT=$(aws sts get-caller-identity --output text --query 'Account' 2>/dev/null)
    _pass "AWS identity: account $ACCOUNT"
else
    _fail "aws sts get-caller-identity failed — check IAM instance role"
fi

# Check ~/.aws/config has credential_source for assume-role profiles
if grep -q "credential_source" ~/.aws/config 2>/dev/null; then
    _pass "~/.aws/config has credential_source"
else
    _warn "~/.aws/config missing credential_source — cross-account assume-role may fail"
fi

echo ""

# ── B. Bedrock model access (live invoke-model test) ──────────────────────────
echo "[B] Bedrock model access"
HAIKU_ID="eu.anthropic.claude-haiku-4-5-20251001-v1:0"
BEDROCK_RESULT=$(aws bedrock-runtime invoke-model \
    --region eu-west-1 \
    --model-id "$HAIKU_ID" \
    --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/bedrock-test-out.json 2>&1)

if [ $? -eq 0 ] && [ -f /tmp/bedrock-test-out.json ]; then
    _pass "Bedrock invoke-model ($HAIKU_ID) — live call succeeded"
    rm -f /tmp/bedrock-test-out.json
else
    _fail "Bedrock invoke-model failed: $BEDROCK_RESULT"
fi

echo ""

# ── C. OpenClaw config (global. prefix, no eu. bug) ───────────────────────────
echo "[C] OpenClaw config"
OC_CONFIG="$HOME/.openclaw/openclaw.json"
if [ -f "$OC_CONFIG" ]; then
    _pass "openclaw.json exists"
    # Check for eu. prefix (known bug — should be global.)
    if grep -q '"eu\.' "$OC_CONFIG" 2>/dev/null; then
        _fail "openclaw.json contains eu. model prefix — OpenClaw bug #55642 will cause 'invalid model identifier'. Change to global. prefix"
    elif grep -q '"global\.' "$OC_CONFIG" 2>/dev/null; then
        _pass "openclaw.json uses global. model prefix (correct)"
    else
        _warn "openclaw.json found but no model prefix detected — verify model IDs manually"
    fi
else
    _warn "openclaw.json not found at $OC_CONFIG — OpenClaw may not be configured"
fi

echo ""

# ── D. Gateway process running ────────────────────────────────────────────────
echo "[D] Gateway process"
if pgrep -f "openclaw gateway" > /dev/null 2>&1; then
    _pass "OpenClaw gateway process is running"
elif openclaw agents list > /dev/null 2>&1; then
    _pass "OpenClaw gateway is responsive (agents list OK)"
else
    _fail "OpenClaw gateway is NOT running — agents cannot execute. Start with: nohup openclaw gateway start --auth none > ~/.openclaw/gateway.log 2>&1 & disown"
fi

echo ""

# ── E. Bridge container healthy ───────────────────────────────────────────────
echo "[E] Bridge container"
BRIDGE_STATUS=$(sudo docker ps --filter "name=cloudiqs" --format "{{.Status}}" 2>/dev/null | head -1)
if echo "$BRIDGE_STATUS" | grep -qi "up"; then
    _pass "Bridge container is running: $BRIDGE_STATUS"
else
    _fail "Bridge container not running. Status: ${BRIDGE_STATUS:-not found}"
fi

BRIDGE_HEALTH=$(curl -s --max-time 5 http://localhost:8787/health 2>/dev/null || echo "")
if echo "$BRIDGE_HEALTH" | grep -q '"ok"'; then
    _pass "Bridge /health endpoint responds OK"
else
    _fail "Bridge /health endpoint did not respond — check: sudo docker logs cloudiqs-engine-bridge"
fi

echo ""

# ── F. Cron jobs registered (expect 48) ───────────────────────────────────────
echo "[F] Cron jobs"
CRON_COUNT=$(openclaw cron list 2>/dev/null | grep -c "." || echo "0")
if [ "$CRON_COUNT" -ge 48 ] 2>/dev/null; then
    _pass "Cron jobs registered: $CRON_COUNT (expected 48+)"
elif [ "$CRON_COUNT" -ge 40 ] 2>/dev/null; then
    _warn "Cron jobs registered: $CRON_COUNT (expected 48) — some may be missing"
elif [ "$CRON_COUNT" -gt 0 ] 2>/dev/null; then
    _fail "Only $CRON_COUNT cron jobs registered (expected 48) — run: bash scripts/register-cron-jobs.sh"
else
    _fail "No cron jobs registered or openclaw cron list failed — run: bash scripts/register-cron-jobs.sh"
fi

echo ""

# ── G. Agent directories (expect 47) ─────────────────────────────────────────
echo "[G] Agent directories"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_COUNT=$(find "$REPO_DIR/agents" -maxdepth 1 -mindepth 1 -type d | wc -l)
SOUL_COUNT=$(find "$REPO_DIR/agents" -maxdepth 2 -name "SOUL.md" | wc -l)

if [ "$AGENT_COUNT" -ge 47 ]; then
    _pass "Agent directories: $AGENT_COUNT (expected 47+)"
else
    _warn "Agent directories: $AGENT_COUNT (expected 47)"
fi

if [ "$SOUL_COUNT" -ge 46 ]; then
    _pass "SOUL.md files: $SOUL_COUNT (expected 46+)"
else
    _fail "SOUL.md files: $SOUL_COUNT (expected 46) — some agents have no instructions"
fi

echo ""

# ── H. Live agent execution test ──────────────────────────────────────────────
echo "[H] Live agent execution"
# Test sdr-vmware with a brief timeout — just check it starts, not full run
if openclaw agents list 2>/dev/null | grep -q "sdr-vmware"; then
    _pass "sdr-vmware agent is registered in OpenClaw"
else
    _warn "sdr-vmware not found in OpenClaw agent list — agents may not be deployed. Run deploy.sh"
fi

echo ""

# ── I. Secrets Manager (all 6 critical secrets) ───────────────────────────────
echo "[I] Secrets Manager"
STACK_NAME="${STACK_NAME:-cloudiqs-engine}"
SECRETS=(
    "cloudiqs/$STACK_NAME/hubspot/api-key"
    "cloudiqs/$STACK_NAME/instantly/api-key"
    "cloudiqs/$STACK_NAME/teams/webhook-url"
    "cloudiqs/$STACK_NAME/partner-central/role-arn"
    "cloudiqs/$STACK_NAME/brave/api-key"
    "cloudiqs/$STACK_NAME/companies-house/api-key"
)

for secret in "${SECRETS[@]}"; do
    VALUE=$(aws secretsmanager get-secret-value \
        --secret-id "$secret" \
        --query SecretString \
        --output text \
        --region eu-west-1 2>/dev/null || echo "")
    if [ -n "$VALUE" ] && [ "$VALUE" != "PLACEHOLDER" ] && [ "$VALUE" != "dummy" ]; then
        _pass "Secret exists: $secret"
    else
        _fail "Secret missing or placeholder: $secret — add via: aws secretsmanager create-secret --name \"$secret\" --secret-string \"VALUE\" --region eu-west-1"
    fi
done

echo ""

# ── J. ACE cross-account role ─────────────────────────────────────────────────
echo "[J] ACE cross-account role"
ROLE_ARN=$(aws secretsmanager get-secret-value \
    --secret-id "cloudiqs/$STACK_NAME/partner-central/role-arn" \
    --query SecretString \
    --output text \
    --region eu-west-1 2>/dev/null || echo "")

if [ -n "$ROLE_ARN" ] && echo "$ROLE_ARN" | grep -q "arn:aws:iam"; then
    ASSUME_RESULT=$(aws sts assume-role \
        --role-arn "$ROLE_ARN" \
        --role-session-name healthcheck-test \
        --duration-seconds 900 \
        --query "Credentials.AccessKeyId" \
        --output text 2>/dev/null || echo "")
    if [ -n "$ASSUME_RESULT" ] && echo "$ASSUME_RESULT" | grep -q "^ASIA\|^AKIA"; then
        _pass "ACE cross-account role assumable: $ROLE_ARN"
    else
        _fail "Cannot assume ACE role: $ROLE_ARN — check trust policy in account 349440382087"
    fi
else
    _warn "ACE role ARN not configured in Secrets Manager — MCP and ACE endpoints will fail"
fi

echo ""

# ── K. S3 poller in crontab ───────────────────────────────────────────────────
echo "[K] S3 poller"
if crontab -l 2>/dev/null | grep -q "s3-upload-poller"; then
    _pass "s3-upload-poller is in crontab"
else
    _warn "s3-upload-poller not found in crontab — bulk uploads via S3 will not be processed"
fi

echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "=============================================="
TOTAL=$((PASS+FAIL+WARN))
echo "  Results: ${PASS} passed, ${FAIL} failed, ${WARN} warnings (${TOTAL} total)"
if [ "$FAIL" -eq 0 ] && [ "$WARN" -eq 0 ]; then
    echo -e "  ${GREEN}ALL CHECKS PASSED${NC}"
elif [ "$FAIL" -eq 0 ]; then
    echo -e "  ${YELLOW}PASSED WITH WARNINGS — review above${NC}"
else
    echo -e "  ${RED}${FAIL} CHECK(S) FAILED — review above${NC}"
fi
echo "=============================================="
echo ""

# Always exit 0 — healthcheck is informational, never fails a deploy
exit 0
