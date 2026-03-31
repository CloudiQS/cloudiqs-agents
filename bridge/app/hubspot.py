"""
HubSpot CRM integration.
Creates contacts, deals, checks for duplicates.
Pipeline: CloudiQS Engine
"""

import logging
from datetime import datetime
from typing import Optional

import httpx

from app.config import get_secret, is_dummy
from app.models import LeadPayload, IngestPayload

logger = logging.getLogger("bridge")


async def _get_headers():
    key = get_secret("hubspot/api-key")
    if is_dummy(key):
        return None
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def check_contact_exists(email: str) -> Optional[str]:
    """Check if a contact already exists in HubSpot. Returns contact ID or None."""
    headers = await _get_headers()
    if not headers:
        return None

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://api.hubapi.com/crm/v3/objects/contacts/search",
            headers=headers,
            json={
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "email",
                        "operator": "EQ",
                        "value": email,
                    }]
                }]
            },
        )
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                cid = results[0]["id"]
                logger.info(f"HubSpot contact exists: {email} -> {cid}")
                return cid
    return None


async def check_deal_exists(company: str) -> Optional[str]:
    """Check if a deal already exists for this company.
    Uses exact company name match to avoid false positives.
    Returns deal ID or None.
    """
    headers = await _get_headers()
    if not headers:
        return None

    # Search deals by exact company name in dealname
    # CONTAINS_TOKEN would match partial strings (e.g. "Acme" matches "Academy")
    # Instead, search for deals where the name starts with the company name
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://api.hubapi.com/crm/v3/objects/deals/search",
            headers=headers,
            json={
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "dealname",
                        "operator": "CONTAINS_TOKEN",
                        "value": company,
                    }]
                }],
                "limit": 5,
            },
        )
        if r.status_code == 200:
            results = r.json().get("results", [])
            # Verify the company name actually appears in the deal name
            # to avoid false positives from CONTAINS_TOKEN
            for deal in results:
                deal_name = deal.get("properties", {}).get("dealname", "")
                if company.lower() in deal_name.lower():
                    did = deal["id"]
                    logger.info(f"HubSpot deal exists for {company}: {did}")
                    return did
    return None


async def create_contact(lead: LeadPayload) -> Optional[str]:
    """Create a HubSpot contact with all available fields."""
    headers = await _get_headers()
    if not headers:
        return None

    # Check for duplicate first
    existing = await check_contact_exists(lead.email)
    if existing:
        logger.info(f"Skipping duplicate contact: {lead.email}")
        return existing

    parts = lead.contact.strip().split(" ", 1) if lead.contact else ["", ""]
    props = {
        "email": lead.email,
        "firstname": parts[0] if parts else "",
        "lastname": parts[1] if len(parts) > 1 else "",
        "company": lead.company,
        "jobtitle": lead.job_title,
        "phone": lead.phone,
        "website": lead.website,
        "city": lead.location,
        "icp_score": str(lead.icp_score) if lead.icp_score else "",
        "signal": (lead.signal or "")[:200],
        "pain_summary": (lead.pain or "")[:500],
        "recommended_play": (lead.play or "")[:500],
        "campaign_vertical": lead.campaign,
        "companies_house_number": lead.companies_house_number,
        "aws_services_deployed": (lead.aws_services or "")[:200],
        "linkedin_url": lead.linkedin_url,
    }
    # Remove empty values
    props = {k: v for k, v in props.items() if v}

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers=headers,
            json={"properties": props},
        )
        if r.status_code in (200, 201):
            cid = r.json()["id"]
            logger.info(f"HubSpot contact created: {lead.email} -> {cid}")
            return cid
        # 409 = duplicate
        if r.status_code == 409:
            logger.info(f"HubSpot contact already exists: {lead.email}")
            return await check_contact_exists(lead.email)
        logger.error(f"HubSpot contact create failed {r.status_code}: {r.text[:200]}")
    return None


async def create_deal(lead: LeadPayload, contact_id: str) -> Optional[str]:
    """Create a HubSpot deal and associate with contact."""
    headers = await _get_headers()
    if not headers:
        return None

    # Build deal name from agent data or generate default
    if lead.deal_name:
        deal_name = lead.deal_name
    else:
        deal_name = f"{lead.company} - {lead.campaign.upper()} - {datetime.now().strftime('%Y-%m')}"

    props = {
        "dealname": deal_name,
        "dealstage": "appointmentscheduled",  # New Lead
        "pipeline": "default",
        "campaign_vertical": lead.campaign,
        "icp_score": str(lead.icp_score) if lead.icp_score else "",
        "signal": (lead.signal or "")[:200],
    }
    props = {k: v for k, v in props.items() if v}

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://api.hubapi.com/crm/v3/objects/deals",
            headers=headers,
            json={"properties": props},
        )
        if r.status_code in (200, 201):
            did = r.json()["id"]

            # Associate contact to deal
            assoc_url = f"https://api.hubapi.com/crm/v3/objects/deals/{did}/associations/contacts/{contact_id}/deal_to_contact"
            await c.put(assoc_url, headers=headers)

            logger.info(f"HubSpot deal created: {deal_name} -> {did}")
            return did
        logger.error(f"HubSpot deal create failed {r.status_code}: {r.text[:200]}")
    return None


async def create_ingest_deal(payload: IngestPayload) -> Optional[str]:
    """Create a HubSpot deal from S3 upload / bulk ingest.
    No Instantly enrolment, no ACE. Just creates a deal for triage.
    """
    headers = await _get_headers()
    if not headers:
        return None

    # Check for existing deal with same company
    existing = await check_deal_exists(payload.company)
    if existing:
        logger.info(f"Ingest skipping duplicate: {payload.company}")
        return existing

    deal_name = f"{payload.company} - {payload.campaign.upper()} - {datetime.now().strftime('%Y-%m')}"
    props = {
        "dealname": deal_name,
        "dealstage": "appointmentscheduled",  # New Lead
        "pipeline": "default",
        "campaign_vertical": payload.campaign,
    }

    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://api.hubapi.com/crm/v3/objects/deals",
            headers=headers,
            json={"properties": props},
        )
        if r.status_code in (200, 201):
            did = r.json()["id"]
            logger.info(f"Ingest deal created: {payload.company} -> {did}")
            return did
        logger.error(f"Ingest deal create failed {r.status_code}: {r.text[:200]}")
    return None
