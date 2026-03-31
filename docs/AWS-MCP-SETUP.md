# AWS Partner Central MCP Setup

The Partner Central MCP Server gives your agents direct access to:
- Customer profiles with AWS intelligence (active customer signal)
- Pipeline insights (at-risk deals, stage analysis, closed-lost patterns)
- Funding eligibility checks AND fund request creation (MAP, POC, CEI)
- Sales play generation per opportunity
- Solution matching against your registered solutions
- Opportunity progression via document upload
- Next step recommendations per deal

## Prerequisites

- CloudiQS must be migrated to the new Partner Central experience in AWS Console
- Cross-account IAM role from engine account (736956442878) to partner account (349440382087)
- us-east-1 region access (MCP server is only in N. Virginia)

## Step 1: Update the cross-account IAM role

Run in CloudShell on account 349440382087:

```bash
# Replace the existing policy with full MCP + funding + marketplace access
aws iam put-role-policy \
  --role-name CloudiQS-PartnerCentral-MCP \
  --policy-name PartnerCentralAgentsFullAccess \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "partnercentral:UseSession",
          "partnercentral:List*",
          "partnercentral:Get*",
          "partnercentral:CreateOpportunity",
          "partnercentral:UpdateOpportunity",
          "partnercentral:SubmitOpportunity",
          "partnercentral:AssignOpportunity",
          "partnercentral:AssociateOpportunity",
          "partnercentral:DisassociateOpportunity",
          "partnercentral:CreateResourceSnapshot",
          "partnercentral:CreateResourceSnapshotJob",
          "partnercentral:StartResourceSnapshotJob",
          "partnercentral:CreateEngagement",
          "partnercentral:CreateEngagementInvitation",
          "partnercentral:RejectEngagementInvitation",
          "partnercentral:StartEngagementByAcceptingInvitationTask",
          "partnercentral:StartEngagementFromOpportunityTask",
          "partnercentral:CreateBenefitApplication",
          "partnercentral:UpdateBenefitApplication",
          "partnercentral:SubmitBenefitApplication",
          "partnercentral:AmendBenefitApplication",
          "partnercentral:CancelBenefitApplication",
          "partnercentral:RecallBenefitApplication",
          "partnercentral:AssociateBenefitApplicationResource",
          "partnercentral:DisassociateBenefitApplicationResource"
        ],
        "Resource": "*"
      },
      {
        "Effect": "Allow",
        "Action": [
          "aws-marketplace:DescribeEntity",
          "aws-marketplace:DescribeAgreement",
          "aws-marketplace:SearchAgreements",
          "aws-marketplace:ListEntities"
        ],
        "Resource": "*"
      }
    ]
  }'

echo "IAM policy updated with full MCP + funding + marketplace access"
```

## Step 2: Test the MCP connection

Via SSM on the engine instance:

```bash
curl -s -X POST http://localhost:8787/mcp/message \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, what can you help me with?", "catalog": "Sandbox"}'
```

If you get a response with agent capabilities listed, the MCP is working.

## Step 3: Test customer profile (AWS signal detection)

```bash
curl -s -X POST http://localhost:8787/mcp/profile \
  -H "Content-Type: application/json" \
  -d '{"company": "easyJet"}'
```

The response will include industry, business model, geographic presence,
company size, and AWS AI insights about the company.

## Step 4: Test funding eligibility

```bash
curl -s -X POST http://localhost:8787/mcp/funding \
  -H "Content-Type: application/json" \
  -d '{"opportunity_id": "O17655287"}'
```

## What agents use MCP for

| Agent | MCP Query | Purpose |
|-------|-----------|---------|
| sdr-enrichment | Customer profile | AWS customer signal before outreach |
| ace-create | Solution match + next steps | Correct solution + field validation |
| ace-funding | Funding eligibility + create application | Automated MAP/POC fund requests |
| ace-hygiene | Pipeline insights + next steps | Weekly cleanup and prioritisation |
| ceo-ops | Pipeline insights + closed-lost analysis | Morning briefing intelligence |
| ops-meeting-notes | Opportunity progression | Upload transcripts, auto-populate fields |

## Bridge MCP proxy endpoints

Agents call MCP through the bridge (no SigV4 in sandbox):

| Endpoint | Method | Body | Purpose |
|----------|--------|------|---------|
| /mcp/profile | POST | {"company": "name"} | Customer profile |
| /mcp/funding | POST | {"opportunity_id": "O..."} | Funding check |
| /mcp/pipeline | POST | {"query": "..."} | Pipeline insights |
| /mcp/sales-play | POST | {"opportunity_id": "O..."} | Sales strategy |
| /mcp/next-steps | POST | {"opportunity_id": "O..."} | Next actions |
| /mcp/message | POST | {"message": "...", "catalog": "AWS"} | Any query |

## Security notes

- Sessions expire after 48 hours
- All write operations require human-in-the-loop approval
- File uploads go to an ephemeral S3 bucket (not retained)
- Use Sandbox catalog for testing, AWS for production
- SigV4 auth handled by bridge, not by individual agents
