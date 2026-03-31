# Pre-Deploy Checklist — CloudiQS Engine v7.1

Work through every section in order before running `deploy.sh`.
Each item shows the exact resource name, how to create it, and a verification command.

**Prefix convention:** all Secrets Manager paths below are relative to `cloudiqs/cloudiqs-engine/`.
The full key is therefore `cloudiqs/cloudiqs-engine/<path>`.

---

## 1. AWS Secrets Manager

Run all `aws secretsmanager create-secret` commands from CloudShell on account **736956442878** (eu-west-1).

---

### 1.1 HubSpot

| # | Secret path | Value |
|---|-------------|-------|
| 1 | `hubspot/api-key` | Private app token (starts with `pat-`) |

**How to get it:** HubSpot → Settings → Integrations → Private Apps → Create app → tick `crm.objects.contacts`, `crm.objects.deals` (read + write) → copy token.

```bash
aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/hubspot/api-key" \
  --secret-string "pat-YOUR-KEY-HERE" \
  --region eu-west-1

# Verify the bridge can read it and reach HubSpot
curl -s -H "Authorization: Bearer $(aws secretsmanager get-secret-value \
  --secret-id cloudiqs/cloudiqs-engine/hubspot/api-key \
  --query SecretString --output text --region eu-west-1)" \
  "https://api.hubapi.com/crm/v3/objects/contacts?limit=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('HubSpot OK — contacts:', d.get('total', 'error'))"
```

---

### 1.2 Instantly

One key plus one campaign UUID per SDR vertical (11 campaigns).

| # | Secret path | Value |
|---|-------------|-------|
| 2 | `instantly/api-key` | Instantly API key |
| 3 | `instantly/vmware-campaign-id` | UUID of VMware exit campaign |
| 4 | `instantly/msp-campaign-id` | UUID of MSP campaign |
| 5 | `instantly/greenfield-campaign-id` | UUID of Greenfield campaign |
| 6 | `instantly/startup-campaign-id` | UUID of Startup campaign |
| 7 | `instantly/storage-campaign-id` | UUID of Storage migration campaign |
| 8 | `instantly/smb-campaign-id` | UUID of SMB campaign |
| 9 | `instantly/education-campaign-id` | UUID of Education campaign |
| 10 | `instantly/agentbakery-campaign-id` | UUID of AI/Agent Bakery campaign |
| 11 | `instantly/switcher-campaign-id` | UUID of Switcher campaign |
| 12 | `instantly/awsfunding-campaign-id` | UUID of AWS Funding campaign |
| 13 | `instantly/security-campaign-id` | UUID of Security campaign |

**How to get campaign UUIDs:** Instantly UI → Campaigns → click a campaign → copy the UUID from the URL (`/campaigns/{uuid}`).

```bash
# Store API key
aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/instantly/api-key" \
  --secret-string "YOUR-INSTANTLY-API-KEY" \
  --region eu-west-1

# Store one campaign ID (repeat for all 11 verticals)
aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/instantly/vmware-campaign-id" \
  --secret-string "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" \
  --region eu-west-1

# Verify API key is valid and list campaigns
curl -s -H "Authorization: Bearer $(aws secretsmanager get-secret-value \
  --secret-id cloudiqs/cloudiqs-engine/instantly/api-key \
  --query SecretString --output text --region eu-west-1)" \
  "https://api.instantly.ai/api/v2/campaigns?limit=5" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Instantly OK — campaigns:', len(d.get('items', [])))"
```

---

### 1.3 Microsoft Teams

| # | Secret path | Value |
|---|-------------|-------|
| 14 | `teams/webhook-url` | Incoming webhook URL |

**How to get it:** Teams → channel → … → Connectors → Incoming Webhook → Configure → copy URL.

```bash
aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/teams/webhook-url" \
  --secret-string "https://outlook.office.com/webhook/YOUR-URL-HERE" \
  --region eu-west-1

# Verify by sending a test message
curl -s -X POST "$(aws secretsmanager get-secret-value \
  --secret-id cloudiqs/cloudiqs-engine/teams/webhook-url \
  --query SecretString --output text --region eu-west-1)" \
  -H "Content-Type: application/json" \
  -d '{"text":"CloudiQS Engine pre-deploy test — Teams webhook verified"}' \
  && echo "Teams OK"
```

---

### 1.4 AWS Partner Central (ACE + MCP)

| # | Secret path | Value |
|---|-------------|-------|
| 15 | `partner-central/role-arn` | `arn:aws:iam::349440382087:role/CloudiQS-PartnerCentral-MCP` |
| 16 | `partner-central/catalog` | `Sandbox` for testing, `AWS` for production |

**Note:** Use `Sandbox` until ACE integration has been tested end-to-end. Switch to `AWS` only after verifying opportunity creation does not pollute live pipeline.

```bash
aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/partner-central/role-arn" \
  --secret-string "arn:aws:iam::349440382087:role/CloudiQS-PartnerCentral-MCP" \
  --region eu-west-1

aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/partner-central/catalog" \
  --secret-string "Sandbox" \
  --region eu-west-1

# Verify role assumption works (run from EC2 instance or with instance credentials)
aws sts assume-role \
  --role-arn "$(aws secretsmanager get-secret-value \
    --secret-id cloudiqs/cloudiqs-engine/partner-central/role-arn \
    --query SecretString --output text --region eu-west-1)" \
  --role-session-name pre-deploy-test \
  --query "Credentials.AccessKeyId" --output text \
  && echo "Partner Central role OK"
```

---

### 1.5 Brave Search

| # | Secret path | Value |
|---|-------------|-------|
| 17 | `brave/api-key` | Brave Search API key |

**How to get it:** https://api.search.brave.com/ → Dashboard → API Keys.

```bash
aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/brave/api-key" \
  --secret-string "BSA-YOUR-KEY-HERE" \
  --region eu-west-1

# Verify key works
curl -s -H "Accept: application/json" \
  -H "Accept-Encoding: gzip" \
  -H "X-Subscription-Token: $(aws secretsmanager get-secret-value \
    --secret-id cloudiqs/cloudiqs-engine/brave/api-key \
    --query SecretString --output text --region eu-west-1)" \
  "https://api.search.brave.com/res/v1/web/search?q=CloudiQS+AWS&count=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Brave OK — results:', len(d.get('web',{}).get('results',[])))"
```

---

### 1.6 Companies House

| # | Secret path | Value |
|---|-------------|-------|
| 18 | `companies-house/api-key` | Rotated Companies House API key |

**IMPORTANT:** The key previously stored in `CLAUDE.md` has been in git history and must be treated as compromised. Rotate it now at https://developer.company-information.service.gov.uk before storing the new key here.

```bash
aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/companies-house/api-key" \
  --secret-string "NEW-ROTATED-KEY-HERE" \
  --region eu-west-1

# Verify via bridge endpoint (after deploy)
curl -s http://localhost:8787/config/companies-house-key \
  && echo ""

# Verify directly against Companies House API
curl -s -u "$(aws secretsmanager get-secret-value \
  --secret-id cloudiqs/cloudiqs-engine/companies-house/api-key \
  --query SecretString --output text --region eu-west-1):" \
  "https://api.company-information.service.gov.uk/search/companies?q=Cloudstack&items_per_page=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Companies House OK — items:', d.get('total_results', 'error'))"
```

---

### 1.7 Composio

| # | Secret path | Value |
|---|-------------|-------|
| 19 | `composio/consumer-key` | Composio API key |

**How to get it:** https://app.composio.dev → Settings → API Keys.

```bash
aws secretsmanager create-secret \
  --name "cloudiqs/cloudiqs-engine/composio/consumer-key" \
  --secret-string "YOUR-COMPOSIO-KEY" \
  --region eu-west-1

# Verify (agents call Composio directly, not via bridge)
curl -s -H "X-API-Key: $(aws secretsmanager get-secret-value \
  --secret-id cloudiqs/cloudiqs-engine/composio/consumer-key \
  --query SecretString --output text --region eu-west-1)" \
  "https://backend.composio.dev/api/v1/apps" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Composio OK — apps:', len(d.get('items',[])))"
```

---

## 2. IAM Roles and Policies

### 2.1 EC2 Instance Role

The bridge container runs on the EC2 instance. The instance needs an IAM role with these permissions:

```bash
# Check the instance role exists and has Secrets Manager access
INSTANCE_ROLE=$(aws ec2 describe-instances \
  --instance-ids "$EC2_INSTANCE_ID" \
  --query "Reservations[0].Instances[0].IamInstanceProfile.Arn" \
  --output text --region eu-west-1)
echo "Instance role profile: $INSTANCE_ROLE"

# Verify the role can read secrets
aws iam simulate-principal-policy \
  --policy-source-arn "$(aws iam get-instance-profile \
    --instance-profile-name "$(basename $INSTANCE_ROLE)" \
    --query 'InstanceProfile.Roles[0].Arn' --output text)" \
  --action-names "secretsmanager:GetSecretValue" \
  --resource-arns "arn:aws:secretsmanager:eu-west-1:736956442878:secret:cloudiqs/cloudiqs-engine/*" \
  --query "EvaluationResults[0].EvalDecision" --output text
```

**Minimum permissions the instance role needs:**

```json
{
  "Effect": "Allow",
  "Action": [
    "secretsmanager:GetSecretValue",
    "secretsmanager:DescribeSecret"
  ],
  "Resource": "arn:aws:secretsmanager:eu-west-1:736956442878:secret:cloudiqs/cloudiqs-engine/*"
}
```

```json
{
  "Effect": "Allow",
  "Action": "sts:AssumeRole",
  "Resource": "arn:aws:iam::349440382087:role/CloudiQS-PartnerCentral-MCP"
}
```

```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"],
  "Resource": [
    "arn:aws:s3:::cloudiqs-engine-uploads-736956442878",
    "arn:aws:s3:::cloudiqs-engine-uploads-736956442878/*"
  ]
}
```

---

### 2.2 Partner Central Cross-Account Role

Must exist in account **349440382087**. Full setup in `docs/AWS-MCP-SETUP.md`.

```bash
# Verify the role exists (run from account 736956442878)
aws iam get-role \
  --role-name CloudiQS-PartnerCentral-MCP \
  --profile partner-account 2>/dev/null \
  && echo "Partner Central role exists" \
  || echo "MISSING — see docs/AWS-MCP-SETUP.md"
```

---

### 2.3 GitHub Actions Deploy Role

Must exist in account **736956442878**. Full setup in `docs/SETUP.md`.

```bash
# Verify the role exists
aws iam get-role --role-name github-deploy \
  --query "Role.Arn" --output text \
  && echo "GitHub deploy role exists" \
  || echo "MISSING — run Step 2 in docs/SETUP.md"

# Verify OIDC provider exists
aws iam list-open-id-connect-providers \
  --query "OIDCProviderList[?ends_with(Arn, 'token.actions.githubusercontent.com')]" \
  --output text \
  && echo "OIDC provider exists" \
  || echo "MISSING — run Step 1 in docs/SETUP.md"
```

---

## 3. S3 Bucket

Used by the S3 upload poller and potentially by MCP file uploads.

```bash
STACK_NAME=cloudiqs-engine
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${STACK_NAME}-uploads-${ACCOUNT}"

# Create if it does not exist
aws s3 mb "s3://${BUCKET}" --region eu-west-1 2>/dev/null \
  && echo "Bucket created: $BUCKET" \
  || echo "Bucket already exists (OK): $BUCKET"

# Verify access
aws s3 ls "s3://${BUCKET}/" --region eu-west-1 \
  && echo "S3 bucket accessible" \
  || echo "FAIL — check instance role has s3 permissions"
```

---

## 4. AWS Bedrock Model Access

Agents use three model families. All must be enabled before agents can run.

```bash
# Check enabled models in eu-west-1 (SDR agents)
aws bedrock list-foundation-models \
  --region eu-west-1 \
  --query "modelSummaries[?contains(modelId,'nova-lite') || contains(modelId,'claude-haiku') || contains(modelId,'claude-sonnet')].modelId" \
  --output table
```

**Required models:**

| Model | Region | Used by |
|-------|--------|---------|
| `amazon.nova-lite-v1:0` | eu-west-1 | 12 SDR agents |
| `anthropic.claude-haiku-4-5-20251001-v1:0` | eu-west-1 | Ops, ACE, LinkedIn agents |
| `anthropic.claude-sonnet-4-6` | eu-west-1 | CEO ops, ACE SOW, SEO content |

Enable via: AWS Console → Amazon Bedrock → Model access → Request model access (eu-west-1).

---

## 5. HubSpot CRM Configuration

The bridge writes custom properties to HubSpot deals and contacts. These properties must exist before the first lead is submitted.

### 5.1 Required custom contact properties

| Property internal name | Type | Used for |
|------------------------|------|----------|
| `icp_score` | Number | ICP score (0-10) |
| `signal` | Single-line text | Intent signal that triggered outreach |
| `pain_summary` | Multi-line text | Business pain points |
| `recommended_play` | Multi-line text | Recommended solution play |
| `campaign_vertical` | Single-line text | SDR campaign (vmware, msp, etc.) |
| `companies_house_number` | Single-line text | Companies House registration number |
| `aws_services_deployed` | Multi-line text | Known AWS services in use |
| `li_action_taken` | Boolean checkbox | LinkedIn follow-up done |

### 5.2 Required custom deal properties

| Property internal name | Type | Used for |
|------------------------|------|----------|
| `campaign_vertical` | Single-line text | Which SDR campaign sourced the deal |
| `icp_score` | Number | ICP score at time of creation |
| `signal` | Single-line text | Intent signal |
| `ace_opportunity_id` | Single-line text | ACE opportunity ID (e.g. O1234567) |

**Create via:** HubSpot → Settings → Properties → Create property.

```bash
# After deploy, verify a test contact can be created with custom fields
curl -s -X POST http://localhost:8787/lead \
  -H "Content-Type: application/json" \
  -d '{
    "email": "predeploytest@example-verify.com",
    "company": "Pre-Deploy Test Ltd",
    "contact": "Test User",
    "campaign": "msp",
    "signal": "pre-deploy verification",
    "pain": "Testing custom HubSpot properties exist before going live",
    "play": "MSP",
    "icp_score": 5
  }' | python3 -c "import sys,json; d=json.load(sys.stdin); print('Lead OK — HubSpot contact:', d.get('hubspot_contact_id'), '| deal:', d.get('hubspot_deal_id'))"
```

---

## 6. Instantly Campaign Configuration

Each campaign must be active (not paused) before the bridge tries to enrol leads.

```bash
# After deploy, verify enrolment works end-to-end with the test lead above
# Check bridge logs:
# docker logs cloudiqs-bridge 2>&1 | grep -E "Instantly|enrolled|campaign"

# Or test directly via Instantly API
INSTANTLY_KEY=$(aws secretsmanager get-secret-value \
  --secret-id cloudiqs/cloudiqs-engine/instantly/api-key \
  --query SecretString --output text --region eu-west-1)

VMWARE_CAMPAIGN=$(aws secretsmanager get-secret-value \
  --secret-id cloudiqs/cloudiqs-engine/instantly/vmware-campaign-id \
  --query SecretString --output text --region eu-west-1)

curl -s -H "Authorization: Bearer $INSTANTLY_KEY" \
  "https://api.instantly.ai/api/v2/campaigns/${VMWARE_CAMPAIGN}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Campaign:', d.get('name'), '| Status:', d.get('status'))"
```

---

## 7. Microsoft Teams Channel Setup

| Channel | Purpose |
|---------|---------|
| CloudiQS Engine (general) | Lead notifications, reply alerts |
| Bridge alerts | 500 errors, quota warnings |

Each Teams channel needs its own webhook URL. If you only have one channel, use the same URL for all notifications.

```bash
# After deploy, verify Teams alert fires on a test lead
curl -s -X POST http://localhost:8787/lead \
  -H "Content-Type: application/json" \
  -d '{"email":"teams-test@verify.com","company":"Teams Test","contact":"Test","campaign":"msp","signal":"teams webhook test","pain":"testing teams notifications","play":"MSP","icp_score":6}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('Status:', d.get('status'))"
# Check Teams — a notification card should appear within 30s
```

---

## 8. GitHub Actions Secrets

Configure in the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret name | Value | How to get it |
|-------------|-------|---------------|
| `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::736956442878:role/github-deploy` | Created in Step 2 of `docs/SETUP.md` |
| `EC2_INSTANCE_ID` | `i-0e9301730308aa39b` | AWS EC2 console |

```bash
# Verify both secrets exist using the GitHub CLI
gh secret list --repo cloudiqs/engine | grep -E "AWS_DEPLOY_ROLE_ARN|EC2_INSTANCE_ID"
# Should show both secrets listed
```

---

## 9. OpenClaw Cron Syntax Verification

Before running `register-cron-jobs.sh`, verify the flag syntax on the live instance.

```bash
# Via SSM
aws ssm send-command \
  --instance-ids "$EC2_INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["openclaw cron add --help"]' \
  --query "Command.CommandId" --output text
# Then: aws ssm get-command-invocation --command-id <id> --instance-id <id> --query StandardOutputContent --output text
```

The script already does this automatically and aborts if flags do not match. Read its output carefully on first run.

---

## 10. Post-Deploy Smoke Tests

Run these from the EC2 instance (or via SSM) **after** `deploy.sh` completes.

```bash
# 1. Bridge health
curl -s http://localhost:8787/health
# Expected: {"status":"ok","version":"2.0.0","time":"..."}

# 2. Stats (should reset daily)
curl -s http://localhost:8787/stats
# Expected: {"date":"YYYY-MM-DD","total_leads":0,"duplicates":0,"by_campaign":{}}

# 3. Full lead pipeline (creates real HubSpot contact + Instantly enrolment)
curl -s -X POST http://localhost:8787/lead \
  -H "Content-Type: application/json" \
  -d '{
    "email": "smoketest@example-verify.com",
    "company": "Smoke Test Ltd",
    "contact": "Smoke Tester",
    "campaign": "msp",
    "signal": "post-deploy smoke test",
    "pain": "Smoke testing the full lead pipeline after deployment",
    "play": "MSP",
    "icp_score": 7
  }'
# Expected: {"status":"created","hubspot_contact_id":"<id>","hubspot_deal_id":"<id>","instantly_lead_id":"<id>"}

# 4. MCP message — use Sandbox until Partner Central integration verified
curl -s -X POST http://localhost:8787/mcp/message \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello, what can you help me with?","catalog":"Sandbox"}'
# Expected: {"status":"...","text":"...","sessionId":"..."}

# 5. Webhook event persistence
curl -s -X POST http://localhost:8787/webhook/instantly \
  -H "Content-Type: application/json" \
  -d '{"event_type":"reply","email":"test@test.com","reply_text":"Interested, tell me more","campaign_id":"test"}'
# Expected: {"status":"received","event_type":"reply"}

curl -s "http://localhost:8787/webhook/instantly/recent?limit=1"
# Expected: event appears, and survives a `docker restart cloudiqs-bridge`

# 6. Companies House key accessible
curl -s http://localhost:8787/config/companies-house-key
# Expected: {"api_key":"..."} — NOT the 503 error response

# 7. Bridge logs clean
docker logs cloudiqs-bridge 2>&1 | grep -c "ERROR"
# Expected: 0 (or only known non-critical errors)
```

---

## Checklist Summary

```
SECRETS MANAGER (19 secrets)
[ ] hubspot/api-key
[ ] instantly/api-key
[ ] instantly/vmware-campaign-id
[ ] instantly/msp-campaign-id
[ ] instantly/greenfield-campaign-id
[ ] instantly/startup-campaign-id
[ ] instantly/storage-campaign-id
[ ] instantly/smb-campaign-id
[ ] instantly/education-campaign-id
[ ] instantly/agentbakery-campaign-id
[ ] instantly/switcher-campaign-id
[ ] instantly/awsfunding-campaign-id
[ ] instantly/security-campaign-id
[ ] teams/webhook-url
[ ] partner-central/role-arn
[ ] partner-central/catalog          (start with "Sandbox")
[ ] brave/api-key
[ ] companies-house/api-key          (rotated — old key was in git)
[ ] composio/consumer-key

IAM
[ ] EC2 instance role has secretsmanager:GetSecretValue on cloudiqs/cloudiqs-engine/*
[ ] EC2 instance role has sts:AssumeRole on CloudiQS-PartnerCentral-MCP
[ ] EC2 instance role has s3 read/write on cloudiqs-engine-uploads-* bucket
[ ] Partner Central cross-account role exists in account 349440382087
[ ] GitHub OIDC provider exists in account 736956442878
[ ] github-deploy role exists with SSM send-command permissions

S3
[ ] cloudiqs-engine-uploads-736956442878 bucket exists in eu-west-1

BEDROCK
[ ] amazon.nova-lite-v1:0 enabled in eu-west-1
[ ] anthropic.claude-haiku-4-5-20251001-v1:0 enabled in eu-west-1
[ ] anthropic.claude-sonnet-4-6 enabled in eu-west-1

HUBSPOT
[ ] Private app created with contacts + deals read/write
[ ] Custom contact properties created (icp_score, signal, pain_summary, etc.)
[ ] Custom deal properties created (campaign_vertical, icp_score, signal, ace_opportunity_id)
[ ] Pipeline named "default" exists (or update bridge/app/hubspot.py with your pipeline ID)

INSTANTLY
[ ] API key active (not expired)
[ ] All 11 campaign UUIDs stored in Secrets Manager
[ ] All campaigns are active (not paused or draft)
[ ] Sending domain verified and warmed up
[ ] Instantly webhook pointing to https://<your-domain>/webhook/instantly

TEAMS
[ ] Incoming webhook configured in target channel
[ ] Webhook URL stored in Secrets Manager

GITHUB ACTIONS
[ ] AWS_DEPLOY_ROLE_ARN secret set
[ ] EC2_INSTANCE_ID secret set

OPENCLAW CRON SYNTAX
[ ] Verified --help output on live instance before running register-cron-jobs.sh

POST-DEPLOY SMOKE TESTS
[ ] /health returns 200
[ ] /lead creates real HubSpot contact + deal
[ ] /lead creates real Instantly enrolment
[ ] Teams notification fires
[ ] /mcp/message returns response from Sandbox catalog
[ ] Webhook event survives bridge restart
[ ] /config/companies-house-key returns key (not 503)
[ ] docker logs show 0 ERRORs
```
