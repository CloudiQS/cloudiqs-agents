"""Unit tests for app.teams channel routing and card building."""
from unittest.mock import AsyncMock, MagicMock, patch


# ── _post_raw internal HTTP helper ────────────────────────────────────────────

@patch("app.teams.is_dummy", return_value=False)
@patch("app.teams.get_secret", return_value="https://outlook.office.com/test-hook")
async def test_post_raw_returns_true_on_200(mock_secret, mock_dummy):
    from app.teams import _post_raw
    mock_resp = MagicMock(status_code=200)
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch("app.teams.httpx.AsyncClient", return_value=mock_client):
        result = await _post_raw({"text": "hi"})
    assert result is True


@patch("app.teams.is_dummy", return_value=True)
@patch("app.teams.get_secret", return_value="DUMMY")
async def test_post_raw_returns_false_when_dummy_url(mock_secret, mock_dummy):
    from app.teams import _post_raw
    result = await _post_raw({"text": "hi"})
    assert result is False


@patch("app.teams.get_secret", side_effect=Exception("secret not found"))
async def test_post_raw_returns_false_on_secret_error(mock_secret):
    from app.teams import _post_raw
    result = await _post_raw({"text": "hi"})
    assert result is False


@patch("app.teams.is_dummy", return_value=False)
@patch("app.teams.get_secret", return_value="https://outlook.office.com/test-hook")
async def test_post_raw_returns_false_on_non_200(mock_secret, mock_dummy):
    from app.teams import _post_raw
    mock_resp = MagicMock(status_code=500)
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    with patch("app.teams.httpx.AsyncClient", return_value=mock_client):
        result = await _post_raw({"text": "hi"})
    assert result is False


# ── _post adaptive→fallback logic ─────────────────────────────────────────────

@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_post_tries_adaptive_card_first(mock_raw):
    from app.teams import _post
    await _post("Title", "Body")
    first_call_payload = mock_raw.call_args_list[0][0][0]
    # Adaptive Card has "type": "message" and "attachments"
    assert first_call_payload.get("type") == "message"
    assert "attachments" in first_call_payload


@patch("app.teams._post_raw", new_callable=AsyncMock, side_effect=[False, True])
async def test_post_falls_back_to_simple_on_non_200(mock_raw):
    from app.teams import _post
    result = await _post("Title", "Body")
    assert result is True
    assert mock_raw.call_count == 2
    # Second call is simple format
    second_payload = mock_raw.call_args_list[1][0][0]
    assert "attachments" not in second_payload
    assert "title" in second_payload


# ── _build_adaptive_card ──────────────────────────────────────────────────────

def test_adaptive_card_structure():
    from app.teams import _build_adaptive_card
    card = _build_adaptive_card("My Title", "My body", facts=[{"title": "K", "value": "V"}])
    body = card["attachments"][0]["content"]["body"]
    assert body[0]["text"] == "My Title"
    assert body[0]["weight"] == "bolder"
    assert body[1]["type"] == "FactSet"
    assert body[2]["text"] == "My body"


def test_adaptive_card_no_facts():
    from app.teams import _build_adaptive_card
    card = _build_adaptive_card("Title", "Body")
    body = card["attachments"][0]["content"]["body"]
    # No FactSet when facts is None
    assert all(b.get("type") != "FactSet" for b in body)


# ── Named channel routing ─────────────────────────────────────────────────────

@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_sdr_uses_webhook_url(mock_post):
    from app.teams import post_to_sdr
    await post_to_sdr("Title", "Body")
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/webhook-url"


@patch("app.teams.get_secret", return_value="https://outlook.office.com/ceo-hook")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ceo_uses_ceo_key_when_set(mock_post, mock_secret):
    from app.teams import post_to_ceo
    await post_to_ceo("Title", "Body")
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/ceo-webhook-url"


@patch("app.teams.get_secret", return_value="DUMMY")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ceo_falls_back_when_key_missing(mock_post, mock_secret):
    from app.teams import post_to_ceo
    await post_to_ceo("Title", "Body")
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/webhook-url"


@patch("app.teams.get_secret", return_value="https://outlook.office.com/ace-hook")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ace_uses_ace_key_when_set(mock_post, mock_secret):
    from app.teams import post_to_ace
    await post_to_ace("Title", "Body")
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/ace-webhook-url"


@patch("app.teams.get_secret", return_value="DUMMY")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ace_falls_back_when_key_missing(mock_post, mock_secret):
    from app.teams import post_to_ace
    await post_to_ace("Title", "Body")
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/webhook-url"


# ── notify_lead routes to SDR ─────────────────────────────────────────────────

@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_notify_lead_routes_to_sdr(mock_raw):
    from app.teams import notify_lead
    await notify_lead({"company": "Acme", "campaign": "msp", "icp_score": 7})
    mock_raw.assert_called_once()
    assert mock_raw.call_args[0][1] == "teams/webhook-url"


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_notify_lead_builds_card(mock_raw):
    import json
    from app.teams import notify_lead
    await notify_lead({
        "company": "Acme Ltd", "campaign": "msp", "icp_score": 9,
        "contact": "Jane Doe", "job_title": "CTO", "email": "jane@acme.com",
        "signal": "Hiring cloud engineers", "pain": "VMware exit",
        "hubspot_deal_id": "hs-123",
    })
    card_json = json.dumps(mock_raw.call_args[0][0])
    assert "ICP 9/10" in card_json
    assert "MSP" in card_json
    assert "Acme Ltd" in card_json
    assert "Hiring cloud engineers" in card_json
    # ICP 9 -> "good" style header
    assert "good" in card_json


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_notify_lead_optional_fields(mock_raw):
    import json
    from app.teams import notify_lead
    await notify_lead({
        "company": "Acme", "campaign": "smb", "icp_score": 6,
        "website": "https://acme.com", "employees": 200,
        "location": "Manchester", "companies_house_number": "12345678",
        "linkedin_url": "https://linkedin.com/company/acme",
        "phone": "07700900000",
    })
    card_json = json.dumps(mock_raw.call_args[0][0])
    assert "acme.com" in card_json
    assert "200" in card_json
    assert "Manchester" in card_json
    assert "12345678" in card_json
    assert "linkedin.com" in card_json
    assert "07700900000" in card_json


@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_notify_wraps_text(mock_post):
    from app.teams import notify
    await notify("Test alert message")
    assert mock_post.call_args[0][0] == "Alert"
    assert mock_post.call_args[0][1] == "Test alert message"
