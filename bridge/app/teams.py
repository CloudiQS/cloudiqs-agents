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
  1. Try Adaptive Card v1.4 (Power Automate webhook flows)
  2. On non-200, fall back to simple {"title": ..., "text": ...}

Premium card builders (exported):
  build_lead_card(lead_data)          - full lead notification card
  build_section_card(title, sections) - multi-section card for reports
  build_ace_update_card(data)         - ACE stage change card
"""

import logging
from typing import Optional
import httpx
from app.config import get_secret, is_dummy
from app.card_builder import build_lead_card  # noqa: F401 — re-exported for callers

logger = logging.getLogger("bridge")

# Container style maps to ICP score band
def _icp_style(icp: int) -> str:
    if icp >= 8:
        return "good"       # green
    if icp >= 5:
        return "warning"    # amber
    return "attention"      # red


def _sep() -> dict:
    """A thin separator line between sections."""
    return {"type": "TextBlock", "text": " ", "separator": True, "spacing": "small"}


def _section_header(text: str) -> dict:
    return {
        "type": "TextBlock",
        "text": text,
        "weight": "bolder",
        "size": "small",
        "color": "accent",
        "spacing": "small",
    }


# build_lead_card is imported from app.card_builder (see top of file)

# ── Multi-section report card (CEO briefing, ACE hygiene) ─────────────────────

def build_section_card(
    title: str,
    sections: list[dict],
    header_style: str = "accent",
    subtitle: str = "",
) -> dict:
    """Build a multi-section report card.

    sections: list of {"heading": str, "body": str, "facts": list, "style": str}
      - heading: section header text
      - body: TextBlock text (optional)
      - facts: list of {"title": str, "value": str} (optional)
      - style: container style override (optional)

    Example:
      build_section_card(
          "CEO BRIEFING — 2 Apr 2026",
          [
              {"heading": "TODAY'S FOCUS", "body": "1. Clear Action Required\n2. Submit funding"},
              {"heading": "PIPELINE", "facts": [{"title": "Launched", "value": "48"}, ...]},
          ]
      )
    """
    body: list[dict] = []

    # Header
    header_items: list[dict] = [
        {"type": "TextBlock", "text": title, "weight": "bolder", "size": "large", "color": "light", "wrap": True}
    ]
    if subtitle:
        header_items.append({"type": "TextBlock", "text": subtitle, "color": "light", "isSubtle": True, "spacing": "none", "wrap": True})

    body.append({
        "type": "Container",
        "style": header_style,
        "bleed": True,
        "items": header_items,
    })

    # Sections
    for i, section in enumerate(sections):
        if i > 0:
            body.append(_sep())

        items: list[dict] = [_section_header(section.get("heading", ""))]

        section_body = section.get("body", "")
        if section_body:
            items.append({"type": "TextBlock", "text": section_body, "wrap": True, "spacing": "small"})

        facts = section.get("facts")
        if facts:
            items.append({"type": "FactSet", "facts": facts, "spacing": "small"})

        container: dict = {"type": "Container", "items": items, "spacing": "medium"}
        if section.get("style"):
            container["style"] = section["style"]

        body.append(container)

    return _wrap_card(body)


# ── ACE update card ───────────────────────────────────────────────────────────

def build_ace_update_card(data: dict) -> dict:
    """Build an ACE stage change / rep update card.

    data keys: company, opp_id, stage_from, stage_to, aws_rep,
               contact, action, body_text, style ("good"|"warning"|"attention"|"accent")
    """
    company    = data.get("company", "Unknown")
    opp_id     = data.get("opp_id", "")
    stage_from = data.get("stage_from", "")
    stage_to   = data.get("stage_to", "")
    aws_rep    = data.get("aws_rep", "")
    contact    = data.get("contact", "")
    action     = data.get("action", "")
    body_text  = data.get("body_text", "")
    style      = data.get("style", "accent")
    title      = data.get("title", f"ACE UPDATE: {company}")

    body: list[dict] = []

    # Header
    header_cols = [
        {
            "type": "Column",
            "width": "stretch",
            "items": [{"type": "TextBlock", "text": title, "weight": "bolder", "size": "medium", "color": "light", "wrap": True}],
        }
    ]
    if opp_id:
        header_cols.append({
            "type": "Column",
            "width": "auto",
            "items": [{"type": "TextBlock", "text": opp_id, "color": "light", "isSubtle": True, "horizontalAlignment": "right"}],
        })

    body.append({
        "type": "Container",
        "style": style,
        "bleed": True,
        "items": [{"type": "ColumnSet", "columns": header_cols}],
    })

    # Details
    facts = []
    if stage_from and stage_to:
        facts.append({"title": "Stage", "value": f"{stage_from} → {stage_to}"})
    elif stage_to:
        facts.append({"title": "Stage", "value": stage_to})
    if aws_rep:
        facts.append({"title": "AWS Rep", "value": aws_rep})
    if contact:
        facts.append({"title": "Contact", "value": contact})

    detail_items: list[dict] = []
    if facts:
        detail_items.append({"type": "FactSet", "facts": facts})
    if body_text:
        detail_items.append({"type": "TextBlock", "text": body_text, "wrap": True, "spacing": "small"})
    if action:
        body.append(_sep())
        detail_items.append({
            "type": "TextBlock",
            "text": f"**ACTION:** {action}",
            "wrap": True,
            "color": "attention",
            "spacing": "small",
        })

    if detail_items:
        body.append({"type": "Container", "spacing": "medium", "items": detail_items})

    return _wrap_card(body)


# ── Card wrapper ──────────────────────────────────────────────────────────────

def _wrap_card(body: list) -> dict:
    """Wrap card body in the Power Automate message envelope."""
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


def _build_adaptive_card(title: str, body_text: str, facts: Optional[list] = None) -> dict:
    """Build a simple Adaptive Card v1.4 with title, optional FactSet, and body text."""
    body: list[dict] = [
        {"type": "TextBlock", "text": title, "weight": "bolder", "size": "large", "wrap": True}
    ]
    if facts:
        body.append({"type": "FactSet", "facts": facts})
    if body_text:
        body.append({"type": "TextBlock", "text": body_text, "wrap": True, "spacing": "small"})
    return _wrap_card(body)


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
    """Post to Teams using build_section_card. Falls back to simple format on failure."""
    sections = []
    if facts:
        sections.append({"heading": "", "facts": facts, "body": body_text})
    else:
        sections.append({"heading": "", "body": body_text})

    card = build_section_card(title, sections)
    ok = await _post_raw(card, webhook_key)
    if ok:
        logger.info("teams_sent_adaptive", extra={"key": webhook_key})
        return True
    # Fallback
    simple = _build_simple(title, body_text)
    ok = await _post_raw(simple, webhook_key)
    if ok:
        logger.info("teams_sent_simple_fallback", extra={"key": webhook_key})
    return ok


# ── Webhook key resolver ──────────────────────────────────────────────────────

def _resolve_webhook(preferred_key: str, fallback_key: str = "teams/webhook-url") -> str:
    """Return preferred webhook key if configured, otherwise fallback."""
    try:
        val = get_secret(preferred_key)
        return preferred_key if not is_dummy(val) else fallback_key
    except Exception:
        return fallback_key


# ── Named channel routing functions ──────────────────────────────────────────

async def post_to_sdr(
    title: str,
    body_text: str = "",
    facts: Optional[list] = None,
    colour: str = "0078D4",
) -> bool:
    """Post to the SDR alerts channel (teams/webhook-url)."""
    return await _post(title, body_text, facts, webhook_key="teams/webhook-url")


async def post_to_ceo(
    title: str,
    body_text: str = "",
    facts: Optional[list] = None,
    colour: str = "0078D4",
) -> bool:
    """Post to the CEO briefing channel (teams/ceo-webhook-url, fallback to SDR)."""
    webhook_key = _resolve_webhook("teams/ceo-webhook-url")
    return await _post(title, body_text, facts, webhook_key=webhook_key)


async def post_to_ace(
    title: str,
    body_text: str = "",
    facts: Optional[list] = None,
    colour: str = "0078D4",
) -> bool:
    """Post to the ACE updates channel (teams/ace-webhook-url, fallback to SDR)."""
    webhook_key = _resolve_webhook("teams/ace-webhook-url")
    return await _post(title, body_text, facts, webhook_key=webhook_key)


# ── Plain text helper ─────────────────────────────────────────────────────────

async def notify(text: str) -> bool:
    """Send a plain text message to SDR channel."""
    return await post_to_sdr("Alert", text)


# ── Rich lead card ────────────────────────────────────────────────────────────

async def notify_lead(lead_data: dict) -> bool:
    """Send a lead notification card to the SDR channel."""
    company  = lead_data.get("company", "Unknown")
    icp      = int(lead_data.get("icp_score") or 0)
    campaign = (lead_data.get("campaign") or "").upper()

    card = build_lead_card(lead_data)
    ok = await _post_raw(card, "teams/webhook-url")
    if ok:
        logger.info("teams_lead_card_sent", extra={"company": company, "icp": icp, "campaign": campaign})
        return True

    # Fallback to simple text
    title  = f"New Lead | ICP {icp}/10 | {campaign}"
    simple = _build_simple(title, company)
    ok = await _post_raw(simple, "teams/webhook-url")
    logger.info("teams_lead_card_fallback", extra={"company": company})
    return ok
