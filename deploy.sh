#!/bin/bash
# CloudiQS Engine - Deploy script
# ONLY does: git pull, copy agent SOUL.md files, rebuild bridge, health check.
# NEVER touches the OpenClaw gateway or cron jobs.
# Gateway runs in a screen session — leave it alone.
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENCLAW_DIR="$HOME/.openclaw"

STACK_NAME="${STACK_NAME:-cloudiqs-engine}"
AWS_REGION="${AWS_REGION:-eu-west-1}"
export STACK_NAME AWS_REGION

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  CloudiQS Engine Deploy${NC}"
echo -e "${GREEN}  Stack: ${STACK_NAME}${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Step 1: git pull
echo -e "${YELLOW}[1/3] Pulling latest code...${NC}"
cd "$REPO_DIR"
git pull origin main
echo -e "  ${GREEN}Code up to date${NC}"

# Step 2: Copy agent SOUL.md files
# Only copies SOUL.md, TOOLS.md, AGENTS.md — never overwrites MEMORY.md
echo -e "${YELLOW}[2/3] Deploying agent files...${NC}"
AGENT_COUNT=0
for agent_dir in "$REPO_DIR"/agents/*/; do
    agent_name=$(basename "$agent_dir")
    target="$OPENCLAW_DIR/agents/$agent_name"
    mkdir -p "$target"

    for f in SOUL.md TOOLS.md AGENTS.md; do
        if [ -f "$agent_dir$f" ]; then
            cp "$agent_dir$f" "$target/$f"
        fi
    done

    # Create MEMORY.md if it does not exist (never overwrite — agent runtime data)
    if [ ! -f "$target/MEMORY.md" ]; then
        echo "# $agent_name memory" > "$target/MEMORY.md"
    fi

    AGENT_COUNT=$((AGENT_COUNT + 1))
done
echo -e "  ${GREEN}$AGENT_COUNT agent files deployed${NC}"

# Step 3: Rebuild bridge container
echo -e "${YELLOW}[3/3] Rebuilding bridge...${NC}"
BRIDGE_DIR="$REPO_DIR/bridge"
cd "$BRIDGE_DIR"
export STACK_NAME AWS_REGION
sudo -E docker compose down
sudo -E docker compose build --no-cache
sudo -E docker compose up -d
sleep 5

# Health check — fail the deploy if bridge does not respond
BRIDGE_HEALTH=$(curl -s --max-time 10 http://localhost:8787/health 2>/dev/null || echo "FAILED")
if echo "$BRIDGE_HEALTH" | grep -q '"ok"'; then
    echo -e "  ${GREEN}Bridge healthy${NC}"
else
    echo -e "${RED}Bridge health check failed: $BRIDGE_HEALTH${NC}"
    echo -e "${RED}Check logs: sudo docker logs cloudiqs-engine-bridge${NC}"
    exit 1
fi

# Step 4: Run healthcheck (informational — never fails deploy)
echo -e "${YELLOW}[4/4] Running healthcheck...${NC}"
bash "$REPO_DIR/scripts/healthcheck.sh" || true

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deploy complete${NC}"
echo -e "${GREEN}  Agents: $AGENT_COUNT SOUL.md files copied${NC}"
echo -e "${GREEN}  Bridge: http://localhost:8787${NC}"
echo -e "${GREEN}  Gateway: untouched (cron jobs still running)${NC}"
echo -e "${GREEN}========================================${NC}"
