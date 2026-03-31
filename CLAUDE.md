# CloudiQS Engine v7.1

Autonomous AI-powered GTM and operations platform for CloudiQS, an AWS Advanced Consulting Partner (UK). 46 OpenClaw agents find leads, enrich them, manage ACE pipeline, handle replies, and run operations. Bridge API connects agents to HubSpot, Instantly, AWS Partner Central, and Microsoft Teams.

## Architecture

```
OpenClaw agents (cron) -> Bridge API (FastAPI, Docker, port 8787) -> HubSpot + Instantly + ACE + Teams
                                    |
                          Partner Central MCP (us-east-1) -> pipeline insights, customer profiles, funding
```

Live instance: EC2 in eu-west-1. Bridge runs in Docker. Agents run in OpenClaw sandboxes on cron schedules.

## Key directories

- `bridge/app/` - FastAPI bridge (main.py, ace.py, hubspot.py, instantly.py, teams.py, mcp_client.py, config.py, campaign.py, models.py, architect.py)
- `agents/*/SOUL.md` - Agent instructions (47 agents, all complete)
- `scripts/` - deploy helpers (register-cron-jobs.sh, s3-upload-poller.py, generate-souls.py, diagnostics.sh)
- `docs/` - setup guides (SETUP.md, AWS-MCP-SETUP.md, PRE-DEPLOY.md)
- `context/` - CloudiQS GTM knowledge base (ICP, positioning, objections, case studies, pricing, competitors, sow-structure, sow-architectures). Reference when writing SOUL.md files or reviewing outreach quality.
- `.claude/agents/` - Subagents: engine-ops (health checks), agent-builder (new SDR agents), pipeline-reviewer (pre-push checks)
- `.claude/commands/` - Slash commands: /health-check, /new-agent, /pre-deploy
- `.github/workflows/deploy.yml` - GitHub Actions CI/CD via SSM

## Commands

```bash
# Build and run bridge locally
cd bridge && docker compose up --build

# Test bridge health
curl http://localhost:8787/health
curl http://localhost:8787/stats

# Test lead submission
curl -X POST http://localhost:8787/lead -H "Content-Type: application/json" -d '{"email":"test@example.com","company":"Test Ltd","contact":"John Smith","campaign":"msp","signal":"test","pain":"test pain point for validation","play":"MSP","icp_score":7}'

# Test MCP (use Sandbox for testing)
curl -X POST http://localhost:8787/mcp/message -H "Content-Type: application/json" -d '{"message":"Hello, what can you help me with?","catalog":"Sandbox"}'

# Deploy to live instance
STACK_NAME=cloudiqs-engine bash deploy.sh

# Register all cron jobs
bash scripts/register-cron-jobs.sh

# Run diagnostics on live instance
bash scripts/diagnostics.sh

# Regenerate all agent SOUL.md files from data
python3 scripts/generate-souls.py
```

## Conventions

- STACK_NAME env var drives ALL resource naming. No hardcoded bucket names, instance IDs, or account IDs in code.
- Secrets in AWS Secrets Manager under prefix `cloudiqs/STACK_NAME/`. Use `config.get_secret("key")`.
- ACE enum values in ace.py are verified against AWS API docs. Do not change without checking docs first.
- Email style: no contractions ("do not" not "don't"), no em dashes, no "caught my eye", human tone.
- Agents POST leads to `http://localhost:8787/lead`. Bridge handles HubSpot + Instantly + Teams.
- ACE opportunities created ONLY when deal reaches Qualified stage. Never on lead creation.
- MCP proxy endpoints at `/mcp/*` let agents query Partner Central without SigV4 auth.
- SDR agents follow 9-step pipeline: signal search, pick company, Companies House verify, find DM, score ICP (6+ to proceed), write email, POST to bridge, update MEMORY, repeat.

## IMPORTANT: Known risks (all fixed in code, verify on first deploy)

1. **FIXED** MCP SigV4: switched to `requests-aws4auth` in mcp_client.py. Test with `catalog: "Sandbox"` first.
2. **FIXED** Webhook persistence: file-backed JSON at `/data/webhook_events.json`, mounted as Docker named volume `bridge-data`.
3. **FIXED** Cron flag syntax: register-cron-jobs.sh now runs preflight check and aborts if flags do not match `openclaw cron add --help`.
4. **FIXED** Companies House API key: bridge exposes `GET /config/companies-house-key` which reads from Secrets Manager (`companies-house/api-key`). Store the key there. **The key that was in this file has been removed — rotate it at Companies House immediately (it was in git history).**
5. **FIXED** GitHub Actions: deploy.yml now fails fast with a clear message if `AWS_DEPLOY_ROLE_ARN` or `EC2_INSTANCE_ID` secrets are missing. Follow docs/SETUP.md to create the OIDC provider and deploy role.

## Do not

- Do not hardcode AWS account IDs, instance IDs, or bucket names in code
- Do not create ACE opportunities on lead creation (only on Qualified stage)
- Do not auto-send LinkedIn messages or connection requests (human review required)
- Do not guess email addresses. If you cannot verify, stop.
- Do not deploy all 46 agents at once. Test bridge first, then add agents one at a time.
- Do not change ACE enum values in ace.py without checking AWS API docs

## Reference docs

- @README.md - full architecture, agent roster, known issues
- @docs/SETUP.md - GitHub + AWS setup with IAM commands
- @docs/AWS-MCP-SETUP.md - MCP IAM policy, test commands, agent usage
- @docs/PRE-DEPLOY.md - pre-deploy checklist before pushing to EC2
- `infra/secrets-init.sh` - populate Secrets Manager on first deploy
- AWS Partner Central API: https://docs.aws.amazon.com/partner-central/latest/APIReference/
- AWS MCP Server: https://docs.aws.amazon.com/partner-central/latest/APIReference/partner-central-mcp-server.html

## When compacting

Preserve: list of modified files, current test status, any AWS API findings, bridge endpoint changes.
