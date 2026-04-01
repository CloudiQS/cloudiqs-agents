"""Unit tests for app.teams channel routing functions."""
from unittest.mock import AsyncMock, patch


# ── post_to_sdr ───────────────────────────────────────────────────────────────

@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_sdr_uses_webhook_url(mock_post):
    from app.teams import post_to_sdr
    await post_to_sdr({"text": "hello"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/webhook-url"


# ── post_to_ceo ───────────────────────────────────────────────────────────────

@patch("app.config.get_secret", return_value="https://outlook.office.com/ceo-hook")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ceo_uses_ceo_key_when_set(mock_post, mock_secret):
    from app.teams import post_to_ceo
    await post_to_ceo({"text": "briefing"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/ceo-webhook-url"


@patch("app.config.get_secret", return_value="DUMMY")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ceo_falls_back_when_key_missing(mock_post, mock_secret):
    from app.teams import post_to_ceo
    await post_to_ceo({"text": "briefing"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/webhook-url"


# ── post_to_ace ───────────────────────────────────────────────────────────────

@patch("app.config.get_secret", return_value="https://outlook.office.com/ace-hook")
@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_to_ace_uses_ace_key_when_set(mock_post, mock_secret):
    from app.teams import post_to_ace
    await post_to_ace({"text": "ace update"})
    _, kwargs = mock_post.call_args
    assert kwargs["webhook_key"] == "teams/ace-webhook-url"


@patch("app.config.get_secret", return_value="DUMMY")
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
