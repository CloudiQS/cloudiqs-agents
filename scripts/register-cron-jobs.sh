#!/bin/bash
# Register all 46 agent cron jobs in OpenClaw.
# Clears ALL existing jobs first to prevent duplicates.
# Run via deploy.sh or manually.
set -e

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Models (global inference profiles)
NOVA="global.amazon.nova-lite-v1:0"
HAIKU="global.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET="global.anthropic.claude-sonnet-4-6"

echo -e "${YELLOW}[1/3] Clearing existing cron jobs...${NC}"

# Get all job IDs and remove them
JOB_IDS=$(openclaw cron list --json 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    jobs = data if isinstance(data, list) else data.get('jobs', data.get('entries', []))
    for j in jobs:
        jid = j.get('id', '')
        if jid:
            print(jid)
except:
    pass
" 2>/dev/null || true)

REMOVED=0
for jid in $JOB_IDS; do
    openclaw cron rm "$jid" --yes 2>/dev/null && REMOVED=$((REMOVED + 1)) || true
done
echo -e "  Removed $REMOVED existing jobs"

# Verify clean
REMAINING=$(openclaw cron list 2>/dev/null | grep -cE "idle|ok|running|error" || echo "0")
if [ "$REMAINING" != "0" ] 2>/dev/null; then
    echo -e "  ${YELLOW}Warning: $REMAINING jobs still remain. Continuing anyway.${NC}"
fi

echo -e "${YELLOW}[2/3] Registering 46 agents...${NC}"

REGISTERED=0
FAILED=0

add_job() {
    local NAME="$1"
    local SCHEDULE="$2"
    local TZ="$3"
    local AGENT="$4"
    local MODEL="$5"
    local MESSAGE="$6"
    local TIMEOUT="${7:-1800}"

    if openclaw cron add \
        --name "$NAME" \
        --schedule "$SCHEDULE" \
        --tz "$TZ" \
        --agent "$AGENT" \
        --model "$MODEL" \
        --timeout "$TIMEOUT" \
        --message "$MESSAGE" 2>/dev/null; then
        echo -e "  ${GREEN}+${NC} $NAME"
        REGISTERED=$((REGISTERED + 1))
    else
        echo -e "  ${RED}x${NC} $NAME (failed)"
        FAILED=$((FAILED + 1))
    fi
}

# ── Signal + Enrichment (pre-SDR) ─────────────────────────────────
echo "Signal and enrichment..."

add_job "sdr-signal-tracker-daily" "0 6 * * 1-5" "Europe/London" "sdr-signal-tracker" "$NOVA" \
    "Run daily signal scan. Search for UK companies showing cloud migration intent, VMware pain, AWS hiring, funding rounds. Update HubSpot with intent signals."

add_job "sdr-enrichment-daily" "30 6 * * 1-5" "Europe/London" "sdr-enrichment" "$HAIKU" \
    "Enrich unenriched leads in HubSpot. Run Companies House verification, AWS customer signal via MCP profile, DNS check, job posting analysis. Update aws_customer field."

add_job "sdr-scoring-daily" "45 6 * * 1-5" "Europe/London" "sdr-scoring" "$HAIKU" \
    "Re-score all New Lead stage deals against ICP criteria. Update icp_score in HubSpot. Flag leads below threshold for removal."

# ── SDR Hunt Agents ────────────────────────────────────────────────
echo "SDR hunt agents..."

add_job "sdr-vmware-morning" "0 7 * * 1-5" "Europe/London" "sdr-vmware" "$NOVA" \
    "Run VMware Exit SDR hunt. Find UK SMBs with Broadcom/VMware pain. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-msp-daily" "30 7 * * 1-5" "Europe/London" "sdr-msp" "$NOVA" \
    "Run Managed Services SDR hunt. Find UK SMBs running AWS without an MSP partner. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-greenfield-daily" "0 8 * * 1-5" "Europe/London" "sdr-greenfield" "$NOVA" \
    "Run Greenfield SDR hunt. Find UK SMBs with zero or minimal AWS footprint ready for migration. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-startup-daily" "5 8 * * 1-5" "Europe/London" "sdr-startup" "$NOVA" \
    "Run Startup SDR hunt. Find UK funded startups scaling infrastructure needing AWS. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-storage-daily" "15 8 * * 1-5" "Europe/London" "sdr-storage" "$NOVA" \
    "Run Storage Migration SDR hunt. Find UK companies running NetApp or Dell on-prem storage that could migrate to AWS FSxN. Score ICP, enrich, craft email, POST to bridge."

add_job "sdr-smb-daily" "30 8 * * 1-5" "Europe/London" "sdr-smb" "$NOVA" \
    "Run general UK SMB SDR hunt. Find UK businesses 50-200 employees that need cloud services. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-education-daily" "0 9 * * 1-5" "Europe/London" "sdr-education" "$NOVA" \
    "Run Education sector SDR hunt. Find UK schools, universities, MATs, EdTech companies needing AWS. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-agentbakery-daily" "15 9 * * 1-5" "Europe/London" "sdr-agentbakery" "$NOVA" \
    "Run AI Agent Bakery SDR hunt. Find UK companies exploring GenAI, AI agents, LLMs, or automation. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-switcher-daily" "30 9 * * 1-5" "Europe/London" "sdr-switcher" "$NOVA" \
    "Run AWS Partner Switcher SDR hunt. Find UK companies unhappy with current AWS partner or MSP. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-awsfunding-daily" "45 9 * * 1-5" "Europe/London" "sdr-awsfunding" "$NOVA" \
    "Run AWS Funding SDR hunt. Find UK companies eligible for MAP, POC credits, or CEI funding. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-security-daily" "15 10 * * 1-5" "Europe/London" "sdr-security" "$NOVA" \
    "Run Cloud Security SDR hunt. Find UK companies with security gaps, compliance needs, or MSSP requirements. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

add_job "sdr-vmware-afternoon" "0 13 * * 1-5" "Europe/London" "sdr-vmware" "$NOVA" \
    "Run VMware Exit SDR afternoon hunt. Find UK SMBs with Broadcom/VMware pain. Score ICP, enrich, craft email, POST to bridge at http://localhost:8787/lead."

# ── Account Management ─────────────────────────────────────────────
echo "Account management..."

add_job "sdr-aws-am-daily" "0 10 * * 1-5" "Europe/London" "sdr-aws-am" "$HAIKU" \
    "Run AM outreach from target list. Research assigned accounts, find contacts, draft personalised outreach. POST to bridge at http://localhost:8787/lead."

# ── Reply Handling + Nurture ───────────────────────────────────────
echo "Reply handling and nurture..."

add_job "sdr-reply-handler-morning" "0 7 * * 1-5" "Europe/London" "sdr-reply-handler" "$HAIKU" \
    "Check for Instantly replies. Classify each reply (positive, question, objection, not now, unsubscribe). Update HubSpot. Notify Teams for positive replies."

add_job "sdr-reply-handler-midday" "0 12 * * 1-5" "Europe/London" "sdr-reply-handler" "$HAIKU" \
    "Check for Instantly replies. Classify each reply. Update HubSpot. Notify Teams for positive replies."

add_job "sdr-reply-handler-afternoon" "0 16 * * 1-5" "Europe/London" "sdr-reply-handler" "$HAIKU" \
    "Check for Instantly replies. Classify each reply. Update HubSpot. Notify Teams for positive replies."

add_job "sdr-nurture-daily" "30 11 * * 1-5" "Europe/London" "sdr-nurture" "$HAIKU" \
    "Pick up leads tagged not-now or gone cold (no activity 14+ days). Run re-engagement sequence: value content, case studies, industry news. Update HubSpot."

# ── LinkedIn ───────────────────────────────────────────────────────
echo "LinkedIn..."

add_job "sdr-linkedin-daily" "0 11 * * 1-5" "Europe/London" "sdr-linkedin" "$HAIKU" \
    "Run LinkedIn warm outreach. Pull HubSpot contacts where email opened 3+ times AND no reply AND li_action_taken=false. Research via LinkedIn. Take one action per contact (connect/comment/like). Max 10 actions. Update HubSpot."

add_job "linkedin-prospect-daily" "30 10 * * 1-5" "Europe/London" "linkedin-prospect" "$HAIKU" \
    "Run LinkedIn cold prospecting via Ana account. Research ICP-matching contacts. Draft connection requests. Post drafts to Teams for human review. Max 10 per day."

# ── Digest ─────────────────────────────────────────────────────────
echo "Digest..."

add_job "sdr-digest-daily" "30 10 * * 1-5" "Europe/London" "sdr-digest" "$NOVA" \
    "Compile today's SDR activity digest. Count leads found, enrolled, bounced, replied across all verticals. Check bridge API at http://localhost:8787/stats. Post summary to Teams."

# ── ACE Agents ─────────────────────────────────────────────────────
echo "ACE agents..."

add_job "ace-create-check" "0 8 * * 1-5" "Europe/London" "ace-create" "$HAIKU" \
    "Check HubSpot for deals at Qualified stage without ace_opportunity_id. For each, pull all deal data and POST to http://localhost:8787/ace/create. Update HubSpot with the opportunity ID."

add_job "ace-create-check-pm" "0 14 * * 1-5" "Europe/London" "ace-create" "$HAIKU" \
    "Afternoon check for new Qualified deals needing ACE opportunities."

add_job "ace-sync-morning" "30 7 * * 1-5" "Europe/London" "ace-sync" "$HAIKU" \
    "Sync HubSpot deal stages to ACE. For deals with ace_opportunity_id, compare HubSpot stage to ACE stage. Update ACE if HubSpot has progressed. Update HubSpot if ACE has feedback."

add_job "ace-sync-afternoon" "30 14 * * 1-5" "Europe/London" "ace-sync" "$HAIKU" \
    "Afternoon ACE sync check."

add_job "ace-hygiene-weekly" "0 6 * * 1" "Europe/London" "ace-hygiene" "$HAIKU" \
    "Weekly ACE cleanup. Find stale opportunities (no update 30+ days), missing close dates, Action Required status, approaching deadlines. Post cleanup report to Teams."

add_job "ace-ao-handler-check" "0 9 * * 1-5" "Europe/London" "ace-ao-handler" "$HAIKU" \
    "Check for inbound AWS Originated opportunities via ListEngagementInvitations. Research the company, score ICP, create HubSpot deal, accept invitation. Notify Teams."

add_job "ace-funding-weekly" "30 8 * * 1" "Europe/London" "ace-funding" "$SONNET" \
    "Weekly funding scan. Check all Committed stage opportunities for MAP, POC, CEI eligibility via MCP. Create fund request drafts for eligible opportunities. Post to Teams."

add_job "ace-sow-check" "0 10 * * 1-5" "Europe/London" "ace-sow" "$SONNET" \
    "Check for deals at Proposal stage needing SOW. Pull deal data from HubSpot and ACE, fill CloudiQS SOW template, drop to OneDrive, notify Teams."

# ── Infrastructure Agents ──────────────────────────────────────────
echo "Infrastructure agents..."

add_job "aws-security-daily" "0 6 * * 1-5" "Europe/London" "aws-security-agent" "$SONNET" \
    "Daily security posture scan. Check IAM, Security Hub, GuardDuty findings. Post critical findings to Teams."

add_job "aws-devops-daily" "45 6 * * 1-5" "Europe/London" "aws-devops-agent" "$SONNET" \
    "Daily infrastructure health check. Check EC2 status, ECS services, RDS health, billing anomalies. Post issues to Teams."

# ── Ops Agents ─────────────────────────────────────────────────────
echo "Ops agents..."

add_job "ceo-ops-briefing" "0 6 * * 1-5" "Europe/London" "ceo-ops" "$SONNET" \
    "Morning briefing. Check pipeline status, agent health, bridge health, HubSpot stages, stale deals, ACE status, MCP insights. Post comprehensive briefing to Teams."

add_job "ops-crm-hygiene-weekly" "0 7 * * 1" "Europe/London" "ops-crm-hygiene" "$HAIKU" \
    "Weekly CRM cleanup. Find duplicate contacts, missing fields, stale deals, incorrect stages in HubSpot. Fix what can be fixed, flag the rest to Teams."

add_job "ops-pipeline-report-weekly" "0 7 * * 1" "Europe/London" "ops-pipeline-report" "$HAIKU" \
    "Weekly pipeline report. Pull deal counts by stage, conversion rates, deal velocity, stuck deals. Post dashboard summary to Teams."

add_job "ops-forecast-weekly" "30 7 * * 1" "Europe/London" "ops-forecast" "$HAIKU" \
    "Weekly revenue forecast. Pull pipeline data, apply weighted probability by stage, compare actual vs predicted. Post forecast to Teams."

add_job "ops-customer-health-weekly" "0 8 * * 1" "Europe/London" "ops-customer-health" "$HAIKU" \
    "Weekly customer health check. Score existing MSP clients on churn risk based on support tickets, engagement, AWS spend trends. Flag at-risk accounts to Teams."

add_job "ops-competitor-watch-weekly" "0 9 * * 1" "Europe/London" "ops-competitor-watch" "$NOVA" \
    "Weekly competitor scan. Monitor rival AWS partners for new wins, hires, competencies, pricing changes. Post intel summary to Teams."

add_job "ops-inbox-triage-daily" "0 8 * * 1-5" "Europe/London" "ops-inbox-triage" "$HAIKU" \
    "Daily email triage. Classify incoming emails (urgent, delegate, archive, respond). Draft responses for urgent items. Post daily digest to Teams."

add_job "ops-dashboard-daily" "45 10 * * 1-5" "Europe/London" "ops-dashboard" "$HAIKU" \
    "Daily engine health dashboard. Check all agent statuses, lead counts, error rates, API costs. Post metrics to Teams."

# ── Marketing / SEO ────────────────────────────────────────────────
echo "Marketing and SEO..."

add_job "seo-content-tue" "0 9 * * 2" "Europe/London" "seo-content" "$SONNET" \
    "Tuesday content session. Research trending AWS/cloud/AI topics. Draft one blog post for cloudiqs.com with SEO keywords. Post draft to Teams for review."

add_job "seo-content-thu" "0 9 * * 4" "Europe/London" "seo-content" "$SONNET" \
    "Thursday content session. Draft one blog post or case study. Post to Teams for review."

add_job "seo-monitor-weekly" "0 8 * * 1" "Europe/London" "seo-monitor" "$NOVA" \
    "Weekly SEO monitoring. Track ranking positions for target keywords. Flag drops. Check competitor content. Post report to Teams."

add_job "seo-social-mwf" "0 9 * * 1,3,5" "Europe/London" "seo-social" "$HAIKU" \
    "Draft LinkedIn post for CloudiQS company page and Steve's profile. Align to blog content calendar. Post draft to Teams for review. Never auto-publish."

# ── Recruiting ─────────────────────────────────────────────────────
echo "Recruiting..."

add_job "recruit-agent-tue" "45 10 * * 2" "Europe/London" "recruit-agent" "$HAIKU" \
    "Tuesday recruiting. Source 5 candidates per open role from LinkedIn. Score each. Draft connection request. Post to Teams for human review. Never auto-send."

add_job "recruit-agent-thu" "45 10 * * 4" "Europe/London" "recruit-agent" "$HAIKU" \
    "Thursday recruiting. Source 5 candidates per open role from LinkedIn. Score each. Draft connection request. Post to Teams for human review. Never auto-send."

# ── Client Management ──────────────────────────────────────────────
echo "Client management..."

add_job "am-client-monitor-weekly" "0 9 * * 1" "Europe/London" "am-client-monitor" "$HAIKU" \
    "Weekly client monitoring. Check existing clients for expansion signals: increased AWS spend, new projects, team growth, contract renewals. Flag upsell opportunities to Teams."

echo ""
echo -e "${YELLOW}[3/3] Verifying...${NC}"

TOTAL=$(openclaw cron list 2>/dev/null | grep -cE "idle|ok|running|error" || echo "0")
echo -e "  Registered: ${GREEN}$REGISTERED${NC}"
echo -e "  Failed: ${RED}$FAILED${NC}"
echo -e "  Total active: $TOTAL"

if [ "$REGISTERED" -ge 40 ]; then
    echo -e "  ${GREEN}Cron registration complete${NC}"
else
    echo -e "  ${RED}Warning: expected 46+ jobs but got $REGISTERED${NC}"
fi

echo ""
echo "NOTE: sdr-caller is NOT cron-triggered."
echo "  Trigger manually: openclaw message --agent sdr-caller 'call [NAME] at [COMPANY]'"
echo ""
echo "NOTE: ace-funding and ace-sow are event-driven but also have weekly/daily checks."
echo "NOTE: ops-meeting-notes is event-driven only (no cron)."
echo "NOTE: sdr-account-intel and sdr-multi-thread are event-driven only (no cron)."
