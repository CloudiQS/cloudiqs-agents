"""
CloudiQS Bridge API v2.
Routes agent output to HubSpot, Instantly, ACE, and Teams.

DESIGN DECISIONS:
- POST /lead creates HubSpot contact + deal + Instantly enrol + Teams notify
  It does NOT create an ACE opportunity (that happens on Qualified stage)
- POST /ace/create is called by ace-create agent when deal reaches Qualified
- POST /ingest creates a HubSpot deal only (no Instantly, no ACE) for bulk uploads
- POST /webhook/instantly handles reply/bounce/open events from Instantly
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.models import LeadPayload, IngestPayload, WebhookPayload
from app import hubspot, instantly, ace, teams

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bridge")

# Persistent storage for webhook events
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
WEBHOOK_FILE = DATA_DIR / "webhook_events.json"
MAX_WEBHOOK_EVENTS = 200

_webhook_lock = asyncio.Lock()
_webhook_events: list = []


def _load_events_from_disk() -> list:
    """Load persisted webhook events from disk. Called once at startup."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if WEBHOOK_FILE.exists():
            events = json.loads(WEBHOOK_FILE.read_text())
            logger.info(f"Loaded {len(events)} webhook events from {WEBHOOK_FILE}")
            return events
    except Exception as e:
        logger.warning(f"Could not load webhook events from disk: {e}")
    return []


def _save_events_to_disk(events: list) -> None:
    """Persist webhook events to disk. Called inside _webhook_lock."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        WEBHOOK_FILE.write_text(json.dumps(events))
    except Exception as e:
        logger.error(f"Could not persist webhook events: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _webhook_events
    _webhook_events = _load_events_from_disk()
    yield


app = FastAPI(title="CloudiQS Bridge", version="7.1.0", lifespan=lifespan)

# Daily stats
_stats = {"date": "", "total_leads": 0, "duplicates": 0, "by_campaign": {}}


def _reset_stats_if_new_day():
    today = datetime.now().strftime("%Y-%m-%d")
    if _stats["date"] != today:
        _stats["date"] = today
        _stats["total_leads"] = 0
        _stats["duplicates"] = 0
        _stats["by_campaign"] = {}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "time": datetime.now().isoformat()}


@app.get("/stats")
async def stats():
    _reset_stats_if_new_day()
    return _stats


@app.post("/lead")
async def create_lead(lead: LeadPayload):
    """Full lead pipeline: HubSpot contact + deal + Instantly + Teams.
    NO ACE creation here. ACE happens on Qualified stage via /ace/create.
    """
    _reset_stats_if_new_day()
    logger.info(f"Lead: {lead.email} | {lead.company} | ICP={lead.icp_score} | {lead.campaign}")

    result = {
        "status": "created",
        "email": lead.email,
        "company": lead.company,
        "hubspot_contact_id": None,
        "hubspot_deal_id": None,
        "instantly_lead_id": None,
    }

    # 1. HubSpot contact
    contact_id = await hubspot.create_contact(lead)
    result["hubspot_contact_id"] = contact_id

    # 2. HubSpot deal (associated with contact)
    if contact_id:
        deal_id = await hubspot.create_deal(lead, contact_id)
        result["hubspot_deal_id"] = deal_id

    # 3. Instantly enrolment (only if email and campaign exist)
    if lead.email and lead.campaign:
        instantly_id = await instantly.enrol(lead)
        result["instantly_lead_id"] = instantly_id

    # 4. Teams notification
    await teams.notify_lead({**lead.dict(), **result})

    # 5. Stats
    _stats["total_leads"] += 1
    camp = lead.campaign or "unknown"
    _stats["by_campaign"][camp] = _stats["by_campaign"].get(camp, 0) + 1

    return result


@app.post("/ingest")
async def ingest(payload: IngestPayload):
    """Bulk ingest: HubSpot deal only. No Instantly, no ACE.
    Used by S3 poller and manual uploads.
    """
    _reset_stats_if_new_day()
    logger.info(f"Ingest: {payload.company} | {payload.campaign}")

    deal_id = await hubspot.create_ingest_deal(payload)

    if deal_id:
        await teams.notify(
            f"**Ingest** | {payload.company} | {payload.campaign}\n"
            f"Deal: {deal_id} | Source: {payload.source}"
        )

    return {"status": "created" if deal_id else "skipped", "company": payload.company, "deal_id": deal_id}


@app.post("/ace/create")
async def ace_create(lead: LeadPayload):
    """Create ACE opportunity for a qualified lead.
    Called by ace-create agent when HubSpot deal reaches Qualified.
    Reads all fields from the lead payload (which ace-create pulls from HubSpot).
    """
    logger.info(f"ACE create request: {lead.company}")

    opp_id = await ace.create_opportunity(lead)

    if opp_id:
        await teams.notify(
            f"**ACE Opportunity Created** | {lead.company}\n"
            f"Opportunity: {opp_id} | Campaign: {lead.campaign}"
        )

    return {"status": "created" if opp_id else "failed", "ace_opportunity_id": opp_id}


@app.post("/ace/update-stage")
async def ace_update_stage(request: Request):
    """Update ACE opportunity stage. Called by ace-sync agent."""
    body = await request.json()
    opp_id = body.get("ace_opportunity_id")
    stage = body.get("stage")

    if not opp_id or not stage:
        return JSONResponse(status_code=400, content={"error": "ace_opportunity_id and stage required"})

    success = await ace.update_opportunity_stage(opp_id, stage)
    return {"status": "updated" if success else "failed", "ace_opportunity_id": opp_id, "stage": stage}


@app.post("/webhook/instantly")
async def webhook_instantly(request: Request):
    """Handle Instantly webhook events: reply, bounce, open.
    Stores events for sdr-reply-handler to read via /webhook/instantly/recent.
    """
    body = await request.json()
    event_type = body.get("event_type", body.get("type", "unknown"))
    email = body.get("email", body.get("lead_email", ""))
    reply_text = body.get("reply_text", body.get("text", ""))
    campaign_id = body.get("campaign_id", body.get("campaign", ""))

    logger.info(f"Instantly webhook: {event_type} | {email}")

    # Store the event for sdr-reply-handler (file-backed, survives restarts)
    event = {
        "event_type": event_type,
        "email": email,
        "reply_text": reply_text,
        "campaign_id": campaign_id,
        "timestamp": datetime.now().isoformat(),
        "processed": False,
        "raw": body,
    }
    async with _webhook_lock:
        _webhook_events.append(event)
        # Trim to max size (drop oldest)
        while len(_webhook_events) > MAX_WEBHOOK_EVENTS:
            _webhook_events.pop(0)
        _save_events_to_disk(_webhook_events)

    # Notify Teams for replies
    if event_type in ("reply", "reply_received", "responded"):
        await teams.notify(
            f"**Reply Received** | {email}\n"
            f"Campaign: {campaign_id}\n"
            f"Reply: {reply_text[:200] if reply_text else 'no text captured'}\n\n"
            f"Awaiting classification by sdr-reply-handler."
        )

    return {"status": "received", "event_type": event_type}


@app.get("/webhook/instantly/recent")
async def webhook_instantly_recent(
    since: str = "",
    unprocessed_only: bool = True,
    limit: int = 50,
):
    """Get recent Instantly webhook events.
    Called by sdr-reply-handler to find replies needing classification.

    Args:
        since: ISO timestamp, only return events after this time
        unprocessed_only: if true, only return events not yet processed
        limit: max events to return
    """
    async with _webhook_lock:
        events = _webhook_events.copy()

    if since:
        events = [e for e in events if e["timestamp"] > since]

    if unprocessed_only:
        events = [e for e in events if not e.get("processed")]

    # Most recent first
    events = sorted(events, key=lambda e: e["timestamp"], reverse=True)[:limit]

    return {"events": events, "total": len(events)}


@app.post("/webhook/instantly/mark-processed")
async def webhook_mark_processed(request: Request):
    """Mark webhook events as processed by sdr-reply-handler."""
    body = await request.json()
    timestamps = body.get("timestamps", [])
    count = 0
    async with _webhook_lock:
        for event in _webhook_events:
            if event["timestamp"] in timestamps:
                event["processed"] = True
                count += 1
        if count:
            _save_events_to_disk(_webhook_events)
    return {"marked": count}


@app.get("/config/companies-house-key")
async def config_companies_house_key():
    """Return the Companies House API key from Secrets Manager.
    Agents call this endpoint to get the key rather than needing it in env.
    Secret path: cloudiqs/{STACK_NAME}/companies-house/api-key
    """
    from app.config import get_secret, is_dummy
    key = get_secret("companies-house/api-key")
    if is_dummy(key):
        return JSONResponse(
            status_code=503,
            content={"error": "Companies House API key not configured in Secrets Manager"},
        )
    return {"api_key": key}


@app.get("/lead")
async def check_lead(email: str = ""):
    """Check if a contact exists in HubSpot."""
    if not email:
        return {"exists": False}
    contact_id = await hubspot.check_contact_exists(email)
    return {"exists": bool(contact_id), "contact_id": contact_id}


# ── MCP Proxy Endpoints ───────────────────────────────────────────────
# These let agents call Partner Central MCP through the bridge
# instead of needing direct SigV4 auth in their sandbox

@app.post("/mcp/profile")
async def mcp_profile(request: Request):
    """Get AWS-enriched customer profile. Detects AWS customer signal."""
    from app import mcp_client
    body = await request.json()
    company = body.get("company", "")
    if not company:
        return JSONResponse(status_code=400, content={"error": "company required"})
    text = await mcp_client.get_customer_profile(company)
    return {"company": company, "profile": text}


@app.post("/mcp/funding")
async def mcp_funding(request: Request):
    """Check funding eligibility for an ACE opportunity."""
    from app import mcp_client
    body = await request.json()
    opp_id = body.get("opportunity_id", "")
    if not opp_id:
        return JSONResponse(status_code=400, content={"error": "opportunity_id required"})
    text = await mcp_client.check_funding_eligibility(opp_id)
    return {"opportunity_id": opp_id, "funding": text}


@app.post("/mcp/pipeline")
async def mcp_pipeline(request: Request):
    """Get pipeline insights from Partner Central agent."""
    from app import mcp_client
    body = await request.json()
    query = body.get("query", "Which opportunities need my attention this week?")
    text = await mcp_client.get_pipeline_insights(query)
    return {"query": query, "insights": text}


@app.post("/mcp/sales-play")
async def mcp_sales_play(request: Request):
    """Generate a sales play for an opportunity."""
    from app import mcp_client
    body = await request.json()
    opp_id = body.get("opportunity_id", "")
    if not opp_id:
        return JSONResponse(status_code=400, content={"error": "opportunity_id required"})
    text = await mcp_client.get_sales_play(opp_id)
    return {"opportunity_id": opp_id, "sales_play": text}


@app.post("/mcp/next-steps")
async def mcp_next_steps(request: Request):
    """Get next steps to advance an opportunity."""
    from app import mcp_client
    body = await request.json()
    opp_id = body.get("opportunity_id", "")
    if not opp_id:
        return JSONResponse(status_code=400, content={"error": "opportunity_id required"})
    text = await mcp_client.get_next_steps(opp_id)
    return {"opportunity_id": opp_id, "next_steps": text}


@app.post("/mcp/message")
async def mcp_message(request: Request):
    """Send any natural language query to Partner Central agent."""
    from app import mcp_client
    body = await request.json()
    message = body.get("message", "")
    catalog = body.get("catalog", "AWS")
    if not message:
        return JSONResponse(status_code=400, content={"error": "message required"})
    result = await mcp_client.send_message(message, catalog=catalog)
    return result or {"error": "MCP request failed"}
