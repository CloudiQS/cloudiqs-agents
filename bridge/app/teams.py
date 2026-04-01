"""Microsoft Teams notifications via incoming webhook.

Two public helpers:
  notify(text)      - plain text for replies, errors, system alerts
  notify_lead(dict) - rich MessageCard matching the CloudiQS lead card format
"""

import logging
from typing import Optional
import httpx
from app.config import get_secret, is_dummy

logger = logging.getLogger("bridge")


# ── Internal HTTP helper ──────────────────────────────────────────────────────

async def _post(payload: dict, webhook_key: str = "teams/webhook-url") -> bool:
    """POST any payload to the Teams webhook. Returns True on success."""
    try:
        url = get_secret(webhook_key)
    except Exception as e:
        logger.warning("teams_webhook_url_error", extra={"error": str(e)})
        return False
    if is_dummy(url):
        logger.warning("teams_webhook_not_configured")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload)
            if r.status_code == 200:
                return True
            logger.error("teams_post_failed", extra={"status": r.status_code})
    except Exception as e:
        logger.error("teams_post_error", extra={"error": str(e)})
    return False


# ── Plain text notifications (replies, alerts, system events) ─────────────────

async def notify(text: str, webhook_key: str = "teams/webhook-url") -> bool:
    """Send a plain-text message to Teams."""
    return await _post({"text": text}, webhook_key=webhook_key)


# ── Rich lead card ────────────────────────────────────────────────────────────

async def notify_lead(lead_data: dict) -> bool:
    """Send a structured lead card to Teams.

    Produces an Office 365 MessageCard matching the CloudiQS lead format:

        New Lead | ICP 9/10 | SWITCHER
        ─────────────────────────────────
        Company:         Acme Ltd
        Website:         https://acme.com
        Employees:       250
        Location:        Manchester
        Companies House: 12345678
        ─────────────────────────────────
        PRIMARY:         Jane Doe | Head of Engineering
        Email:           jane@acme.com
        LinkedIn:        https://linkedin.com/in/janedoe
        ─────────────────────────────────
        Signal:          3 cloud platform roles posted Feb 2026
        Pain:            Legacy VMware estate, scaling issues
        Play:            CloudiQS Migration Consulting — free WAR
        ─────────────────────────────────
        HubSpot: hs-deal-123  |  Instantly: enrolled
    """
    company       = lead_data.get("company", "Unknown")
    campaign      = (lead_data.get("campaign") or "").upper()
    icp           = lead_data.get("icp_score", 0)
    contact       = lead_data.get("contact", "")
    job_title     = lead_data.get("job_title", "")
    email_addr    = lead_data.get("email", "")
    linkedin      = lead_data.get("linkedin_url", "")
    website       = lead_data.get("website", "")
    employees     = lead_data.get("employees")
    location      = lead_data.get("location", "")
    ch_number     = lead_data.get("companies_house_number", "")
    signal        = lead_data.get("signal", "")
    pain          = lead_data.get("pain", "")
    play          = lead_data.get("play", "")
    hubspot_deal  = lead_data.get("hubspot_deal_id", "")
    instantly_id  = lead_data.get("instantly_lead_id", "")

    # ── Section 1: Company ───────────────────────────────────────────────────
    company_facts: list[dict] = [{"name": "Company", "value": company}]
    if website:
        company_facts.append({"name": "Website", "value": f"[{website}]({website})"})
    if employees:
        company_facts.append({"name": "Employees", "value": str(employees)})
    if location and location not in ("GB", ""):
        company_facts.append({"name": "Location", "value": location})
    if ch_number:
        company_facts.append({"name": "Companies House", "value": ch_number})

    # ── Section 2: Contact ───────────────────────────────────────────────────
    primary = f"{contact} | {job_title}" if (contact and job_title) else contact or "Unknown"
    contact_facts: list[dict] = [{"name": "PRIMARY", "value": primary}]
    if email_addr:
        contact_facts.append({"name": "Email", "value": email_addr})
    if linkedin:
        contact_facts.append({"name": "LinkedIn", "value": f"[{linkedin}]({linkedin})"})

    # ── Section 3: Intelligence ──────────────────────────────────────────────
    intel_facts: list[dict] = []
    if signal:
        intel_facts.append({"name": "Signal", "value": signal})
    if pain:
        intel_facts.append({"name": "Pain", "value": pain})
    if play:
        intel_facts.append({"name": "Play", "value": play})

    # ── Section 4: CRM status ────────────────────────────────────────────────
    crm_parts = []
    if hubspot_deal:
        crm_parts.append(f"HubSpot: {hubspot_deal}")
    crm_parts.append(f"Instantly: {'enrolled' if instantly_id else 'skipped'}")

    # ── Assemble MessageCard ─────────────────────────────────────────────────
    sections = [
        {"facts": company_facts},
        {"facts": contact_facts},
    ]
    if intel_facts:
        sections.append({"facts": intel_facts})
    sections.append({"text": " &nbsp;|&nbsp; ".join(crm_parts)})

    card = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": "FF6600",
        "summary": f"New Lead | {company} | ICP {icp}/10",
        "title": f"New Lead | ICP {icp}/10 | {campaign}",
        "sections": sections,
    }
    return await _post(card)
