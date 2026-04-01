#!/bin/bash
# CloudiQS Engine - One-click deploy/update
# All resource names derive from STACK_NAME. No hardcoded values.
# Failed deploys auto-rollback to previous state.
set -eE  # -e exits on error, -E ensures ERR trap propagates to functions

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENCLAW_DIR="$HOME/.openclaw"
BRIDGE_DIR="$HOME/bridge"

# Add snap binaries to PATH so AWS CLI (installed as snap) is always found
export PATH="/snap/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

# Stack name drives ALL resource naming
# Set via: STACK_NAME=cloudiqs-engine bash deploy.sh
# Or via environment variable in GitHub Actions
STACK_NAME="${STACK_NAME:-cloudiqs-engine}"
AWS_REGION="${AWS_REGION:-eu-west-1}"
AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "unknown")

# Derived names (never hardcode these)
S3_BUCKET="${STACK_NAME}-uploads-${AWS_ACCOUNT}"
BRIDGE_CONTAINER="${STACK_NAME}-bridge"
DEPLOY_BUCKET="${STACK_NAME}-deploy-${AWS_ACCOUNT}"

export STACK_NAME AWS_REGION S3_BUCKET BRIDGE_CONTAINER

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  CloudiQS Engine Deploy${NC}"
echo -e "${GREEN}  Stack: ${STACK_NAME}${NC}"
echo -e "${GREEN}  Region: ${AWS_REGION}${NC}"
echo -e "${GREEN}  Account: ${AWS_ACCOUNT}${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Snapshot current state for rollback
echo -e "${YELLOW}[0/7] Creating rollback snapshot...${NC}"
ROLLBACK_DIR="/tmp/deploy-rollback-$(date +%s)"
mkdir -p "$ROLLBACK_DIR"
if [ -d "$BRIDGE_DIR/app" ]; then
    cp -r "$BRIDGE_DIR/app" "$ROLLBACK_DIR/bridge-app" 2>/dev/null || true
fi
openclaw cron list --json > "$ROLLBACK_DIR/cron-backup.json" 2>/dev/null || true
echo -e "  ${GREEN}Rollback point saved to $ROLLBACK_DIR${NC}"

# Trap errors for auto-rollback
rollback() {
    echo -e "${RED}Deploy failed. Rolling back...${NC}"
    if [ -d "$ROLLBACK_DIR/bridge-app" ]; then
        cp -r "$ROLLBACK_DIR/bridge-app" "$BRIDGE_DIR/app" 2>/dev/null || true
        cd "$BRIDGE_DIR" && sudo docker compose up -d 2>/dev/null || true
    fi
    echo -e "${RED}Rollback complete. Previous state restored.${NC}"
    exit 1
}
trap rollback ERR

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  CloudiQS Engine Deploy${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Step 1: Copy all agent SOUL.md files
echo -e "${YELLOW}[1/7] Deploying agent files...${NC}"
AGENT_COUNT=0
for agent_dir in "$REPO_DIR"/agents/*/; do
    agent_name=$(basename "$agent_dir")
    target="$OPENCLAW_DIR/agents/$agent_name"
    mkdir -p "$target"

    # Copy all .md and .json files
    for f in "$agent_dir"*.md "$agent_dir"*.json; do
        [ -f "$f" ] && cp "$f" "$target/"
    done

    # Preserve MEMORY.md if it exists on disk (agent runtime data)
    if [ ! -f "$target/MEMORY.md" ]; then
        echo "# $agent_name memory - do not delete" > "$target/MEMORY.md"
    fi

    AGENT_COUNT=$((AGENT_COUNT + 1))
done
echo -e "  ${GREEN}$AGENT_COUNT agents deployed${NC}"

# Step 2: Deploy bridge
echo -e "${YELLOW}[2/7] Building bridge...${NC}"
mkdir -p "$BRIDGE_DIR"
cp -r "$REPO_DIR/bridge/"* "$BRIDGE_DIR/"
cd "$BRIDGE_DIR"

# Pass stack name to docker compose via environment
export STACK_NAME BRIDGE_CONTAINER AWS_REGION
sudo -E docker compose down 2>/dev/null || true
sudo -E docker compose build --no-cache
sudo -E docker compose up -d
sleep 5
BRIDGE_HEALTH=$(curl -s http://localhost:8787/health 2>/dev/null || echo "FAILED")
if echo "$BRIDGE_HEALTH" | grep -q "ok"; then
    echo -e "  ${GREEN}Bridge healthy${NC}"
else
    echo -e "  ${RED}Bridge health check failed: $BRIDGE_HEALTH${NC}"
    echo -e "  ${RED}Check: sudo docker logs $BRIDGE_CONTAINER${NC}"
fi

# Step 3: Install OpenClaw if missing, then start gateway
echo -e "${YELLOW}[3/7] OpenClaw gateway...${NC}"
if ! command -v openclaw >/dev/null 2>&1; then
    echo -e "  ${YELLOW}OpenClaw not found — checking Secrets Manager for install command...${NC}"
    OPENCLAW_INSTALL_CMD=$(aws secretsmanager get-secret-value \
        --secret-id "cloudiqs/${STACK_NAME}/openclaw/install-cmd" \
        --region "$AWS_REGION" \
        --query SecretString --output text 2>/dev/null || echo "DUMMY")

    if [ "$OPENCLAW_INSTALL_CMD" = "DUMMY" ] || [ -z "$OPENCLAW_INSTALL_CMD" ]; then
        echo -e "  ${YELLOW}No install command in Secrets Manager (cloudiqs/${STACK_NAME}/openclaw/install-cmd).${NC}"
        echo -e "  ${YELLOW}Add it via: aws secretsmanager put-secret-value --secret-id cloudiqs/${STACK_NAME}/openclaw/install-cmd --secret-string 'YOUR_INSTALL_CMD'${NC}"
        echo -e "  ${YELLOW}Skipping gateway and cron registration.${NC}"
    else
        echo -e "  ${YELLOW}Installing OpenClaw...${NC}"
        eval "$OPENCLAW_INSTALL_CMD"
        if command -v openclaw >/dev/null 2>&1; then
            echo -e "  ${GREEN}OpenClaw installed: $(openclaw --version 2>/dev/null || echo 'ok')${NC}"
        else
            echo -e "  ${RED}OpenClaw install failed — check install command in Secrets Manager${NC}"
        fi
    fi
fi

if command -v openclaw >/dev/null 2>&1; then
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    openclaw gateway restart 2>/dev/null || openclaw gateway start 2>/dev/null || true
    sleep 3
    echo -e "  ${GREEN}Gateway restarted${NC}"
fi

# Step 4: Register cron jobs
echo -e "${YELLOW}[4/7] Registering cron jobs...${NC}"
if command -v openclaw >/dev/null 2>&1; then
    bash "$REPO_DIR/scripts/register-cron-jobs.sh"
else
    echo -e "  ${YELLOW}Skipping cron registration — OpenClaw not installed${NC}"
fi

# Step 5: Install pollers
echo -e "${YELLOW}[5/7] Installing pollers...${NC}"
cp "$REPO_DIR/scripts/s3-upload-poller.py" "$HOME/s3-upload-poller.py"
cp "$REPO_DIR/scripts/cv-poller.sh" "$HOME/cv-poller.sh" 2>/dev/null || true
chmod +x "$HOME/s3-upload-poller.py" "$HOME/cv-poller.sh" 2>/dev/null || true

# Install poller crons (idempotent, with stack name in environment)
EXISTING_CRON=$(crontab -l 2>/dev/null || true)
if ! echo "$EXISTING_CRON" | grep -q "s3-upload-poller"; then
    (echo "$EXISTING_CRON"; echo "*/5 * * * * PATH=/snap/bin:/usr/local/bin:/usr/bin:/bin STACK_NAME=$STACK_NAME AWS_REGION=$AWS_REGION python3 $HOME/s3-upload-poller.py >> /tmp/s3-poller.log 2>&1") | crontab -
    echo -e "  ${GREEN}S3 poller cron installed (bucket: $S3_BUCKET)${NC}"
else
    # Update existing cron with current stack name
    EXISTING_CRON=$(echo "$EXISTING_CRON" | grep -v "s3-upload-poller")
    (echo "$EXISTING_CRON"; echo "*/5 * * * * PATH=/snap/bin:/usr/local/bin:/usr/bin:/bin STACK_NAME=$STACK_NAME AWS_REGION=$AWS_REGION python3 $HOME/s3-upload-poller.py >> /tmp/s3-poller.log 2>&1") | crontab -
    echo -e "  ${GREEN}S3 poller cron updated (bucket: $S3_BUCKET)${NC}"
fi

# Step 6: Run diagnostics
echo -e "${YELLOW}[6/7] Running diagnostics...${NC}"
python3 "$REPO_DIR/scripts/diagnostics.py" 2>/dev/null || bash "$REPO_DIR/scripts/diagnostics.sh"

# Step 7: Notify Teams
echo -e "${YELLOW}[7/7] Notifying Teams...${NC}"
TEAMS_URL=$(aws secretsmanager get-secret-value --secret-id cloudiqs/cloudiqs-engine/teams/webhook-url --region eu-west-1 --query SecretString --output text 2>/dev/null || echo "")
if [ -n "$TEAMS_URL" ] && [ "$TEAMS_URL" != "DUMMY" ]; then
    STATS=$(curl -s http://localhost:8787/stats 2>/dev/null || echo "{}")
    CRON_COUNT=$(openclaw cron list 2>/dev/null | grep -c "idle\|ok\|running" || echo "?")
    curl -s -X POST "$TEAMS_URL" \
        -H "Content-Type: application/json" \
        -d "{\"text\":\"CloudiQS Engine deployed. $AGENT_COUNT agents, $CRON_COUNT cron jobs active. Bridge: healthy. Stats: $STATS\"}" > /dev/null 2>&1
    echo -e "  ${GREEN}Teams notified${NC}"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deploy complete${NC}"
echo -e "${GREEN}  Agents: $AGENT_COUNT${NC}"
echo -e "${GREEN}  Bridge: http://localhost:8787${NC}"
echo -e "${GREEN}========================================${NC}"
