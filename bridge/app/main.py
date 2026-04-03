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

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger
from starlette.middleware.base import BaseHTTPMiddleware

from app.models import LeadPayload, IngestPayload, WebhookPayload
from app import hubspot, instantly, ace, teams, ace_notifications, ace_customer_lookup, ace_control_plane, knowledge


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


@app.get("/healthcheck")
async def healthcheck():
    """Run scripts/healthcheck.sh and return structured JSON results.
    Parses PASS/FAIL/WARN lines from the script output.
    """
    import subprocess
    import re

    script = Path(__file__).parent.parent.parent / "scripts" / "healthcheck.sh"
    if not script.exists():
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "healthcheck.sh not found"},
        )

    try:
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout

        checks = []
        passed = failed = warned = 0
        for line in output.splitlines():
            m = re.match(r"\s*(PASS|FAIL|WARN)\s+(.+)", re.sub(r"\x1b\[[0-9;]*m", "", line))
            if m:
                status, msg = m.group(1), m.group(2)
                checks.append({"status": status, "message": msg})
                if status == "PASS":
                    passed += 1
                elif status == "FAIL":
                    failed += 1
                else:
                    warned += 1

        overall = "ok" if failed == 0 else "degraded"
        return {
            "status": overall,
            "passed": passed,
            "failed": failed,
            "warned": warned,
            "checks": checks,
            "time": datetime.now().isoformat(),
        }
    except subprocess.TimeoutExpired:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "healthcheck timed out after 120s"},
        )
    except Exception as exc:
        logger.error("healthcheck_error", extra={"error": str(exc)})
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(exc)},
        )


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
async def create_lead(lead: LeadPayload, background_tasks: BackgroundTasks):
    """Full lead pipeline: HubSpot contact + deal + Instantly enrol + Teams notify.
    Also writes a profile to S3 and logs a lead_created event to DynamoDB.
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

    # ── Knowledge base (fire-and-forget, never delays response) ──────────
    slug = knowledge.slugify(lead.company)
    full_profile = {**lead.model_dump(), **result}
    agent_name = getattr(lead, "agent", "") or "manual"

    background_tasks.add_task(
        knowledge.save_profile,
        slug,
        full_profile,
    )
    background_tasks.add_task(
        knowledge.log_event,
        slug,
        "lead_created",
        agent_name,
        f"New lead: {lead.company} | ICP {lead.icp_score}/10 | {camp.upper()}",
        {"icp_score": lead.icp_score, "email": lead.email},
        lead.campaign,
    )

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


# ── Knowledge base / research endpoints ──────────────────────────────────────

@app.get("/research/profile/{company_slug}")
async def research_profile(company_slug: str):
    """Return the stored S3 profile for a company slug.

    Used by SDR agents to check if research is already done before repeating it.
    Returns the profile dict with a 'profile_age_days' field added.
    Returns 404 if no profile exists.

    Example:
        curl http://localhost:8787/research/profile/uk-tote-group
    """
    profile = await asyncio.to_thread(knowledge.get_profile, company_slug)
    if not profile:
        return JSONResponse(status_code=404, content={"error": "profile not found"})

    # Add age in days so agents can decide whether to refresh
    saved_at = profile.get("saved_at", "")
    if saved_at:
        try:
            from datetime import timezone as _tz
            saved_dt = datetime.fromisoformat(saved_at)
            if saved_dt.tzinfo is None:
                saved_dt = saved_dt.replace(tzinfo=_tz.utc)
            age_days = (datetime.now(_tz.utc) - saved_dt).days
            profile["profile_age_days"] = age_days
        except Exception:
            profile["profile_age_days"] = None

    return profile


@app.get("/research/slug/{company_name}")
async def research_slug(company_name: str):
    """Convert a company name to its knowledge base slug.

    Useful for agents that need to construct a slug from a name.
    Example:
        curl 'http://localhost:8787/research/slug/UK%20Tote%20Group'
        → {"company_name": "UK Tote Group", "slug": "uk-tote-group"}
    """
    return {
        "company_name": company_name,
        "slug": knowledge.slugify(company_name),
    }


@app.get("/research/events/{company_slug}")
async def research_events(company_slug: str, limit: int = 20):
    """Return the most recent events for a company slug.

    Used by agents to check the history of actions taken on a company.
    """
    events = await asyncio.to_thread(knowledge.get_events, company_slug)
    return {"company_slug": company_slug, "count": len(events), "events": events[:limit]}


@app.post("/research/event")
async def research_event(request: Request, background_tasks: BackgroundTasks):
    """Log an agent event to the knowledge base.

    Body:
        company:      required — company name (slug derived automatically)
        company_slug: optional — override slug if already known
        event_type:   required — e.g. "outreach_sent", "reply_received"
        agent:        required — agent name (e.g. "sdr-vmware")
        summary:      required — one-line description
        detail:       optional — dict of extra data
        campaign:     optional — campaign vertical

    Returns immediately. Write is fire-and-forget.
    """
    body = await request.json()
    company     = body.get("company", "")
    slug        = body.get("company_slug") or (knowledge.slugify(company) if company else "")
    event_type  = body.get("event_type", "")
    agent_name  = body.get("agent", "")
    summary     = body.get("summary", "")
    detail      = body.get("detail") or {}
    campaign    = body.get("campaign", "")

    if not slug or not event_type or not agent_name:
        return JSONResponse(
            status_code=400,
            content={"error": "company (or company_slug), event_type, and agent are required"},
        )

    background_tasks.add_task(
        knowledge.log_event, slug, event_type, agent_name, summary, detail, campaign,
    )
    return {"status": "accepted", "company_slug": slug, "event_type": event_type}


@app.get("/research/never-contacted/{campaign}")
async def research_never_contacted(campaign: str):
    """Return slugs of companies in a campaign that have not been contacted.

    Used by SDR agents to find companies to research that haven't been
    reached out to yet.
    """
    slugs = await asyncio.to_thread(knowledge.get_never_contacted, campaign)
    return {"campaign": campaign, "count": len(slugs), "slugs": slugs}


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


@app.post("/ace/update-opportunity")
async def ace_update_opportunity(request: Request):
    """Update free-form fields on an ACE opportunity. Alias for /ace/update.

    Body:
        ace_opportunity_id: required — ACE opportunity ID (e.g. "O14608392")
        customer_business_problem: str (20-2000 chars)
        website: str (e.g. "shadagro.com")
        national_security: "Yes" | "No"

    Examples:
        Fix ShadAgro Action Required (O14608392):
        {
          "ace_opportunity_id": "O14608392",
          "customer_business_problem": "ShadAgro requires cloud migration ...",
          "website": "shadagro.com"
        }
    """
    return await ace_update(request)


@app.post("/ace/auto-create")
async def ace_auto_create(request: Request):
    """Auto-create an ACE opportunity from a HubSpot deal ID.

    Called by ace-create agent or POST /webhook/hubspot when a deal reaches
    Qualified stage. Fetches deal properties from HubSpot, validates stage is
    qualifiedtobuy, checks for existing ace_opportunity_id to prevent duplicates,
    then creates the ACE opportunity and writes the ID back to HubSpot.

    Body:
        hubspot_deal_id:  required — HubSpot deal ID
        email:            optional — contact email (falls back to placeholder)
        contact_name:     optional — contact full name
    """
    body = await request.json()
    deal_id = str(body.get("hubspot_deal_id", "")).strip()
    if not deal_id:
        return JSONResponse(status_code=400, content={"error": "hubspot_deal_id required"})

    deal = await hubspot.get_deal_details(deal_id)
    if not deal:
        logger.warning("ace_auto_create_deal_not_found", extra={"deal_id": deal_id})
        return JSONResponse(status_code=404, content={"error": "deal not found in HubSpot"})

    stage = deal.get("dealstage", "")
    if stage != "qualifiedtobuy":
        return {
            "status": "skipped",
            "reason": f"deal stage is '{stage}', not 'qualifiedtobuy'",
            "deal_id": deal_id,
        }

    existing_ace_id = deal.get("ace_opportunity_id", "")
    if existing_ace_id:
        return {
            "status": "skipped",
            "reason": "ACE opportunity already exists",
            "ace_opportunity_id": existing_ace_id,
            "deal_id": deal_id,
        }

    # Build LeadPayload from HubSpot deal fields
    company   = deal.get("dealname", "Unknown Company").split(" - ")[0].strip()
    email     = body.get("email", "") or f"hubspot-{deal_id}@auto.cloudiqs.internal"
    contact   = body.get("contact_name", "") or deal.get("dealname", "")
    campaign  = deal.get("campaign_vertical", "msp")
    icp_score = int(deal.get("icp_score", 0) or 0)
    signal    = deal.get("signal", "")
    pain      = deal.get("pain_summary", "")
    play      = deal.get("recommended_play", "")

    lead = LeadPayload(
        email=email,
        company=company,
        contact=contact,
        campaign=campaign,
        icp_score=icp_score,
        signal=signal,
        pain=pain,
        play=play,
    )

    logger.info("ace_auto_create_started", extra={"company": company, "deal_id": deal_id})
    opp_id = await ace.create_opportunity(lead)

    if opp_id:
        await hubspot.update_deal_property(deal_id, "ace_opportunity_id", opp_id)
        await ace_notifications.notify_created(opp_id, lead)
        logger.info("ace_auto_create_success", extra={"opp_id": opp_id, "deal_id": deal_id})

    return {
        "status": "created" if opp_id else "failed",
        "ace_opportunity_id": opp_id,
        "deal_id": deal_id,
        "company": company,
    }


@app.post("/ace/control-plane")
async def ace_control_plane_endpoint(request: Request):
    """ACE Control Plane — daily Alliance Lead briefing card.

    Runs all 8 MCP queries in parallel and builds a control plane card
    with 6 sections: What Happened, Your Actions Today, Where the Money Is,
    Funding, Co-Sell Momentum, and Pipeline Snapshot.

    Automatically posts the card to Teams (CEO channel).
    Returns structured JSON for programmatic use.

    Body:
        stats: optional dict with {total_leads: int}

    Always returns 200. On MCP failure sections show "No data available."
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    stats = body.get("stats") or {}
    data = await ace_control_plane.run_control_plane(stats=stats)
    await ace_control_plane.post_control_plane_to_teams(data)
    return data


@app.get("/ace/control-plane")
async def ace_control_plane_get(request: Request):
    """GET version — returns JSON without posting to Teams. Useful for testing."""
    data = await ace_control_plane.run_control_plane()
    return data


@app.post("/ace/customer-lookup")
async def ace_customer_lookup_endpoint(request: Request):
    """Query Partner Central MCP for AWS intelligence on a company.

    Runs two MCP queries:
      1. ACE pipeline check — existing opportunities for this company
      2. AWS customer profile — services, spend, region, account owner

    Body:
        company:  required — company name (e.g. "UK Tote Group")
        website:  optional — domain for disambiguation (e.g. "uktotegroup.com")

    Returns:
        aws_customer, aws_services, aws_region, aws_spend,
        aws_account_owner, aws_existing_opps, ace_opportunities

    Always returns 200. On MCP failure returns empty fields — never blocks pipeline.
    Agents call this during research and include the results in POST /lead.
    """
    body = await request.json()
    company = str(body.get("company", "")).strip()
    if not company:
        return JSONResponse(status_code=400, content={"error": "company is required"})

    website = str(body.get("website", "")).strip()
    result = await ace_customer_lookup.customer_lookup(company, website)
    result["company"] = company
    return result


@app.post("/webhook/hubspot")
async def webhook_hubspot(request: Request):
    """Receive HubSpot CRM webhook events.

    Handles deal.propertyChange events. When a deal's stage changes to
    'qualifiedtobuy', triggers ACE auto-create.

    HubSpot sends an array of subscription events per request:
    [{"subscriptionType": "deal.propertyChange",
      "objectId": 12345,
      "propertyName": "dealstage",
      "propertyValue": "qualifiedtobuy"}, ...]

    Returns 200 immediately (HubSpot requires fast response).
    """
    try:
        events = await request.json()
    except Exception:
        return {"status": "ok"}

    if not isinstance(events, list):
        events = [events]

    for event in events:
        sub_type = event.get("subscriptionType", "")
        prop     = event.get("propertyName", "")
        value    = event.get("propertyValue", "")
        obj_id   = str(event.get("objectId", ""))

        if sub_type == "deal.propertyChange" and prop == "dealstage" and value == "qualifiedtobuy":
            logger.info("hubspot_webhook_qualified", extra={"deal_id": obj_id})
            # Fire and forget — do not await; webhook must return fast
            asyncio.create_task(
                ace_auto_create(
                    _FakeRequest({"hubspot_deal_id": obj_id})
                )
            )

    return {"status": "ok"}


class _FakeRequest:
    """Minimal Request-like object for internal ace_auto_create calls."""
    def __init__(self, body: dict):
        self._body = body

    async def json(self) -> dict:
        return self._body


def _parse_mcp(result) -> str:
    """Extract and clean text from an MCP send_message response via shared parser."""
    from app.mcp_parser import parse_mcp_response
    return parse_mcp_response(result) or "No data returned."


@app.get("/targets/weekly")
async def targets_weekly():
    """Return Q2 2026 weekly pipeline target progress.

    Shows week number, cumulative pipeline vs expected, on_track flag,
    gap to target, and required weekly pipeline to close the gap.
    """
    from app import targets
    _reset_stats_if_new_day()
    stats = {
        "total_leads":    _stats.get("total_leads", 0),
        "week_leads":     _stats.get("total_leads", 0),
    }
    return await targets.get_weekly_targets(stats=stats)


@app.post("/ace/funding-check")
async def ace_funding_check_post():
    """Run ACE funding eligibility check via Partner Central MCP.

    3 parallel queries: eligible opportunities, active applications, programs.
    Posts results to ACE Teams channel.

    Called by ace-funding agent on demand.
    """
    from app import ace_funding
    logger.info("ace_funding_check_started")
    data = await ace_funding.run_funding_check()
    await ace_funding.post_funding_to_teams(data)
    logger.info("ace_funding_check_complete", extra={"eligible": data.get("eligible_count", 0)})
    return {
        "status":          "complete",
        "date":            data.get("date", ""),
        "eligible_count":  data.get("eligible_count", 0),
        "action_items":    data.get("action_items", []),
        "eligible":        data.get("eligible", ""),
        "active":          data.get("active", ""),
        "programs":        data.get("programs", ""),
    }


@app.get("/ace/funding-check")
async def ace_funding_check_get():
    """GET version — returns same data without posting to Teams."""
    from app import ace_funding
    return await ace_funding.run_funding_check()


@app.post("/ace/sync")
async def ace_sync_post():
    """Sync ACE stages into HubSpot (one-way: ACE → HubSpot).

    Fetches all HubSpot deals with ace_opportunity_id, reads the current
    stage from ACE Partner Central, and updates HubSpot where ACE is ahead.
    Never updates ACE from HubSpot — ACE changes require human review.

    Called by ace-sync agent every 2 hours.
    """
    from app import ace_sync
    logger.info("ace_sync_started")
    data = await ace_sync.run_sync()
    await ace_sync.post_sync_to_teams(data)
    logger.info("ace_sync_complete", extra={"synced": data.get("synced", 0)})
    return data


@app.get("/ace/sync")
async def ace_sync_get():
    """GET version of ACE sync — returns the same data without posting to Teams."""
    from app import ace_sync
    return await ace_sync.run_sync()


@app.post("/ace/hygiene")
async def ace_hygiene_post():
    """Run ACE pipeline hygiene check via Partner Central MCP.

    6 queries run concurrently (asyncio.gather):
      action_required, stale_launched, funding_eligible,
      aws_stage, past_close_dates, cosell

    Returns health score (0-10), prioritised action plan, and full sections.
    Posts a formatted card to the ACE Teams channel.

    Called by ace-hygiene agent on its Monday 06:00 schedule.
    """
    from app import ace_hygiene
    logger.info("ace_hygiene_started")
    data = await ace_hygiene.run_hygiene()
    await ace_hygiene.post_hygiene_to_teams(data)
    logger.info("ace_hygiene_complete", extra={"health_score": data.get("health_score")})
    return {
        "status":           "complete",
        "date":             data.get("date", ""),
        "health_score":     data.get("health_score", 0),
        "health_label":     data.get("health_label", ""),
        "action_plan":      data.get("action_plan", []),
        "action_required":  data.get("action_required", ""),
        "stale_launched":   data.get("stale_launched", ""),
        "funding_eligible": data.get("funding_eligible", ""),
        "aws_stage":        data.get("aws_stage", ""),
        "past_close_dates": data.get("past_close_dates", ""),
        "cosell":           data.get("cosell", ""),
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

    if event_type in ("reply", "reply_received", "responded") and reply_text:
        # Classify via Bedrock Haiku in background — do not block webhook response
        asyncio.create_task(_classify_and_notify(event, email, reply_text, campaign_id))
    elif event_type in ("reply", "reply_received", "responded"):
        await teams.notify(
            f"Reply Received | {email}\n"
            f"Campaign: {campaign_id}\n"
            "No reply text captured."
        )

    return {"status": "received", "event_type": event_type}


async def _classify_and_notify(event: dict, email: str, reply_text: str, campaign_id: str) -> None:
    """Classify a reply with Bedrock and post enriched Teams notification."""
    from app import reply_classifier
    try:
        classification = await reply_classifier.classify_reply(reply_text, email, campaign_id)
        label = classification.get("classification", "unknown")
        action = classification.get("suggested_action", "Human review required")
        confidence = classification.get("confidence", "medium")
        reason = classification.get("reason", "")

        # Update the stored event with classification
        async with _webhook_lock:
            for e in _webhook_events:
                if e is event:
                    e["classification"] = label
                    e["suggested_action"] = action
                    break
            _save_events_to_disk(_webhook_events)

        # Urgency: interested/referral = high priority
        if label in ("interested", "referral"):
            title = f"HOT REPLY: {label.upper()} | {email}"
        else:
            title = f"Reply: {label} | {email}"

        body = (
            f"Campaign: {campaign_id}\n"
            f"Classification: {label} ({confidence} confidence)\n"
            f"Reason: {reason}\n"
            f"Action: {action}\n\n"
            f"Reply: {reply_text[:300]}"
        )
        await teams.post_to_sdr(title=title, body_text=body)
    except Exception as exc:
        logger.warning("classify_and_notify_failed", extra={"error": str(exc), "email": email})


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


@app.get("/webhook/instantly/stats")
async def webhook_instantly_stats():
    """Return reply classification stats for the stored events.

    Aggregates classification labels so sdr-digest can report on reply quality.
    """
    async with _webhook_lock:
        events = _webhook_events.copy()

    reply_events = [
        e for e in events
        if e.get("event_type") in ("reply", "reply_received", "responded")
    ]

    counts: dict = {}
    for e in reply_events:
        label = e.get("classification", "unclassified")
        counts[label] = counts.get(label, 0) + 1

    total = len(reply_events)
    interested = counts.get("interested", 0)
    reply_rate = round(interested / total * 100, 1) if total > 0 else 0.0

    return {
        "total_replies": total,
        "classifications": counts,
        "interested_count": interested,
        "interested_rate_pct": reply_rate,
    }


# ── Event bus ────────────────────────────────────────────────────────────────

@app.post("/event")
async def event_publish(request: Request):
    """Publish an event to the agent event bus.

    SDR agents POST this after lead creation, ACE agents after opportunity
    creation, reply handler after classification, etc.

    Body:
        event_type: str  — e.g. "lead.created", "deal.qualified"
        agent:      str  — source agent name (e.g. "sdr-vmware")
        payload:    dict — event-specific data

    Returns the saved event with id and timestamp.
    """
    from app import events as ev
    body = await request.json()
    event_type = body.get("event_type", "")
    agent      = body.get("agent", "unknown")
    payload    = body.get("payload", {})

    if not event_type:
        return JSONResponse(status_code=400, content={"error": "event_type required"})

    event = await ev.publish(event_type, agent, payload)
    return {"status": "published", "event": event}


@app.get("/events/recent")
async def events_recent(
    event_type: str = "",
    agent: str = "",
    limit: int = 50,
):
    """Return recent bus events, newest first.

    Query params:
        event_type: filter by exact event type
        agent:      filter by source agent
        limit:      max results (default 50, max 200)
    """
    from app import events as ev
    items = await ev.get_recent(
        event_type=event_type or None,
        agent=agent or None,
        limit=limit,
    )
    return {"events": items, "total": len(items)}


@app.post("/event/replay")
async def event_replay(request: Request):
    """Look up a specific event by ID.

    Body:
        event_id: str — UUID of the event to retrieve
    """
    from app import events as ev
    body = await request.json()
    event_id = body.get("event_id", "")
    if not event_id:
        return JSONResponse(status_code=400, content={"error": "event_id required"})

    event = await ev.replay(event_id)
    if not event:
        return JSONResponse(status_code=404, content={"error": "event not found"})
    return event


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


# ── Config: Brave key ─────────────────────────────────────────────────────────

@app.get("/config/brave-key")
async def config_brave_key():
    """Return the Brave Search API key from Secrets Manager.
    Secret path: cloudiqs/{STACK_NAME}/brave/api-key
    """
    from app.config import get_secret, is_dummy
    key = get_secret("brave/api-key")
    if is_dummy(key):
        return JSONResponse(
            status_code=503,
            content={"error": "Brave API key not configured in Secrets Manager"},
        )
    return {"api_key": key}


# ── Teams direct post endpoints (for agents that need a simple curl) ──────────

@app.post("/teams/sdr")
async def teams_post_sdr(request: Request):
    """Post a message to the SDR alerts channel.
    Body: {"title": "...", "body_text": "...", "facts": [...]}
    """
    body = await request.json()
    ok = await teams.post_to_sdr(
        title=body.get("title", "Alert"),
        body_text=body.get("body_text", ""),
        facts=body.get("facts"),
    )
    return {"ok": ok}


@app.post("/teams/ace")
async def teams_post_ace(request: Request):
    """Post a message to the ACE updates channel.
    Body: {"title": "...", "body_text": "...", "facts": [...]}
    """
    body = await request.json()
    ok = await teams.post_to_ace(
        title=body.get("title", "ACE Update"),
        body_text=body.get("body_text", ""),
        facts=body.get("facts"),
    )
    return {"ok": ok}


@app.post("/teams/ceo")
async def teams_post_ceo(request: Request):
    """Post a message to the CEO briefing channel.
    Body: {"title": "...", "body_text": "...", "facts": [...]}
    """
    body = await request.json()
    ok = await teams.post_to_ceo(
        title=body.get("title", "CEO Update"),
        body_text=body.get("body_text", ""),
        facts=body.get("facts"),
    )
    return {"ok": ok}


# ── Research / knowledge store ────────────────────────────────────────────────
# In-memory store for agent research profiles and discovery briefs.
# On production these should be backed by S3; for now they persist for the
# bridge lifetime (Docker restart clears them — acceptable for agent-to-agent
# handover within a session).

_research_profiles: dict = {}
_research_briefs: dict = {}


@app.post("/research/save")
async def research_save(request: Request):
    """Save a company research dossier (from research-agent).
    Body: {"company": "acme", "profile": {...full dossier...}}
    """
    body = await request.json()
    company = body.get("company", "").lower().replace(" ", "-")
    profile = body.get("profile")
    if not company or not profile:
        return JSONResponse(status_code=400, content={"error": "company and profile required"})
    _research_profiles[company] = profile
    logger.info("research_saved", extra={"company": company})
    return {"status": "saved", "key": f"profiles/{company}.json"}


@app.get("/research/profile")
async def research_profile(company: str):
    """Return the research dossier for a company.
    Query: ?company=acme-corp
    """
    key = company.lower().replace(" ", "-")
    profile = _research_profiles.get(key)
    if not profile:
        return JSONResponse(
            status_code=404,
            content={"error": f"No profile found for {company}"},
        )
    return profile


@app.post("/research/brief")
async def research_brief_save(request: Request):
    """Save a discovery brief (from qualification-agent).
    Body: {"company": "acme", "brief": "...", "hubspot_deal_id": "..."}
    """
    body = await request.json()
    company = body.get("company", "").lower().replace(" ", "-")
    brief = body.get("brief")
    if not company or not brief:
        return JSONResponse(status_code=400, content={"error": "company and brief required"})
    _research_briefs[company] = {
        "brief": brief,
        "hubspot_deal_id": body.get("hubspot_deal_id"),
        "saved_at": datetime.now().isoformat(),
    }
    logger.info("brief_saved", extra={"company": company})
    return {"status": "saved", "key": f"briefs/{company}-discovery.json"}


@app.get("/research/brief")
async def research_brief_get(company: str):
    """Return the discovery brief for a company.
    Query: ?company=acme-corp
    """
    key = company.lower().replace(" ", "-")
    brief = _research_briefs.get(key)
    if not brief:
        return JSONResponse(
            status_code=404,
            content={"error": f"No brief found for {company}"},
        )
    return brief


# ── HubSpot search convenience endpoint ──────────────────────────────────────

@app.get("/hubspot/search")
async def hubspot_search(company: str = "", email: str = ""):
    """Search HubSpot for an existing deal by company name or email.
    Returns first matching deal or 404.
    Query: ?company=acme or ?email=user@acme.com
    """
    if not company and not email:
        return JSONResponse(status_code=400, content={"error": "company or email required"})
    try:
        from app import hubspot as hs
        if email:
            contact = await asyncio.to_thread(hs.get_contact_by_email, email)
            if contact:
                return {"found": True, "contact": contact}
        if company:
            deals = await asyncio.to_thread(hs.search_deals_by_company, company)
            if deals:
                return {"found": True, "deals": deals}
        return JSONResponse(status_code=404, content={"found": False})
    except Exception as exc:
        logger.warning("hubspot_search_error", extra={"error": str(exc)})
        return JSONResponse(status_code=404, content={"found": False})
