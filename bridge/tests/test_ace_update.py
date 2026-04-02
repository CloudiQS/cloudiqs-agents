"""Unit tests for /ace/update and /ace/update-opportunity endpoints."""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


# ── /ace/update ───────────────────────────────────────────────────────────────

async def test_ace_update_missing_opp_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/update", json={"customer_business_problem": "Some problem"})
    assert r.status_code == 400
    assert "ace_opportunity_id" in r.json()["error"]


@patch("app.ace.update_opportunity_fields", new_callable=AsyncMock, return_value=True)
async def test_ace_update_success(mock_update):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/update", json={
            "ace_opportunity_id": "O14608392",
            "customer_business_problem": "ShadAgro needs cloud migration",
            "website": "shadagro.com",
        })
    assert r.status_code == 200
    assert r.json()["status"] == "updated"
    assert r.json()["ace_opportunity_id"] == "O14608392"
    assert "customer_business_problem" in r.json()["fields"]
    assert "website" in r.json()["fields"]


@patch("app.ace.update_opportunity_fields", new_callable=AsyncMock, return_value=False)
async def test_ace_update_failure(mock_update):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/update", json={
            "ace_opportunity_id": "O14608392",
            "customer_business_problem": "Some problem",
        })
    assert r.json()["status"] == "failed"


@patch("app.ace.update_opportunity_fields", new_callable=AsyncMock, return_value=True)
async def test_ace_update_strips_empty_fields(mock_update):
    """Empty string values should not be passed to the ACE API."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/update", json={
            "ace_opportunity_id": "O14608392",
            "customer_business_problem": "Valid problem text",
            "website": "",  # empty — should be excluded
        })
    called_fields = mock_update.call_args[0][1]
    assert "website" not in called_fields


# ── /ace/update-opportunity (alias) ──────────────────────────────────────────

@patch("app.ace.update_opportunity_fields", new_callable=AsyncMock, return_value=True)
async def test_ace_update_opportunity_alias(mock_update):
    """POST /ace/update-opportunity delegates to same logic as /ace/update."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/update-opportunity", json={
            "ace_opportunity_id": "O14608392",
            "customer_business_problem": "ShadAgro migrating workloads to AWS",
            "website": "shadagro.com",
        })
    assert r.status_code == 200
    assert r.json()["status"] == "updated"
    assert r.json()["ace_opportunity_id"] == "O14608392"


async def test_ace_update_opportunity_missing_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/ace/update-opportunity", json={"website": "test.com"})
    assert r.status_code == 400
