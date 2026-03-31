# CloudiQS Engine v7.1

Autonomous AI-powered GTM pipeline for CloudiQS. 46 agents running on OpenClaw, orchestrated via event bus, deployed to AWS EC2 via GitHub Actions.

## Architecture

```
GitHub (this repo)
    |
    v  (push to main)
GitHub Actions
    |
    v  (aws ssm send-command)
EC2 Instance (eu-west-1, account 736956442878)
    |
    +-- OpenClaw Gateway (port 18789)
    |     +-- 46 AI agents on cron schedules
    |     +-- Cognee knowledge graph (shared memory)
    |     +-- Event bus (agent-to-agent coordination)
    |
    +-- Bridge API (port 8787, Docker)
    |     +-- POST /lead      -> HubSpot + Instantly + Teams
    |     +-- POST /ingest    -> HubSpot + Teams (bulk upload)
    |     +-- POST /event     -> Event bus publish
    |     +-- GET  /stats     -> Daily lead counts
    |     +-- GET  /health    -> Health check
    |     +-- POST /webhook/instantly -> Reply/bounce/open events
    |
    +-- S3 Poller (cron, every 5 min)
    |     +-- s3://cloudiqs-engine-uploads/ -> bridge /ingest
    |     +-- s3://cloudiqs-engine-uploads/cvs/ -> recruit-agent
    |
    +-- Bedrock (Nova Lite for SDR, Haiku for ops, Sonnet for expert)
    +-- HubSpot CRM (pipeline: CloudiQS Engine)
    +-- Instantly (email campaigns per vertical)
    +-- AWS Partner Central (ACE opportunities)
    +-- Microsoft Teams (notifications)
```

## Agent Roster (46 agents)

### SDR Agents (18) - Lead generation and outreach
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| sdr-vmware | 07:00 + 13:00 | Nova Lite | VMware/Broadcom exit leads |
| sdr-msp | 07:30 | Nova Lite | Managed services leads |
| sdr-greenfield | 08:00 | Nova Lite | New-to-AWS leads |
| sdr-startup | 08:05 | Nova Lite | Funded startup leads |
| sdr-storage | 08:15 | Nova Lite | On-prem storage migration |
| sdr-smb | 08:30 | Nova Lite | General UK SMB leads |
| sdr-education | 09:00 | Nova Lite | Education sector leads |
| sdr-agentbakery | 09:15 | Nova Lite | AI/GenAI buyer leads |
| sdr-switcher | 09:30 | Nova Lite | AWS partner switcher leads |
| sdr-awsfunding | 09:45 | Nova Lite | AWS funding eligible leads |
| sdr-security | 10:15 | Nova Lite | Cloud security leads |
| sdr-reply-handler | Every 2h | Haiku | Classify Instantly replies |
| sdr-nurture | 11:30 | Haiku | Re-engage cold leads |
| sdr-enrichment | 06:30 | Haiku | Enrich new leads with data |
| sdr-scoring | 06:45 | Haiku | Re-score leads against ICP |
| sdr-signal-tracker | 06:00 | Nova Lite | Monitor intent signals |
| sdr-linkedin | 11:00 | Haiku | LinkedIn warm follow-up |
| sdr-caller | Manual | Sonnet | Warm outbound calls |

### ACE Agents (6) - AWS Partner Central
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| ace-create | Event-driven | Haiku | Create ACE on Qualified stage |
| ace-hygiene | Mon 06:00 | Haiku | Weekly ACE cleanup |
| ace-sync | Every 2h | Haiku | HubSpot <> ACE sync |
| ace-ao-handler | Every 4h | Haiku | Inbound AWS leads |
| ace-funding | Event-driven | Sonnet | Funding package prep |
| ace-sow | Event-driven | Sonnet | SOW from template |

### Account Management (4)
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| sdr-aws-am | 10:00 | Haiku | AM outreach from target list |
| sdr-account-intel | Event-driven | Sonnet | Deep research for qualified |
| sdr-multi-thread | Event-driven | Haiku | Multi-persona per account |
| am-client-monitor | Weekly | Haiku | Upsell signals |

### Ops (9)
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| ceo-ops | 06:00 daily | Sonnet | Morning briefing + orchestration |
| ops-crm-hygiene | Weekly | Haiku | CRM data quality |
| ops-pipeline-report | Mon 07:00 | Haiku | Pipeline dashboard |
| ops-forecast | Mon 07:30 | Haiku | Revenue forecast |
| ops-customer-health | Weekly | Haiku | Churn risk scoring |
| ops-competitor-watch | Weekly | Nova Lite | Competitor monitoring |
| ops-inbox-triage | 08:00 daily | Haiku | Email classification |
| ops-meeting-notes | Event-driven | Sonnet | Call summary + actions |
| ops-finance | Weekly | Haiku | Invoice + MRR tracking |

### Marketing/SEO (3)
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| seo-content | Tue/Thu | Sonnet | Blog drafts + keywords |
| seo-monitor | Weekly | Nova Lite | Ranking tracker |
| seo-social | Mon/Wed/Fri | Haiku | LinkedIn posts |

### LinkedIn Prospecting (1)
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| linkedin-prospect | 10:30 daily | Haiku | Cold outreach via Ana account |

### Infrastructure (2)
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| aws-security-agent | 06:00 | Sonnet | Security posture scan |
| aws-devops-agent | 06:45 | Sonnet | Infrastructure health |

### Recruiting (1)
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| recruit-agent | Tue/Thu | Haiku | LinkedIn candidate sourcing |

### Dashboard (1)
| Agent | Schedule | Model | Purpose |
|-------|----------|-------|---------|
| ops-dashboard | 10:45 | Haiku | Agent health + metrics |

## Quick Start

### Prerequisites
- AWS account 736956442878 with Bedrock models enabled
- GitHub repo with Actions secrets configured
- HubSpot API key (Starter plan minimum)
- Instantly API key with active campaigns
- Teams webhook URL

### Deploy
```bash
git clone git@github.com:cloudiqs/engine.git
cd engine
bash deploy.sh
```

### GitHub Actions (automated)
Push to main branch triggers deployment via SSM.

## Configuration

### Secrets (AWS Secrets Manager)
All secrets stored in `cloudiqs/cloudiqs-engine/` prefix:
- `hubspot/api-key`
- `instantly/api-key`
- `instantly/*-campaign-id` (per vertical)
- `brave/api-key`
- `teams/webhook-url`
- `partner-central/role-arn`
- `composio/consumer-key`

### Environment
- Instance: EC2 in eu-west-1
- OpenClaw: v2026.3.13
- Bridge: FastAPI in Docker on port 8787
- Gateway: port 18789 (loopback only)

## Development

### Adding a new agent
1. Create `agents/agent-name/SOUL.md`
2. Add cron entry to `scripts/register-cron-jobs.sh`
3. Add Teams channel if needed
4. Push to main - auto deploys

### Fix workbook
See `docs/fix-workbook.md` for all 70+ tracked fixes across v6.0 to v7.0.

## Known Issues (fix via Claude Code)

| Issue | File | Impact | Status |
|-------|------|--------|--------|
| 40 placeholder SOUL.md | agents/*/SOUL.md | Agents have no instructions | HIGH - write all 40 following sdr-vmware pattern |
| MCP SigV4 signing | bridge/app/mcp_client.py | httpx may invalidate signature | HIGH - test against real endpoint, may need requests-aws4auth |
| MCP SSE streaming | bridge/app/mcp_client.py | Response may be SSE not JSON | HIGH - add SSE handling if needed |
| cloudformation.yml | infra/ | No infrastructure-as-code | MEDIUM - port from v6.4 |
| fix-workbook.md | docs/ | No fix history | MEDIUM - port from v6.4 |
| MCP session expiry | bridge/app/mcp_client.py | Sessions expire after 48h | LOW - add timestamp check |
| ~~ace.py stub~~ | ~~bridge/app/ace.py~~ | ~~Fixed~~ | DONE - full implementation with verified enums |
| ~~register-cron-jobs.sh~~ | ~~scripts/~~ | ~~Fixed~~ | DONE - 46 agents registered |
| ~~webhook event storage~~ | ~~bridge/app/main.py~~ | ~~Fixed~~ | DONE - /webhook/instantly/recent endpoint |

## Philosophy
"AI handles the volume, humans handle the relationships."
- Agents do research, sourcing, scoring, outreach, and follow-up
- Humans step in at qualification, discovery calls, and closing
- ACE opportunities created only after human qualification
- No email sent without agent quality check
- All data flows through HubSpot as single source of truth
