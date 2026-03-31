"""
Instantly email campaign integration.
Enrols leads into campaign-specific sequences.
"""

import logging
from typing import Optional

import httpx

from app.config import get_secret, is_dummy, is_valid_uuid
from app.campaign import INSTANTLY_CAMPAIGN_MAP
from app.models import LeadPayload

logger = logging.getLogger("bridge")

INSTANTLY_API = "https://api.instantly.ai/api/v2"


async def enrol(lead: LeadPayload) -> Optional[str]:
    """Enrol a lead into the correct Instantly campaign. Returns lead ID or None."""
    key = get_secret("instantly/api-key")
    if is_dummy(key):
        logger.warning("Instantly key not set - skipping enrolment")
        return None

    # Resolve campaign UUID from Secrets Manager
    camp_secret = INSTANTLY_CAMPAIGN_MAP.get(lead.campaign, "instantly/vmware-campaign-id")
    campaign_id = get_secret(camp_secret)

    if is_dummy(campaign_id) or not is_valid_uuid(campaign_id):
        # Fallback to vmware campaign
        campaign_id = get_secret("instantly/vmware-campaign-id")

    if is_dummy(campaign_id) or not is_valid_uuid(campaign_id):
        logger.warning(f"No valid campaign UUID for {lead.campaign} - skipping Instantly")
        return None

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    parts = lead.contact.strip().split(" ", 1) if lead.contact else ["", ""]
    payload = {
        "campaign": campaign_id,
        "email": lead.email,
        "first_name": parts[0] if parts else "",
        "last_name": parts[1] if len(parts) > 1 else "",
        "company_name": lead.company,
        "personalization": (lead.email_1_body or lead.hook or "")[:500],
        "variables": {
            "company": lead.company,
            "pain": lead.pain or "",
            "signal": lead.signal or "",
            "icp_score": str(lead.icp_score),
        },
    }

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{INSTANTLY_API}/leads", headers=headers, json=payload)
        if r.status_code in (200, 201):
            lid = r.json().get("id", "")
            logger.info(f"Instantly enrolled: {lead.email} campaign={campaign_id[:8]}...")
            return lid
        logger.error(f"Instantly enrol failed {r.status_code}: {r.text[:200]}")
    return None
