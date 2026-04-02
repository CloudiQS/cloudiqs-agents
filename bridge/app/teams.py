"""Microsoft Teams notifications via incoming webhook.

Three channel routing functions (use these everywhere):
  post_to_sdr(title, body_text, facts=None, colour="0078D4")
  post_to_ceo(title, body_text, facts=None, colour="0078D4")
  post_to_ace(title, body_text, facts=None, colour="0078D4")

Channels:
  SDR   -> teams/webhook-url
  CEO   -> teams/ceo-webhook-url  (fallback: teams/webhook-url)
  ACE   -> teams/ace-webhook-url  (fallback: teams/webhook-url)

Format strategy:
  1. Try Adaptive Card (works with new Power Automate webhook flows)
  2. On non-200, fall back to simple {"title": ..., "text": ...} which
     every Teams webhook format accepts

Helpers:
  notify(text)      - plain text to SDR channel
  notify_lead(dict) - rich lead card to SDR channel
"""

import logging
from typing import Optional
import httpx
from app.config import get_secret, is_dummy

logger = logging.getLogger("bridge")

# Colour constants for border/highlight
COLOUR_GREEN = "00B050"
COLOUR_AMBER = "FFC000"
COLOUR_RED   = "C00000"
COLOUR_BLUE  = "0078D4"


# ── Card builder ──────────────────────────────────────────────────────────────

def _build_adaptive_card(title: str, body_text: str, facts: Optional[list] = None) -> dict:
    """Build an Adaptive Card 1.4 payload for Teams."""
    body: list[dict] = [
        {"type": "TextBlock", "text": title, "size": "medium", "weight": "bolder", "wrap": True},
    ]
    if facts:
        body.append({"type": "FactSet", "facts": facts})
    if body_text:
        body.append({"type": "TextBlock", "text": body_text, "wrap": True})
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": body,
            },
        }],
    }


def _build_simple(title: str, body_text: str) -> dict:
    """Simple payload that every Teams webhook variant accepts."""
    text = f"**{title}**\n\n{body_text}" if body_text else title
    return {"title": title, "text": text}


# ── Internal HTTP helper ──────────────────────────────────────────────────────

async def _post_raw(payload: dict, webhook_key: str = "teams/webhook-url") -> bool:
    """POST any payload to the Teams webhook. Returns True on 200."""
    try:
        url = get_secret(webhook_key)
    except Exception as e:
        logger.warning("teams_webhook_url_error", extra={"key": webhook_key, "error": str(e)})
        return False
    if is_dummy(url):
        logger.warning("teams_webhook_not_configured", extra={"key": webhook_key})
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload)
            if r.status_code == 200:
                return True
            logger.warning("teams_post_failed", extra={"status": r.status_code, "key": webhook_key})
    except Exception as e:
        logger.error("teams_post_error", extra={"error": str(e), "key": webhook_key})
    return False


async def _post(
    title: str,
    body_text: str,
    facts: Optional[list] = None,
    webhook_key: str = "teams/webhook-url",
) -> bool:
    """Post to Teams. Try Adaptive Card first, fall back to simple format."""
    card = _build_adaptive_card(title, body_text, facts)
    ok = await _post_raw(card, webhook_key)
    if ok:
        logger.info("teams_sent_adaptive", extra={"key": webhook_key})
        return True
    # Fallback to simple format for older Power Automate connectors
    simple = _build_simple(title, body_text)
    ok = await _post_raw(simple, webhook_key)
    if ok:
        logger.info("teams_sent_simple_fallback", extra={"key": webhook_key})
    return ok


# ── Named channel routing functions ──────────────────────────────────────────

async def post_to_sdr(
    title: str,
    body_text: str = "",
    facts: Optional[list] = None,
    colour: str = COLOUR_BLUE,
) -> bool:
    """Post to the SDR alerts channel (teams/webhook-url)."""
    return await _post(title, body_text, facts, webhook_key="teams/webhook-url")


async def post_to_ceo(
    title: str,
    body_text: str = "",
    facts: Optional[list] = None,
    colour: str = COLOUR_BLUE,
) -> bool:
    """Post to the CEO briefing channel (teams/ceo-webhook-url, fallback to SDR)."""
    key = get_secret("teams/ceo-webhook-url")
    webhook_key = "teams/ceo-webhook-url" if not is_dummy(key) else "teams/webhook-url"
    return await _post(title, body_text, facts, webhook_key=webhook_key)


async def post_to_ace(
    title: str,
    body_text: str = "",
    facts: Optional[list] = None,
    colour: str = COLOUR_BLUE,
) -> bool:
    """Post to the ACE updates channel (teams/ace-webhook-url, fallback to SDR)."""
    key = get_secret("teams/ace-webhook-url")
    webhook_key = "teams/ace-webhook-url" if not is_dummy(key) else "teams/webhook-url"
    return await _post(title, body_text, facts, webhook_key=webhook_key)


# ── Plain text helper ─────────────────────────────────────────────────────────

async def notify(text: str) -> bool:
    """Send a plain text message to SDR channel."""
    return await post_to_sdr("Alert", text)


# ── Rich lead card ────────────────────────────────────────────────────────────

async def notify_lead(lead_data: dict) -> bool:
    """Send a structured lead card to the SDR channel."""
    company      = lead_data.get("company", "Unknown")
    campaign     = (lead_data.get("campaign") or "").upper()
    icp          = lead_data.get("icp_score", 0)
    contact      = lead_data.get("contact", "")
    job_title    = lead_data.get("job_title", "")
    email_addr   = lead_data.get("email", "")
    linkedin     = lead_data.get("linkedin_url", "")
    website      = lead_data.get("website", "")
    employees    = lead_data.get("employees")
    location     = lead_data.get("location", "")
    ch_number    = lead_data.get("companies_house_number", "")
    signal       = lead_data.get("signal", "")
    pain         = lead_data.get("pain", "")
    play         = lead_data.get("play", "")
    hubspot_deal = lead_data.get("hubspot_deal_id", "")
    instantly_id = lead_data.get("instantly_lead_id", "")

    facts: list[dict] = [{"title": "Company", "value": company}]
    if website:
        facts.append({"title": "Website", "value": website})
    if employees:
        facts.append({"title": "Employees", "value": str(employees)})
    if location and location not in ("GB", ""):
        facts.append({"title": "Location", "value": location})
    if ch_number:
        facts.append({"title": "Companies House", "value": ch_number})

    primary = f"{contact} | {job_title}" if (contact and job_title) else contact or "Unknown"
    facts.append({"title": "PRIMARY", "value": primary})
    if email_addr:
        facts.append({"title": "Email", "value": email_addr})
    if linkedin:
        facts.append({"title": "LinkedIn", "value": linkedin})
    if signal:
        facts.append({"title": "Signal", "value": signal})
    if pain:
        facts.append({"title": "Pain", "value": pain})
    if play:
        facts.append({"title": "Play", "value": play})

    crm_parts = []
    if hubspot_deal:
        crm_parts.append(f"HubSpot: {hubspot_deal}")
    crm_parts.append(f"Instantly: {'enrolled' if instantly_id else 'skipped'}")

    title = f"New Lead | ICP {icp}/10 | {campaign}"
    body = " | ".join(crm_parts)
    return await post_to_sdr(title, body, facts)
