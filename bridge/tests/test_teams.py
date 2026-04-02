"""Unit tests for app.teams channel routing functions."""
from unittest.mock import AsyncMock, MagicMock, patch


# ── _post internal HTTP helper ───────────────────────────────────────────────

@patch("app.teams.is_dummy", return_value=False)
@patch("app.teams.get_secret", return_value="https://outlook.office.com/test-hook")
async def test_post_returns_true_on_200(mock_secret, mock_dummy):
    import httpx
    from app.teams import _post
    mock_resp = MagicMock(status_code=200)
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch("app.teams.httpx.AsyncClient", return_value=mock_client):
        result = await _post({"text": "hi"})
    assert result is True


@patch("app.teams.is_dummy", return_value=True)
@patch("app.teams.get_secret", return_value="DUMMY")
async def test_post_returns_false_when_dummy_url(mock_secret, mock_dummy):
    from app.teams import _post
    result = await _post({"text": "hi"})
    assert result is False


@patch("app.teams.get_secret", side_effect=Exception("secret not found"))
async def test_post_returns_false_on_secret_error(mock_secret):
    from app.teams import _post
    result = await _post({"text": "hi"})
    assert result is False


@patch("app.teams.is_dummy", return_value=False)
@patch("app.teams.get_secret", return_value="https://outlook.office.com/test-hook")
async def test_post_returns_false_on_non_200(mock_secret, mock_dummy):
    from app.teams import _post
    mock_resp = MagicMock(status_code=500)
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch("app.teams.httpx.AsyncClient", return_value=mock_client):
        result = await _post({"text": "hi"})
    assert result is False


# ── _adaptive_card helper ─────────────────────────────────────────────────────

def test_adaptive_card_envelope():
    from app.teams import _adaptive_card
    card = _adaptive_card([{"type": "TextBlock", "text": "hello"}])
    assert card["type"] == "message"
    content = card["attachments"][0]["content"]
    assert content["type"] == "AdaptiveCard"
    assert content["version"] == "1.4"
    assert content["body"][0]["text"] == "hello"


# ── post_to_sdr ───────────────────────────────────────────────────────────────

@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_sdr_uses_webhook_url(mock_post):
    from app.teams import post_to_sdr
    await post_to_sdr({"text": "hello"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/webhook-url"


# ── post_to_ceo ───────────────────────────────────────────────────────────────

@patch("app.teams.get_secret", return_value="https://outlook.office.com/ceo-hook")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ceo_uses_ceo_key_when_set(mock_post, mock_secret):
    from app.teams import post_to_ceo
    await post_to_ceo({"text": "briefing"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/ceo-webhook-url"


@patch("app.teams.get_secret", return_value="DUMMY")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ceo_falls_back_when_key_missing(mock_post, mock_secret):
    from app.teams import post_to_ceo
    await post_to_ceo({"text": "briefing"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/webhook-url"


# ── post_to_ace ───────────────────────────────────────────────────────────────

@patch("app.teams.get_secret", return_value="https://outlook.office.com/ace-hook")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ace_uses_ace_key_when_set(mock_post, mock_secret):
    from app.teams import post_to_ace
    await post_to_ace({"text": "ace update"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/ace-webhook-url"


@patch("app.teams.get_secret", return_value="DUMMY")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ace_falls_back_when_key_missing(mock_post, mock_secret):
    from app.teams import post_to_ace
    await post_to_ace({"text": "ace update"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/webhook-url"


# ── notify_lead routes to SDR ─────────────────────────────────────────────────

@patch("app.teams.post_to_sdr", new_callable=AsyncMock, return_value=True)
async def test_notify_lead_routes_to_sdr(mock_sdr):
    from app.teams import notify_lead
    await notify_lead({"company": "Acme", "campaign": "msp", "icp_score": 7})
    mock_sdr.assert_called_once()


@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_notify_lead_builds_adaptive_card(mock_post):
    from app.teams import notify_lead
    await notify_lead({
        "company": "Acme Ltd",
        "campaign": "msp",
        "icp_score": 9,
        "contact": "Jane Doe",
        "job_title": "CTO",
        "email": "jane@acme.com",
        "signal": "Hiring 5 cloud engineers",
        "pain": "Legacy VMware",
        "play": "Migration",
        "hubspot_deal_id": "hs-123",
        "instantly_lead_id": "il-456",
    })
    card = mock_post.call_args[0][0]
    body = card["attachments"][0]["content"]["body"]
    assert body[0]["text"] == "New Lead | ICP 9/10 | MSP"
    # Company FactSet
    company_fs = body[1]
    assert company_fs["type"] == "FactSet"
    assert any(f["title"] == "Company" and f["value"] == "Acme Ltd" for f in company_fs["facts"])
    # Contact FactSet
    contact_fs = body[2]
    assert any(f["title"] == "PRIMARY" and "Jane Doe" in f["value"] for f in contact_fs["facts"])
    assert any(f["title"] == "Email" for f in contact_fs["facts"])


@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_notify_lead_intel_factset_included(mock_post):
    from app.teams import notify_lead
    await notify_lead({
        "company": "Beta", "campaign": "vmware", "icp_score": 7,
        "signal": "3 cloud roles", "pain": "VMware exit", "play": "Migration",
    })
    card = mock_post.call_args[0][0]
    body = card["attachments"][0]["content"]["body"]
    fact_sets = [b for b in body if b.get("type") == "FactSet"]
    # Intel FactSet should be present
    intel_titles = [f["title"] for fs in fact_sets for f in fs["facts"]]
    assert "Signal" in intel_titles
    assert "Pain" in intel_titles


@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_notify_lead_optional_company_fields(mock_post):
    from app.teams import notify_lead
    await notify_lead({
        "company": "Acme Ltd",
        "campaign": "smb",
        "icp_score": 6,
        "website": "https://acme.com",
        "employees": 200,
        "location": "Manchester",
        "companies_house_number": "12345678",
        "linkedin_url": "https://linkedin.com/company/acme",
    })
    card = mock_post.call_args[0][0]
    body = card["attachments"][0]["content"]["body"]
    company_fs = body[1]
    fact_titles = [f["title"] for f in company_fs["facts"]]
    assert "Website" in fact_titles
    assert "Employees" in fact_titles
    assert "Location" in fact_titles
    assert "Companies House" in fact_titles


@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_notify_wraps_text_in_adaptive_card(mock_post):
    from app.teams import notify
    await notify("Test alert message")
    card = mock_post.call_args[0][0]
    body = card["attachments"][0]["content"]["body"]
    assert body[0]["text"] == "Test alert message"
    assert body[0]["wrap"] is True
