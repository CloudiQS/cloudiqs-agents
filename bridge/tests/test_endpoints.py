"""
Integration tests for CloudiQS Bridge API endpoints.
External APIs (HubSpot, Instantly, Teams, Bedrock) are mocked.
"""
import pytest
from unittest.mock import AsyncMock, patch


# ── Health endpoint (no auth required) ───────────────────────────────────────

def test_health_no_auth_required(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "version" in data
    assert "webhook_events_total" in data


def test_health_returns_correct_structure(client):
    r = client.get("/health")
    data = r.json()
    assert all(k in data for k in ["status", "version", "uptime_seconds", "auth_enabled"])


# ── Auth enforcement ──────────────────────────────────────────────────────────

def test_lead_requires_auth(client):
    r = client.post("/lead", json={})
    assert r.status_code == 401


def test_stats_requires_auth(client):
    r = client.get("/stats")
    assert r.status_code == 401


def test_deals_pipeline_requires_auth(client):
    r = client.get("/deals/pipeline")
    assert r.status_code == 401


def test_wrong_api_key_rejected(client):
    r = client.get("/stats", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401


def test_correct_api_key_accepted(client, auth):
    r = client.get("/stats", headers=auth)
    assert r.status_code == 200


# ── /lead endpoint ────────────────────────────────────────────────────────────

def test_lead_missing_fields_returns_422(client, auth):
    r = client.post("/lead", json={"email": "only@email.com"}, headers=auth)
    assert r.status_code == 422


@patch("app.hubspot.create_contact", new_callable=AsyncMock, return_value="hs-contact-123")
@patch("app.hubspot.create_deal", new_callable=AsyncMock, return_value="hs-deal-456")
@patch("app.instantly.enrol", new_callable=AsyncMock, return_value="inst-lead-789")
@patch("app.teams.notify_lead", new_callable=AsyncMock, return_value=True)
def test_lead_full_pipeline(mock_teams, mock_inst, mock_deal, mock_contact, client, auth, valid_lead):
    r = client.post("/lead", json=valid_lead, headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "created"
    assert data["hubspot_contact_id"] == "hs-contact-123"
    assert data["hubspot_deal_id"] == "hs-deal-456"
    assert data["instantly_lead_id"] == "inst-lead-789"
    mock_contact.assert_called_once()
    mock_deal.assert_called_once()
    mock_inst.assert_called_once()
    mock_teams.assert_called_once()


@patch("app.hubspot.create_contact", new_callable=AsyncMock, return_value=None)
@patch("app.teams.notify_lead", new_callable=AsyncMock, return_value=True)
def test_lead_hubspot_failure_still_returns_200(mock_teams, mock_contact, client, auth, valid_lead):
    """If HubSpot is down, lead endpoint should not raise 500."""
    r = client.post("/lead", json=valid_lead, headers=auth)
    assert r.status_code == 200
    assert r.json()["hubspot_contact_id"] is None


# ── /stats endpoint ───────────────────────────────────────────────────────────

def test_stats_returns_daily_counts(client, auth):
    r = client.get("/stats", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert "total_leads" in data
    assert "by_campaign" in data


# ── /webhook/instantly endpoints ──────────────────────────────────────────────

@patch("app.teams.notify", new_callable=AsyncMock, return_value=True)
def test_webhook_stores_event(mock_teams, client, auth):
    payload = {
        "event_type": "reply",
        "email": "lead@prospect.com",
        "reply_text": "Interested, please send more info",
        "campaign_id": "camp-abc123",
    }
    r = client.post("/webhook/instantly", json=payload, headers=auth)
    assert r.status_code == 200
    assert r.json()["status"] == "received"


def test_webhook_recent_returns_events(client, auth):
    """After storing an event, /recent should return it."""
    with patch("app.teams.notify", new_callable=AsyncMock):
        client.post(
            "/webhook/instantly",
            json={"event_type": "open", "email": "a@b.com"},
            headers=auth,
        )

    r = client.get("/webhook/instantly/recent?unprocessed_only=false", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert "events" in data
    assert "total" in data


def test_webhook_mark_processed(client, auth):
    with patch("app.teams.notify", new_callable=AsyncMock):
        client.post(
            "/webhook/instantly",
            json={"event_type": "open", "email": "mark@test.com"},
            headers=auth,
        )

    # Get the timestamp
    r = client.get("/webhook/instantly/recent?unprocessed_only=true", headers=auth)
    events = r.json().get("events", [])

    if events:
        ts = events[0]["timestamp"]
        r2 = client.post(
            "/webhook/instantly/mark-processed",
            json={"timestamps": [ts]},
            headers=auth,
        )
        assert r2.status_code == 200
        assert r2.json()["marked"] >= 1


# ── /deals endpoints ──────────────────────────────────────────────────────────

@patch("app.hubspot.search_deals_by_stage", new_callable=AsyncMock, return_value=[])
def test_deals_pipeline_empty(mock_search, client, auth):
    r = client.get("/deals/pipeline?stage=qualifiedtobuy", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["stage"] == "qualifiedtobuy"
    assert data["count"] == 0
    assert data["deals"] == []


@patch("app.hubspot.search_deals_by_stage", new_callable=AsyncMock, return_value=[
    {"deal_id": "123", "dealname": "Acme - MSP", "stage": "qualifiedtobuy", "campaign": "msp"}
])
def test_deals_pipeline_with_results(mock_search, client, auth):
    r = client.get("/deals/pipeline?stage=qualifiedtobuy", headers=auth)
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_deals_search_requires_filter(client, auth):
    r = client.get("/deals/search", headers=auth)
    assert r.status_code == 400


@patch("app.hubspot.update_deal_property", new_callable=AsyncMock, return_value=True)
def test_deal_update(mock_update, client, auth):
    r = client.post(
        "/deals/hs-deal-123/update",
        json={"property": "sow_status", "value": "Draft"},
        headers=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "updated"
    assert data["deal_id"] == "hs-deal-123"
    mock_update.assert_called_once_with("hs-deal-123", "sow_status", "Draft")


def test_deal_update_missing_fields(client, auth):
    r = client.post("/deals/123/update", json={"property": "sow_status"}, headers=auth)
    assert r.status_code == 400


# ── /mcp/architecture endpoint ────────────────────────────────────────────────

@patch("app.architect.generate_architecture", new_callable=AsyncMock,
       return_value="## Architecture\n\nASCII diagram here...")
def test_architecture_returns_markdown(mock_arch, client, auth):
    r = client.post(
        "/mcp/architecture",
        json={
            "requirements": "VMware exit, 80 servers, eu-west-1 required",
            "service_type": "vmware",
            "company": "Acme Ltd",
        },
        headers=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert "architecture" in data
    assert "company" in data
    assert data["company"] == "Acme Ltd"
    mock_arch.assert_called_once()


@patch("app.architect.generate_architecture", new_callable=AsyncMock, return_value=None)
def test_architecture_fallback_when_bedrock_unavailable(mock_arch, client, auth):
    """If Bedrock is unavailable, a fallback [TBC] response is returned."""
    r = client.post(
        "/mcp/architecture",
        json={"requirements": "test", "service_type": "msp", "company": "Beta Corp"},
        headers=auth,
    )
    assert r.status_code == 200
    data = r.json()
    assert "[TBC" in data["architecture"]


def test_architecture_missing_requirements(client, auth):
    r = client.post(
        "/mcp/architecture",
        json={"service_type": "msp", "company": "Test"},
        headers=auth,
    )
    assert r.status_code == 400


# ── /config/companies-house-key ───────────────────────────────────────────────

@patch("app.config.get_secret", return_value="test-ch-key-abc")
def test_companies_house_key_returns_key(mock_secret, client, auth):
    r = client.get("/config/companies-house-key", headers=auth)
    assert r.status_code == 200
    assert r.json()["api_key"] == "test-ch-key-abc"


@patch("app.config.get_secret", return_value="DUMMY")
def test_companies_house_key_returns_503_when_not_configured(mock_secret, client, auth):
    r = client.get("/config/companies-house-key", headers=auth)
    assert r.status_code == 503


# ── /check lead ───────────────────────────────────────────────────────────────

@patch("app.hubspot.check_contact_exists", new_callable=AsyncMock, return_value="hs-123")
def test_check_lead_exists(mock_check, client, auth):
    r = client.get("/lead?email=test@example.com", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["exists"] is True
    assert data["contact_id"] == "hs-123"


def test_check_lead_no_email(client, auth):
    r = client.get("/lead", headers=auth)
    assert r.status_code == 200
    assert r.json()["exists"] is False


# ── /ceo/briefing endpoint ────────────────────────────────────────────────────

_BRIEFING_RESULT = {
    "date": "07 Apr 2026",
    "is_monday": False,
    "pipeline": "5 Committed",
    "action_required": "Acme: submit win wire",
    "closing_soon": "Acme closes 10 Apr",
    "aws_stage": "8 confirmed",
    "cosell": "Bob Smith engaged",
    "funding": "Acme eligible MAP",
    "weekly": {},
    "leads_today": 5,
}


@patch("app.ceo_briefing.post_briefing_to_teams", new_callable=AsyncMock, return_value=True)
@patch("app.ceo_briefing.run_briefing", new_callable=AsyncMock, return_value=_BRIEFING_RESULT)
def test_ceo_briefing_post_returns_complete(mock_run, mock_post, client, auth):
    r = client.post("/ceo/briefing", headers=auth)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "complete"
    assert data["date"] == "07 Apr 2026"
    assert data["leads_today"] == 5
    mock_run.assert_called_once()
    mock_post.assert_called_once()


@patch("app.ceo_briefing.run_briefing", new_callable=AsyncMock, return_value=_BRIEFING_RESULT)
def test_ceo_briefing_get_returns_json_no_teams(mock_run, client, auth):
    r = client.get("/ceo/briefing", headers=auth)
    assert r.status_code == 200
    data = r.json()
    # GET returns full briefing dict, not posting to Teams
    assert "pipeline" in data
    assert "action_required" in data
    assert "aws_stage" in data
    assert "funding" in data
    mock_run.assert_called_once()
