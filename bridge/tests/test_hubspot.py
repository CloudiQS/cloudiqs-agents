"""
Unit tests for HubSpot CRM integration.
All HubSpot API calls are mocked — no network required.
"""
import pytest
import httpx
import respx
from unittest.mock import patch, AsyncMock

from app.models import LeadPayload, IngestPayload

HUBSPOT_BASE = "https://api.hubapi.com"

# Fixture: patch HubSpot auth header for all tests
@pytest.fixture(autouse=True)
def mock_hs_headers():
    with patch("app.hubspot._get_headers", return_value={
        "Authorization": "Bearer test-hs-key",
        "Content-Type": "application/json",
    }):
        yield


def _make_lead(**kwargs) -> LeadPayload:
    defaults = {
        "email": "test@company.com",
        "company": "Test Ltd",
        "contact": "Jane Doe",
        "campaign": "msp",
        "signal": "hiring signal",
        "pain": "VMware exit pain",
        "play": "Migration",
        "icp_score": 7,
    }
    defaults.update(kwargs)
    return LeadPayload(**defaults)


# ── check_contact_exists ──────────────────────────────────────────────────────

@respx.mock
async def test_check_contact_exists_found():
    from app.hubspot import check_contact_exists
    respx.post(f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "hs-123"}]})
    )
    result = await check_contact_exists("test@company.com")
    assert result == "hs-123"


@respx.mock
async def test_check_contact_exists_not_found():
    from app.hubspot import check_contact_exists
    respx.post(f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    result = await check_contact_exists("new@company.com")
    assert result is None


@respx.mock
async def test_check_contact_exists_returns_none_on_error():
    from app.hubspot import check_contact_exists
    respx.post(f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(500, json={"error": "server error"})
    )
    result = await check_contact_exists("test@company.com")
    assert result is None


# ── search_deals_by_stage ─────────────────────────────────────────────────────

@respx.mock
async def test_search_deals_by_stage_returns_list():
    from app.hubspot import search_deals_by_stage
    respx.post(f"{HUBSPOT_BASE}/crm/v3/objects/deals/search").mock(
        return_value=httpx.Response(200, json={
            "results": [{
                "id": "deal-001",
                "properties": {
                    "dealname": "Acme - MSP - 2026-03",
                    "dealstage": "qualifiedtobuy",
                    "campaign_vertical": "msp",
                    "ace_opportunity_id": "",
                    "sow_url": "",
                }
            }]
        })
    )
    deals = await search_deals_by_stage("qualifiedtobuy")
    assert len(deals) == 1
    assert deals[0]["deal_id"] == "deal-001"
    assert deals[0]["campaign"] == "msp"


@respx.mock
async def test_search_deals_by_stage_empty():
    from app.hubspot import search_deals_by_stage
    respx.post(f"{HUBSPOT_BASE}/crm/v3/objects/deals/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    deals = await search_deals_by_stage("closedwon")
    assert deals == []


# ── update_deal_property ──────────────────────────────────────────────────────

@respx.mock
async def test_update_deal_property_success():
    from app.hubspot import update_deal_property
    respx.patch(f"{HUBSPOT_BASE}/crm/v3/objects/deals/deal-123").mock(
        return_value=httpx.Response(200, json={"id": "deal-123"})
    )
    result = await update_deal_property("deal-123", "sow_status", "Draft")
    assert result is True


@respx.mock
async def test_update_deal_property_not_found():
    from app.hubspot import update_deal_property
    respx.patch(f"{HUBSPOT_BASE}/crm/v3/objects/deals/bad-id").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    result = await update_deal_property("bad-id", "sow_status", "Draft")
    assert result is False


# ── create_contact ────────────────────────────────────────────────────────────

@respx.mock
async def test_create_contact_success():
    from app.hubspot import create_contact
    # No existing contact
    respx.post(f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    # Create succeeds
    respx.post(f"{HUBSPOT_BASE}/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(201, json={"id": "new-contact-456"})
    )
    lead = _make_lead()
    result = await create_contact(lead)
    assert result == "new-contact-456"


@respx.mock
async def test_create_contact_deduplicates():
    from app.hubspot import create_contact
    # Contact already exists
    respx.post(f"{HUBSPOT_BASE}/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "existing-789"}]})
    )
    lead = _make_lead()
    result = await create_contact(lead)
    assert result == "existing-789"


# ── get_deal_details ──────────────────────────────────────────────────────────

@respx.mock
async def test_get_deal_details_returns_dict():
    from app.hubspot import get_deal_details
    respx.get(url__regex=r".*deals/deal-001.*").mock(
        return_value=httpx.Response(200, json={
            "id": "deal-001",
            "properties": {
                "dealname": "Acme - MSP",
                "dealstage": "qualifiedtobuy",
                "campaign_vertical": "msp",
            }
        })
    )
    result = await get_deal_details("deal-001")
    assert result is not None
    assert result["deal_id"] == "deal-001"
    assert result["dealname"] == "Acme - MSP"
