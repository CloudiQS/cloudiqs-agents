"""Unit tests for /ace/auto-create and /webhook/hubspot endpoints."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


# ── helpers ───────────────────────────────────────────────────────────────────

_QUALIFIED_DEAL = {
    "deal_id": "99001",
    "dealname": "Acme Corp - MSP",
    "dealstage": "qualifiedtobuy",
    "campaign_vertical": "msp",
    "icp_score": "7",
    "signal": "vmware exit",
    "pain_summary": "Needs cloud migration",
    "recommended_play": "MSP Managed Services",
    "ace_opportunity_id": "",
}

_WRONG_STAGE_DEAL = {**_QUALIFIED_DEAL, "dealstage": "appointmentscheduled", "ace_opportunity_id": ""}
_EXISTING_ACE_DEAL = {**_QUALIFIED_DEAL, "ace_opportunity_id": "O99001"}


# ── POST /ace/auto-create ─────────────────────────────────────────────────────

async def test_auto_create_missing_deal_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/auto-create", json={})
    assert r.status_code == 400
    assert "hubspot_deal_id" in r.json()["error"]


@patch("app.hubspot.get_deal_details", new_callable=AsyncMock, return_value=None)
async def test_auto_create_deal_not_found(mock_deal):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/auto-create", json={"hubspot_deal_id": "99999"})
    assert r.status_code == 404


@patch("app.hubspot.get_deal_details", new_callable=AsyncMock, return_value=_WRONG_STAGE_DEAL)
async def test_auto_create_skips_non_qualified_stage(mock_deal):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/auto-create", json={"hubspot_deal_id": "99001"})
    assert r.status_code == 200
    assert r.json()["status"] == "skipped"
    assert "qualifiedtobuy" in r.json()["reason"]


@patch("app.hubspot.get_deal_details", new_callable=AsyncMock, return_value=_EXISTING_ACE_DEAL)
async def test_auto_create_skips_existing_ace_id(mock_deal):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/auto-create", json={"hubspot_deal_id": "99001"})
    assert r.json()["status"] == "skipped"
    assert r.json()["ace_opportunity_id"] == "O99001"


@patch("app.ace_notifications.notify_created", new_callable=AsyncMock)
@patch("app.hubspot.update_deal_property", new_callable=AsyncMock, return_value=True)
@patch("app.ace.create_opportunity", new_callable=AsyncMock, return_value="O12345")
@patch("app.hubspot.get_deal_details", new_callable=AsyncMock, return_value=_QUALIFIED_DEAL)
async def test_auto_create_success(mock_deal, mock_create, mock_update, mock_notify):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/auto-create", json={"hubspot_deal_id": "99001"})
    assert r.json()["status"] == "created"
    assert r.json()["ace_opportunity_id"] == "O12345"
    mock_update.assert_called_once_with("99001", "ace_opportunity_id", "O12345")
    mock_notify.assert_called_once()


@patch("app.ace_notifications.notify_created", new_callable=AsyncMock)
@patch("app.hubspot.update_deal_property", new_callable=AsyncMock, return_value=True)
@patch("app.ace.create_opportunity", new_callable=AsyncMock, return_value=None)
@patch("app.hubspot.get_deal_details", new_callable=AsyncMock, return_value=_QUALIFIED_DEAL)
async def test_auto_create_ace_failure(mock_deal, mock_create, mock_update, mock_notify):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/auto-create", json={"hubspot_deal_id": "99001"})
    assert r.json()["status"] == "failed"
    mock_update.assert_not_called()


@patch("app.ace_notifications.notify_created", new_callable=AsyncMock)
@patch("app.hubspot.update_deal_property", new_callable=AsyncMock, return_value=True)
@patch("app.ace.create_opportunity", new_callable=AsyncMock, return_value="O12345")
@patch("app.hubspot.get_deal_details", new_callable=AsyncMock, return_value=_QUALIFIED_DEAL)
async def test_auto_create_uses_email_from_body(mock_deal, mock_create, mock_update, mock_notify):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/auto-create", json={
            "hubspot_deal_id": "99001",
            "email": "ceo@acme.com",
            "contact_name": "John Smith",
        })
    assert r.json()["status"] == "created"
    # Verify create_opportunity received a lead with the provided email
    lead_arg = mock_create.call_args[0][0]
    assert lead_arg.email == "ceo@acme.com"
    assert lead_arg.contact == "John Smith"


@patch("app.ace_notifications.notify_created", new_callable=AsyncMock)
@patch("app.hubspot.update_deal_property", new_callable=AsyncMock, return_value=True)
@patch("app.ace.create_opportunity", new_callable=AsyncMock, return_value="O12345")
@patch("app.hubspot.get_deal_details", new_callable=AsyncMock, return_value=_QUALIFIED_DEAL)
async def test_auto_create_placeholder_email_when_missing(mock_deal, mock_create, mock_update, mock_notify):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/auto-create", json={"hubspot_deal_id": "99001"})
    lead_arg = mock_create.call_args[0][0]
    assert "auto.cloudiqs.internal" in lead_arg.email


# ── POST /webhook/hubspot ─────────────────────────────────────────────────────

async def test_webhook_hubspot_returns_ok_on_empty():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/webhook/hubspot", json=[])
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_webhook_hubspot_ignores_non_stage_change():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/webhook/hubspot", json=[{
            "subscriptionType": "deal.propertyChange",
            "propertyName": "dealname",
            "propertyValue": "New Name",
            "objectId": 12345,
        }])
    assert r.status_code == 200


async def test_webhook_hubspot_ignores_non_qualified_stage():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/webhook/hubspot", json=[{
            "subscriptionType": "deal.propertyChange",
            "propertyName": "dealstage",
            "propertyValue": "appointmentscheduled",
            "objectId": 12345,
        }])
    assert r.status_code == 200


@patch("app.main.asyncio.create_task")
async def test_webhook_hubspot_fires_auto_create_on_qualified(mock_task):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/webhook/hubspot", json=[{
            "subscriptionType": "deal.propertyChange",
            "propertyName": "dealstage",
            "propertyValue": "qualifiedtobuy",
            "objectId": 99001,
        }])
    assert r.status_code == 200
    mock_task.assert_called_once()
