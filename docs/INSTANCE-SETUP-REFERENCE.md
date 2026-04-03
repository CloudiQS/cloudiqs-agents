# CloudiQS Engine — Instance Setup Reference

Quick reference for the live EC2 instance (eu-west-1). Use alongside docs/PRE-DEPLOY.md.

## OpenClaw

**Version in use:** v2026.3.7 (pinned — do NOT upgrade)

**Known bug (#59079):** v2026.3.31 blocks agent exec approvals when cron jobs run
through the gateway. Fixed by:
1. Staying on v2026.3.7
2. Adding `--local` flag to every `openclaw cron add` command

The `--local` flag bypasses gateway exec approval routing. All cron jobs in
`scripts/register-cron-jobs.sh` include `--local` in the `add_job()` function.

**Model prefix:** Always use `global.` prefix (not `eu.`, `us.`, `jp.`).
Bug #55642 strips regional prefixes. The scripts/register-cron-jobs.sh uses
the correct `global.` prefix throughout.

**Gateway startup:**
```bash
nohup openclaw gateway start --port 18789 > /var/log/openclaw-gateway.log 2>&1 &
```
Use `nohup`, NOT `screen` or `systemd`. Never restart the gateway in deploy.sh.

## Bridge

Port: 8787 (loopback only — 127.0.0.1:8787)
Container: cloudiqs-bridge
Compose file: bridge/docker-compose.yml

```bash
cd bridge && docker compose up -d --build
docker logs cloudiqs-bridge --tail=50
```

## DynamoDB

Live table: `cloudiqs-agent-log`
Default in code: `{STACK_NAME}-agent-log` = `cloudiqs-engine-agent-log`

Override via env var in docker-compose.yml:
```
KNOWLEDGE_TABLE=cloudiqs-agent-log
```
This is already set in bridge/docker-compose.yml.

## Key env vars (set in docker-compose.yml)

| Variable | Value | Purpose |
|----------|-------|---------|
| STACK_NAME | cloudiqs-engine | Resource naming prefix |
| AWS_DEFAULT_REGION | eu-west-1 | Bridge AWS region |
| DATA_DIR | /data | Webhook persistence volume |
| KNOWLEDGE_TABLE | cloudiqs-agent-log | DynamoDB table override |

## Cron registration

After deploy, always re-register cron jobs:
```bash
bash scripts/register-cron-jobs.sh
```
This clears all existing jobs first, then registers all 48. The script includes
`--local` flag on all `openclaw cron add` commands.

## Secrets Manager prefix

All secrets at: `cloudiqs/cloudiqs-engine/` in eu-west-1.
See docs/PRE-DEPLOY.md for the full list of 19 required secrets.
