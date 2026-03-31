"""Microsoft Teams notifications via webhook."""

import logging
from typing import Optional
import httpx
from app.config import get_secret, is_dummy

logger = logging.getLogger("bridge")


async def notify(text: str, webhook_key: str = "teams/webhook-url") -> bool:
    """Send a notification to Teams. Returns True on success."""
    try:
        url = get_secret(webhook_key)
    except Exception as e:
        logger.warning(f"Teams notify: could not retrieve webhook URL: {e}")
        return False
    if is_dummy(url):
        logger.warning("Teams webhook not set - skipping notification")
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json={"text": text})
            if r.status_code == 200:
                return True
            logger.error(f"Teams notify failed {r.status_code}")
    except Exception as e:
        logger.error(f"Teams notify error: {e}")
    return False


async def notify_lead(lead_data: dict) -> bool:
    """Send a formatted lead notification to Teams."""
    company = lead_data.get("company", "Unknown")
    contact = lead_data.get("contact", "")
    campaign = lead_data.get("campaign", "").upper()
    icp = lead_data.get("icp_score", 0)
    signal = lead_data.get("signal", "")
    pain = lead_data.get("pain", "")
    play = lead_data.get("play", "")
    hubspot_deal = lead_data.get("hubspot_deal_id", "")
    instantly = lead_data.get("instantly_lead_id", "")

    text = (
        f"**New Lead** | ICP {icp}/10 | {campaign}\n\n"
        f"**Company:** {company}\n"
        f"**Contact:** {contact} ({lead_data.get('job_title', '')})\n"
        f"**Signal:** {signal}\n**Pain:** {pain}\n**Play:** {play}\n\n"
        f"HubSpot Deal: {hubspot_deal} | Instantly: {'enrolled' if instantly else 'skipped'}"
    )
    return await notify(text)
