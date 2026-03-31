"""
HubSpot CRM integration.
Creates contacts, deals, checks for duplicates, queries pipeline.
Pipeline: CloudiQS Engine
"""

import logging
from datetime import datetime
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_secret, is_dummy
from app.models import LeadPayload, IngestPayload

logger = logging.getLogger("bridge")

HUBSPOT_BASE = "https://api.hubapi.com"


def _is_retryable(exc: BaseException) -> bool:
    """Retry on 5xx responses and network errors; not on 4xx client errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))


_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)


async def _get_headers() -> Optional[dict]:
    key = get_secret("hubspot/api-key")
    if is_dummy(key):
        return None
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


# ── Read operations ──────────────────────────────────────────────────────────

async def check_contact_exists(email: str) -> Optional[str]:
    """Check if a contact already exists in HubSpot. Returns contact ID or None."""
    headers = await _get_headers()
    if not headers:
        return None

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search",
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
            r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                cid = results[0]["id"]
                logger.info(f"HubSpot contact exists: {email} -> {cid}")
                return cid
            return None

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot check_contact_exists failed: {e}")
        return None


async def check_deal_exists(company: str) -> Optional[str]:
    """Check if a deal already exists for this company.
    Uses exact company name match to avoid false positives.
    Returns deal ID or None.
    """
    headers = await _get_headers()
    if not headers:
        return None

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/deals/search",
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
            r.raise_for_status()
            results = r.json().get("results", [])
            for deal in results:
                deal_name = deal.get("properties", {}).get("dealname", "")
                if company.lower() in deal_name.lower():
                    did = deal["id"]
                    logger.info(f"HubSpot deal exists for {company}: {did}")
                    return did
            return None

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot check_deal_exists failed: {e}")
        return None


async def search_deals_by_stage(stage: str, limit: int = 20) -> list:
    """Search HubSpot deals by pipeline stage.

    Args:
        stage: HubSpot pipeline stage ID (e.g. 'decisionmakerboughtin', 'qualifiedtobuy')
        limit: Maximum number of deals to return (max 100)

    Returns:
        List of deal dicts with id, dealname, stage, campaign_vertical,
        ace_opportunity_id, sow_url, and associated contact.
    """
    headers = await _get_headers()
    if not headers:
        return []

    properties = [
        "dealname", "dealstage", "campaign_vertical", "icp_score",
        "signal", "pain_summary", "recommended_play",
        "ace_opportunity_id", "sow_url", "sow_status",
        "companies_house_number", "amount", "closedate",
    ]

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/deals/search",
                headers=headers,
                json={
                    "filterGroups": [{
                        "filters": [{
                            "propertyName": "dealstage",
                            "operator": "EQ",
                            "value": stage,
                        }]
                    }],
                    "properties": properties,
                    "limit": min(limit, 100),
                    "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
                },
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            deals = []
            for d in results:
                props = d.get("properties", {})
                deals.append({
                    "deal_id": d["id"],
                    "dealname": props.get("dealname", ""),
                    "stage": props.get("dealstage", ""),
                    "campaign": props.get("campaign_vertical", ""),
                    "icp_score": props.get("icp_score", ""),
                    "pain_summary": props.get("pain_summary", ""),
                    "recommended_play": props.get("recommended_play", ""),
                    "ace_opportunity_id": props.get("ace_opportunity_id", ""),
                    "sow_url": props.get("sow_url", ""),
                    "sow_status": props.get("sow_status", ""),
                    "amount": props.get("amount", ""),
                })
            logger.info(f"HubSpot pipeline query: stage={stage} -> {len(deals)} deals")
            return deals

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot search_deals_by_stage failed: {e}")
        return []


async def search_deals(filters: dict, limit: int = 20) -> list:
    """Generic deal search with arbitrary filters.

    Args:
        filters: Dict of propertyName -> value pairs (all joined with AND)
        limit: Maximum deals to return

    Returns:
        List of deal dicts (same structure as search_deals_by_stage)
    """
    headers = await _get_headers()
    if not headers:
        return []

    filter_list = [
        {"propertyName": k, "operator": "EQ", "value": v}
        for k, v in filters.items()
    ]
    properties = [
        "dealname", "dealstage", "campaign_vertical", "icp_score",
        "signal", "pain_summary", "recommended_play",
        "ace_opportunity_id", "sow_url", "sow_status", "amount",
    ]

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/deals/search",
                headers=headers,
                json={
                    "filterGroups": [{"filters": filter_list}],
                    "properties": properties,
                    "limit": min(limit, 100),
                },
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            return [
                {
                    "deal_id": d["id"],
                    **{k: d.get("properties", {}).get(k, "") for k in properties},
                }
                for d in results
            ]

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot search_deals failed: {e}")
        return []


async def get_deal_details(deal_id: str) -> Optional[dict]:
    """Fetch all properties for a specific deal.

    Returns:
        Dict of deal properties, or None if not found.
    """
    headers = await _get_headers()
    if not headers:
        return None

    properties = [
        "dealname", "dealstage", "pipeline", "campaign_vertical",
        "icp_score", "signal", "pain_summary", "recommended_play",
        "ace_opportunity_id", "sow_url", "sow_status",
        "companies_house_number", "amount", "closedate", "createdate",
    ]
    params = "&".join(f"properties={p}" for p in properties)

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{HUBSPOT_BASE}/crm/v3/objects/deals/{deal_id}?{params}",
                headers=headers,
            )
            r.raise_for_status()
            d = r.json()
            return {"deal_id": d["id"], **d.get("properties", {})}

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot get_deal_details({deal_id}) failed: {e}")
        return None


async def get_pipeline_counts() -> dict:
    """Get deal counts by pipeline stage for ops-pipeline-report agent."""
    headers = await _get_headers()
    if not headers:
        return {}

    # HubSpot pipeline stage IDs for CloudiQS Engine pipeline
    stages = [
        "appointmentscheduled",   # New Lead
        "qualifiedtobuy",          # Qualified
        "presentationscheduled",   # Proposal
        "decisionmakerboughtin",   # Proposal Sent
        "contractsent",            # Negotiation
        "closedwon",               # Closed Won
        "closedlost",              # Closed Lost
    ]
    counts = {}

    async def _count_stage(stage: str) -> int:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"{HUBSPOT_BASE}/crm/v3/objects/deals/search",
                    headers=headers,
                    json={
                        "filterGroups": [{"filters": [{
                            "propertyName": "dealstage",
                            "operator": "EQ",
                            "value": stage,
                        }]}],
                        "limit": 1,
                    },
                )
                if r.status_code == 200:
                    return r.json().get("total", 0)
        except Exception as e:
            logger.warning(f"HubSpot stage count for {stage} failed: {e}")
        return 0

    import asyncio
    results = await asyncio.gather(*[_count_stage(s) for s in stages])
    for stage, count in zip(stages, results):
        counts[stage] = count

    logger.info(f"HubSpot pipeline counts: {counts}")
    return counts


# ── Write operations ─────────────────────────────────────────────────────────

async def update_deal_property(deal_id: str, property_name: str, value: str) -> bool:
    """Update a single property on a HubSpot deal.

    Args:
        deal_id: HubSpot deal ID
        property_name: HubSpot property name (e.g. 'sow_status', 'dealstage')
        value: New value to set

    Returns:
        True if updated successfully, False otherwise.
    """
    headers = await _get_headers()
    if not headers:
        return False

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.patch(
                f"{HUBSPOT_BASE}/crm/v3/objects/deals/{deal_id}",
                headers=headers,
                json={"properties": {property_name: value}},
            )
            r.raise_for_status()
            logger.info(f"HubSpot deal {deal_id}: {property_name}={value}")
            return True

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot update_deal_property({deal_id}) failed: {e}")
        return False


async def create_contact(lead: LeadPayload) -> Optional[str]:
    """Create a HubSpot contact with all available fields."""
    headers = await _get_headers()
    if not headers:
        return None

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
    props = {k: v for k, v in props.items() if v}

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/contacts",
                headers=headers,
                json={"properties": props},
            )
            if r.status_code in (200, 201):
                cid = r.json()["id"]
                logger.info(f"HubSpot contact created: {lead.email} -> {cid}")
                return cid
            if r.status_code == 409:
                logger.info(f"HubSpot contact already exists: {lead.email}")
                return await check_contact_exists(lead.email)
            r.raise_for_status()
            return None

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot create_contact failed: {e}")
        return None


async def create_deal(lead: LeadPayload, contact_id: str) -> Optional[str]:
    """Create a HubSpot deal and associate with contact."""
    headers = await _get_headers()
    if not headers:
        return None

    if lead.deal_name:
        deal_name = lead.deal_name
    else:
        deal_name = f"{lead.company} - {lead.campaign.upper()} - {datetime.now().strftime('%Y-%m')}"

    props = {
        "dealname": deal_name,
        "dealstage": "appointmentscheduled",
        "pipeline": "default",
        "campaign_vertical": lead.campaign,
        "icp_score": str(lead.icp_score) if lead.icp_score else "",
        "signal": (lead.signal or "")[:200],
    }
    props = {k: v for k, v in props.items() if v}

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/deals",
                headers=headers,
                json={"properties": props},
            )
            r.raise_for_status()
            did = r.json()["id"]

            assoc_url = (
                f"{HUBSPOT_BASE}/crm/v3/objects/deals/{did}"
                f"/associations/contacts/{contact_id}/deal_to_contact"
            )
            await c.put(assoc_url, headers=headers)

            logger.info(f"HubSpot deal created: {deal_name} -> {did}")
            return did

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot create_deal failed: {e}")
        return None


async def create_ingest_deal(payload: IngestPayload) -> Optional[str]:
    """Create a HubSpot deal from S3 upload / bulk ingest.
    No Instantly enrolment, no ACE. Just creates a deal for triage.
    """
    headers = await _get_headers()
    if not headers:
        return None

    existing = await check_deal_exists(payload.company)
    if existing:
        logger.info(f"Ingest skipping duplicate: {payload.company}")
        return existing

    deal_name = f"{payload.company} - {payload.campaign.upper()} - {datetime.now().strftime('%Y-%m')}"
    props = {
        "dealname": deal_name,
        "dealstage": "appointmentscheduled",
        "pipeline": "default",
        "campaign_vertical": payload.campaign,
    }

    @_retry
    async def _call():
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{HUBSPOT_BASE}/crm/v3/objects/deals",
                headers=headers,
                json={"properties": props},
            )
            r.raise_for_status()
            did = r.json()["id"]
            logger.info(f"Ingest deal created: {payload.company} -> {did}")
            return did

    try:
        return await _call()
    except Exception as e:
        logger.error(f"HubSpot create_ingest_deal failed: {e}")
        return None
