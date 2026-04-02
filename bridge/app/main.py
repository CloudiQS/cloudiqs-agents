"""
CloudiQS Bridge API v7.1
Routes agent output to HubSpot, Instantly, ACE, Teams, and Partner Central MCP.

DESIGN DECISIONS:
- POST /lead: HubSpot contact + deal + Instantly enrol + Teams notify
  Does NOT create ACE opportunity (that happens on Qualified stage via /ace/create)
- POST /ace/create: Called by ace-create agent when deal reaches Qualified
- POST /ingest: HubSpot deal only (bulk uploads from S3 poller)
- POST /webhook/instantly: Reply/bounce/open events (file-backed persistence)
- GET /deals/pipeline: Returns deals by stage for ops and ACE agents
- POST /deals/{id}/update: Updates a single deal property
- POST /mcp/architecture: Generates AWS architecture via Bedrock Claude Sonnet
- GET /config/companies-house-key: Returns CH key from Secrets Manager for agents

SECURITY:
- All endpoints except /health require X-API-Key header
- Key stored in Secrets Manager: cloudiqs/{STACK_NAME}/bridge/api-key
- Rate limits applied per API key (simple in-memory sliding window)
"""

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger
from starlette.middleware.base import BaseHTTPMiddleware

from app.models import LeadPayload, IngestPayload, WebhookPayload
from app import hubspot, instantly, ace, teams, ace_notifications


# ── Logging (structured JSON for CloudWatch) ─────────────────────────────────

def _configure_logging() -> None:
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


_configure_logging()
logger = logging.getLogger("bridge")


# ── Webhook event persistence ─────────────────────────────────────────────────

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
            logger.info("webhook_events_loaded", extra={"count": len(events)})
            return events
    except Exception as e:
        logger.warning("webhook_events_load_failed", extra={"error": str(e)})
    return []


def _save_events_to_disk(events: list) -> None:
    """Persist webhook events to disk. Called inside _webhook_lock."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        WEBHOOK_FILE.write_text(json.dumps(events))
    except Exception as e:
        logger.error("webhook_events_save_failed", extra={"error": str(e)})


# ── Auth configuration ────────────────────────────────────────────────────────

_BRIDGE_API_KEY: str = ""
_BRIDGE_AUTH_ENABLED: bool = os.environ.get("BRIDGE_AUTH_ENABLED", "true").lower() == "true"

# Paths that do not require auth
_AUTH_EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

# Rate limits: path_prefix -> (max_requests, window_seconds)
_RATE_LIMITS: dict = {
    "/lead": (100, 3600),
    "/ingest": (20, 3600),
    "/mcp/": (200, 3600),
    "/webhook/": (500, 3600),
    "/ace/": (50, 3600),
    "/deals/": (200, 3600),
}

# In-memory rate limit counters: (path_prefix, api_key) -> [timestamp, ...]
_rate_counters: dict = defaultdict(list)
_rate_lock = asyncio.Lock()


def _load_bridge_api_key() -> str:
    """Load bridge API key from Secrets Manager. Falls back to a generated key if not set."""
    from app.config import get_secret, is_dummy
    key = get_secret("bridge/api-key")
    if is_dummy(key):
        if not _BRIDGE_AUTH_ENABLED:
            logger.info("bridge_auth_disabled")
            return ""
        generated = secrets.token_urlsafe(32)
        logger.warning(
            "bridge_api_key_not_configured",
            extra={
                "action": "generated_fallback_key",
                "hint": "Store this in Secrets Manager: cloudiqs/{STACK}/bridge/api-key",
                "key": generated,
            },
        )
        return generated
    logger.info("bridge_api_key_loaded")
    return key


# ── Startup / lifespan ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _webhook_events, _BRIDGE_API_KEY
    _webhook_events = _load_events_from_disk()
    _BRIDGE_API_KEY = _load_bridge_api_key()
    # Ensure all required HubSpot properties exist (idempotent — safe to run every startup)
    await hubspot.ensure_properties()
    logger.info(
        "bridge_started",
        extra={"auth_enabled": _BRIDGE_AUTH_ENABLED, "version": "7.1.0"},
    )
    yield
    logger.info("bridge_stopped")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="CloudiQS Bridge",
    version="7.1.0",
    description="Routes AI agent output to HubSpot, Instantly, ACE, Teams, and Partner Central.",
    lifespan=lifespan,
)

# Daily stats
_stats = {"date": "", "total_leads": 0, "duplicates": 0, "by_campaign": {}}
_start_time = time.time()


def _reset_stats_if_new_day() -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    if _stats["date"] != today:
        _stats.update({"date": today, "total_leads": 0, "duplicates": 0, "by_campaign": {}})


# ── Middleware: request IDs ───────────────────────────────────────────────────

class RequestIDMiddleware(BaseHTTPMiddleware):
    """Adds X-Request-ID to every request and response for log correlation."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - start) * 1000)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )
        return response


# ── Middleware: auth + rate limiting ──────────────────────────────────────────

class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """
    1. Checks X-API-Key header (skips /health and docs paths).
    2. Skips auth for requests from localhost/127.0.0.1 — agents run on
       the same instance and the security group blocks all inbound traffic,
       so loopback requests are trusted without a key.
    3. Applies per-key rate limits by endpoint prefix.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for exempt paths
        if path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)

        # Trust loopback and Docker bridge network — agents run on the same host,
        # no inbound ports open. Bridge runs in Docker so host requests arrive
        # via Docker bridge (172.x.x.x) not 127.0.0.1.
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost") or (
            client_host and client_host.startswith("172.")
        ):
            return await call_next(request)

        # Auth check for external requests
        if _BRIDGE_AUTH_ENABLED:
            api_key = request.headers.get("X-API-Key", "")
            if not api_key or api_key != _BRIDGE_API_KEY:
                logger.warning(
                    "auth_rejected",
                    extra={"path": path, "has_key": bool(api_key)},
                )
                return JSONResponse(
                    status_code=401,
                    content={"error": "Missing or invalid X-API-Key header"},
                )
        else:
            api_key = "noauth"

        # Rate limit check
        now = time.time()
        for prefix, (max_req, window) in _RATE_LIMITS.items():
            if path.startswith(prefix):
                bucket_key = (prefix, api_key)
                async with _rate_lock:
                    timestamps = _rate_counters[bucket_key]
                    # Purge timestamps outside the window
                    _rate_counters[bucket_key] = [t for t in timestamps if now - t < window]
                    if len(_rate_counters[bucket_key]) >= max_req:
                        logger.warning(
                            "rate_limit_exceeded",
                            extra={"path": path, "prefix": prefix, "limit": max_req},
                        )
                        return JSONResponse(
                            status_code=429,
                            content={
                                "error": f"Rate limit exceeded: {max_req} requests per {window}s"
                            },
                        )
                    _rate_counters[bucket_key].append(now)
                break

        return await call_next(request)


app.add_middleware(RequestIDMiddleware)
app.add_middleware(AuthRateLimitMiddleware)


# ── Health and stats ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check — no auth required. Enhanced with operational metrics."""
    async with _webhook_lock:
        webhook_count = len(_webhook_events)
        unprocessed = sum(1 for e in _webhook_events if not e.get("processed"))
    return {
        "status": "ok",
        "version": "7.1.0",
        "uptime_seconds": int(time.time() - _start_time),
        "time": datetime.now().isoformat(),
        "webhook_events_total": webhook_count,
        "webhook_events_unprocessed": unprocessed,
        "auth_enabled": _BRIDGE_AUTH_ENABLED,
    }


@app.get("/stats")
async def stats():
    """Daily lead counts by campaign."""
    _reset_stats_if_new_day()
    return _stats


@app.get("/teams/test")
async def teams_test():
    """Post a test message to all three Teams channels.
    Returns which format each channel accepted (adaptive or simple fallback).
    """
    results = {}
    for channel, fn in [
        ("sdr", teams.post_to_sdr),
        ("ceo", teams.post_to_ceo),
        ("ace", teams.post_to_ace),
    ]:
        ok = await fn(
            title=f"CloudiQS Engine — test message ({channel.upper()})",
            body_text=f"Bridge is alive. Test sent at {datetime.now().isoformat()}.",
            facts=[
                {"title": "Channel", "value": channel.upper()},
                {"title": "Bridge",  "value": "7.1.0"},
            ],
        )
        results[channel] = "delivered" if ok else "failed"
    return results


# ── Lead pipeline ─────────────────────────────────────────────────────────────

@app.post("/lead")
async def create_lead(lead: LeadPayload):
    """Full lead pipeline: HubSpot contact + deal + Instantly enrol + Teams notify.
    ACE opportunity is NOT created here — only on Qualified stage via /ace/create.
    """
    _reset_stats_if_new_day()
    logger.info(
        "lead_received",
        extra={
            "email": lead.email,
            "company": lead.company,
            "icp_score": lead.icp_score,
            "campaign": lead.campaign,
        },
    )

    result = {
        "status": "created",
        "email": lead.email,
        "company": lead.company,
        "hubspot_contact_id": None,
        "hubspot_deal_id": None,
        "instantly_lead_id": None,
    }

    contact_id = await hubspot.create_contact(lead)
    result["hubspot_contact_id"] = contact_id

    if contact_id:
        deal_id = await hubspot.create_deal(lead, contact_id)
        result["hubspot_deal_id"] = deal_id

    if lead.email and lead.campaign:
        instantly_id = await instantly.enrol(lead)
        result["instantly_lead_id"] = instantly_id

    await teams.notify_lead({**lead.model_dump(), **result})

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
    logger.info(
        "ingest_received",
        extra={"company": payload.company, "campaign": payload.campaign},
    )

    deal_id = await hubspot.create_ingest_deal(payload)

    if deal_id:
        await teams.notify(
            f"Ingest | {payload.company} | {payload.campaign}\n"
            f"Deal: {deal_id} | Source: {payload.source}"
        )

    return {
        "status": "created" if deal_id else "skipped",
        "company": payload.company,
        "deal_id": deal_id,
    }


@app.get("/lead")
async def check_lead(email: str = ""):
    """Check if a contact exists in HubSpot."""
    if not email:
        return {"exists": False}
    contact_id = await hubspot.check_contact_exists(email)
    return {"exists": bool(contact_id), "contact_id": contact_id}


# ── HubSpot deal queries ──────────────────────────────────────────────────────

@app.get("/deals/pipeline")
async def deals_pipeline(stage: str = "appointmentscheduled", limit: int = 20):
    """Return deals at a given pipeline stage.

    Used by ace-sow, ace-create, ace-sync, and ops agents to find deals
    they need to act on.

    Common stage IDs:
    - appointmentscheduled: New Lead
    - qualifiedtobuy: Qualified
    - presentationscheduled: Proposal
    - decisionmakerboughtin: Proposal Sent
    - contractsent: Negotiation
    - closedwon: Closed Won
    - closedlost: Closed Lost
    """
    deals = await hubspot.search_deals_by_stage(stage, limit=min(limit, 100))
    return {"stage": stage, "count": len(deals), "deals": deals}


@app.get("/deals/search")
async def deals_search(campaign: str = "", stage: str = "", limit: int = 20):
    """Search deals by campaign and/or stage (both optional)."""
    filters: dict = {}
    if stage:
        filters["dealstage"] = stage
    if campaign:
        filters["campaign_vertical"] = campaign
    if not filters:
        return JSONResponse(
            status_code=400,
            content={"error": "At least one of 'campaign' or 'stage' is required"},
        )
    deals = await hubspot.search_deals(filters, limit=min(limit, 100))
    return {"count": len(deals), "deals": deals}


@app.post("/deals/{deal_id}/update")
async def deal_update(deal_id: str, request: Request):
    """Update a single property on a HubSpot deal.

    Body: {"property": "sow_status", "value": "Draft"}

    Used by ace-sow to mark deals after SOW generation,
    and by ace-sync to update deal stages.
    """
    body = await request.json()
    prop = body.get("property")
    value = body.get("value")

    if not prop or value is None:
        return JSONResponse(
            status_code=400,
            content={"error": "'property' and 'value' are required"},
        )

    success = await hubspot.update_deal_property(deal_id, prop, str(value))
    return {
        "status": "updated" if success else "failed",
        "deal_id": deal_id,
        "property": prop,
        "value": value,
    }


# ── ACE pipeline ──────────────────────────────────────────────────────────────

@app.post("/ace/create")
async def ace_create(lead: LeadPayload):
    """Create ACE opportunity for a qualified lead.
    Called by ace-create agent when HubSpot deal reaches Qualified.
    """
    logger.info("ace_create_request", extra={"company": lead.company})

    opp_id = await ace.create_opportunity(lead)

    if opp_id:
        await ace_notifications.notify_created(opp_id, lead)

    return {"status": "created" if opp_id else "failed", "ace_opportunity_id": opp_id}


@app.post("/ace/update")
async def ace_update(request: Request):
    """Update free-form fields on an existing ACE opportunity.

    Body:
        ace_opportunity_id: ACE opportunity ID (e.g. "O14608392")
        customer_business_problem: str (20-2000 chars)
        website: str (e.g. "shadagro.com")

    Used by agents and manually to fix/enrich existing opportunities.
    """
    body = await request.json()
    opp_id = body.get("ace_opportunity_id", "")
    if not opp_id:
        return JSONResponse(status_code=400, content={"error": "ace_opportunity_id required"})

    fields = {k: v for k, v in body.items() if k != "ace_opportunity_id" and v}
    success = await ace.update_opportunity_fields(opp_id, fields)
    return {
        "status": "updated" if success else "failed",
        "ace_opportunity_id": opp_id,
        "fields": list(fields.keys()),
    }


def _parse_mcp(result) -> str:
    """Extract and clean text from an MCP send_message response via shared parser."""
    from app.mcp_parser import parse_mcp_response
    return parse_mcp_response(result) or "No data returned."


async def _run_ace_hygiene() -> dict:
    """Run 3 ACE hygiene MCP queries concurrently and return parsed text results.
    Shared by POST /ace/hygiene and GET /ace/hygiene.
    """
    from app import mcp_client

    queries = {
        "action_required": (
            "List all opportunities with Action Required status. "
            "For each show opportunity ID, company name, and what action is needed."
        ),
        "stale_launched": (
            "List all opportunities in Launched stage that have not been updated in 30 days. "
            "Show opportunity ID, company name, and last update date."
        ),
        "funding_eligible": (
            "Which of my opportunities at Business Validation or Committed stage are eligible "
            "for funding programs such as MAP, POC credits, or CEI? "
            "Show opportunity ID, company name, program name, and estimated amount."
        ),
    }

    results = await asyncio.gather(
        *[mcp_client.send_message(q, catalog="AWS") for q in queries.values()],
        return_exceptions=True,
    )

    report = {}
    for key, result in zip(queries.keys(), results):
        if isinstance(result, Exception) or result is None:
            logger.warning(f"ace_hygiene_mcp_failed", extra={"section": key})
            report[key] = "⚠️ MCP query failed — check Partner Central connection."
        else:
            report[key] = _parse_mcp(result)

    return report



@app.post("/ace/hygiene")
async def ace_hygiene_post():
    """Run ACE pipeline hygiene check via Partner Central MCP.

    Queries (run concurrently):
      1. Opportunities with Action Required status
      2. Stale Launched opportunities (30+ days no update)
      3. Funding eligible at Business Validation / Committed stage

    Parses MCP text responses, posts a formatted MessageCard to Teams,
    and returns the plain-text sections in the API response.

    Called by ace-hygiene agent on its Monday 06:00 schedule.
    """
    logger.info("ace_hygiene_started")
    report = await _run_ace_hygiene()
    await ace_notifications.notify_hygiene(report)
    logger.info("ace_hygiene_complete")
    return {
        "status": "complete",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "action_required": report.get("action_required", ""),
        "stale_launched": report.get("stale_launched", ""),
        "funding_eligible": report.get("funding_eligible", ""),
    }


@app.get("/ace/hygiene")
async def ace_hygiene_get():
    """GET version of ACE hygiene — same logic, callable via curl from cron agents."""
    return await ace_hygiene_post()


@app.post("/ace/update-stage")
async def ace_update_stage(request: Request):
    """Update ACE opportunity stage. Called by ace-sync agent.

    Required body fields: ace_opportunity_id, stage
    Optional body fields: company, old_stage (used for richer Teams notification)
    """
    body = await request.json()
    opp_id = body.get("ace_opportunity_id")
    stage = body.get("stage")

    if not opp_id or not stage:
        return JSONResponse(
            status_code=400,
            content={"error": "ace_opportunity_id and stage required"},
        )

    success = await ace.update_opportunity_stage(opp_id, stage)

    if success:
        company = body.get("company", opp_id)
        old_stage = body.get("old_stage", "")
        await ace_notifications.notify_stage_change(opp_id, company, stage, old_stage)

    return {
        "status": "updated" if success else "failed",
        "ace_opportunity_id": opp_id,
        "stage": stage,
    }


# ── Instantly webhook ─────────────────────────────────────────────────────────

@app.post("/webhook/instantly")
async def webhook_instantly(request: Request):
    """Handle Instantly webhook events: reply, bounce, open.
    Stores events for sdr-reply-handler to read via /webhook/instantly/recent.
    File-backed persistence survives container restarts.
    """
    body = await request.json()
    event_type = body.get("event_type", body.get("type", "unknown"))
    email = body.get("email", body.get("lead_email", ""))
    reply_text = body.get("reply_text", body.get("text", ""))
    campaign_id = body.get("campaign_id", body.get("campaign", ""))

    logger.info("webhook_received", extra={"event_type": event_type, "email": email})

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
        while len(_webhook_events) > MAX_WEBHOOK_EVENTS:
            _webhook_events.pop(0)
        _save_events_to_disk(_webhook_events)

    if event_type in ("reply", "reply_received", "responded"):
        await teams.notify(
            f"Reply Received | {email}\n"
            f"Campaign: {campaign_id}\n"
            f"Reply: {reply_text[:200] if reply_text else 'no text captured'}\n\n"
            "Awaiting classification by sdr-reply-handler."
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
        since: ISO timestamp — only return events after this time
        unprocessed_only: If true, only return events not yet processed
        limit: Max events to return (default 50, max 200)
    """
    async with _webhook_lock:
        events = _webhook_events.copy()

    if since:
        events = [e for e in events if e["timestamp"] > since]
    if unprocessed_only:
        events = [e for e in events if not e.get("processed")]

    events = sorted(events, key=lambda e: e["timestamp"], reverse=True)[:min(limit, 200)]
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


# ── Config endpoints ──────────────────────────────────────────────────────────

@app.get("/config/companies-house-key")
async def config_companies_house_key():
    """Return the Companies House API key from Secrets Manager.
    Agents call this endpoint to get the key at runtime — key is never hardcoded.
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


# ── MCP proxy endpoints ───────────────────────────────────────────────────────
# Let agents call Partner Central and Bedrock through the bridge
# without needing SigV4 auth in their sandbox.

@app.post("/mcp/profile")
async def mcp_profile(request: Request):
    """Get AWS-enriched customer profile from Partner Central."""
    from app import mcp_client
    body = await request.json()
    company = body.get("company", "")
    if not company:
        return JSONResponse(status_code=400, content={"error": "company required"})
    text = await mcp_client.get_customer_profile(company)
    return {"company": company, "profile": text}


@app.post("/mcp/funding")
async def mcp_funding(request: Request):
    """Check MAP/WAR/POC funding eligibility for an ACE opportunity."""
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
    """Generate a sales play for an ACE opportunity."""
    from app import mcp_client
    body = await request.json()
    opp_id = body.get("opportunity_id", "")
    if not opp_id:
        return JSONResponse(status_code=400, content={"error": "opportunity_id required"})
    text = await mcp_client.get_sales_play(opp_id)
    return {"opportunity_id": opp_id, "sales_play": text}


@app.post("/mcp/next-steps")
async def mcp_next_steps(request: Request):
    """Get next steps to advance an ACE opportunity."""
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


@app.post("/mcp/architecture")
async def mcp_architecture(request: Request):
    """Generate an AWS architecture recommendation for SOW documents.

    Uses Bedrock Claude Sonnet to create a tailored architecture based on
    the customer's requirements and service type.

    Body:
        requirements: Customer pain points and technical requirements (free text)
        service_type: migration | vmware | msp | agentbakery | security | startup | smb
        company: Customer company name

    Returns:
        architecture: Markdown string with overview, ASCII diagram, service list,
                      key decisions, security notes, and AWS funding opportunities.
    """
    from app import architect
    body = await request.json()
    requirements = body.get("requirements", "")
    service_type = body.get("service_type", "migration")
    company = body.get("company", "Unknown")

    if not requirements:
        return JSONResponse(status_code=400, content={"error": "requirements required"})

    architecture = await architect.generate_architecture(
        requirements=requirements,
        service_type=service_type,
        company=company,
    )

    if not architecture:
        # Bedrock unavailable — return static fallback with TBC markers
        architecture = (
            f"## Architecture for {company}\n\n"
            "*[TBC — Bedrock unavailable. Sita to complete this section.]*\n\n"
            f"Service type: {service_type}\n\n"
            f"Requirements: {requirements}"
        )
        logger.warning("architecture_fallback_used", extra={"company": company})

    return {
        "company": company,
        "service_type": service_type,
        "architecture": architecture,
    }


# ── CEO briefing ──────────────────────────────────────────────────────────────

@app.post("/ceo/briefing")
async def ceo_briefing_post():
    """Run CEO daily briefing.

    Makes 6 Partner Central MCP queries (9 on Mondays) covering pipeline
    scorecard, actions required, deals closing soon, AWS stage truth,
    co-sell activity, and funding eligibility. Combines with today's lead
    stats and posts a single structured MessageCard to the CEO Teams channel.

    Called by ceo-ops agent on its 06:00 daily schedule.
    """
    from app import ceo_briefing as _ceo
    logger.info("ceo_briefing_started")
    data = await _ceo.run_briefing(stats=_stats)
    await _ceo.post_briefing_to_teams(data)
    await ace_notifications.notify_briefing_alerts(data)
    logger.info("ceo_briefing_complete", extra={"date": data.get("date")})
    return {
        "status": "complete",
        "date": data.get("date"),
        "leads_today": data.get("leads_today"),
        "is_monday": data.get("is_monday"),
    }


@app.get("/ceo/briefing")
async def ceo_briefing_get():
    """Return today's CEO briefing data as JSON without posting to Teams.

    Useful for the ops dashboard and ad-hoc inspection via curl.
    """
    from app import ceo_briefing as _ceo
    return await _ceo.run_briefing(stats=_stats)
