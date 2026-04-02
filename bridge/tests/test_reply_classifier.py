"""Unit tests for app.reply_classifier — Bedrock Haiku reply classification."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Auto-detection (no Bedrock call) ─────────────────────────────────────────

async def test_ooo_detected_without_bedrock():
    from app.reply_classifier import classify_reply
    result = await classify_reply("I am currently away from the office until Monday.")
    assert result["classification"] == "ooo"
    assert result["confidence"] == "high"


async def test_unsubscribe_detected_without_bedrock():
    from app.reply_classifier import classify_reply
    result = await classify_reply("Please remove me from your mailing list.")
    assert result["classification"] == "unsubscribe"


async def test_empty_reply_returns_bounce():
    from app.reply_classifier import classify_reply
    result = await classify_reply("")
    assert result["classification"] == "bounce"


async def test_whitespace_reply_returns_bounce():
    from app.reply_classifier import classify_reply
    result = await classify_reply("   ")
    assert result["classification"] == "bounce"


# ── Bedrock classification ────────────────────────────────────────────────────

def _make_bedrock_response(label: str, confidence: str = "high", reason: str = "clear signal") -> MagicMock:
    """Build a mock boto3 invoke_model response."""
    body_content = json.dumps({
        "content": [{"text": json.dumps({
            "classification": label,
            "confidence": confidence,
            "reason": reason,
        })}]
    })
    mock_body = MagicMock()
    mock_body.read.return_value = body_content.encode()
    mock_response = MagicMock()
    mock_response.__getitem__ = lambda self, k: mock_body if k == "body" else None
    return mock_response


@patch("app.reply_classifier._get_bedrock")
async def test_classify_interested(mock_bedrock_factory):
    mock_client = MagicMock()
    mock_bedrock_factory.return_value = mock_client
    mock_client.invoke_model.return_value = _make_bedrock_response("interested")

    from app.reply_classifier import classify_reply
    result = await classify_reply("Yes, I would love to learn more about this.", "ceo@acme.com", "vmware")
    assert result["classification"] == "interested"
    assert "call" in result["suggested_action"].lower() or "book" in result["suggested_action"].lower()


@patch("app.reply_classifier._get_bedrock")
async def test_classify_not_now(mock_bedrock_factory):
    mock_client = MagicMock()
    mock_bedrock_factory.return_value = mock_client
    mock_client.invoke_model.return_value = _make_bedrock_response("not_now", "high", "busy now")

    from app.reply_classifier import classify_reply
    result = await classify_reply("Not the right time for us at the moment.", "cto@beta.com", "msp")
    assert result["classification"] == "not_now"
    assert result["confidence"] == "high"


@patch("app.reply_classifier._get_bedrock")
async def test_classify_unknown_label_normalised(mock_bedrock_factory):
    """Invalid classification labels from Bedrock should be normalised to 'unknown'."""
    mock_client = MagicMock()
    mock_bedrock_factory.return_value = mock_client
    mock_client.invoke_model.return_value = _make_bedrock_response("gibberish_label")

    from app.reply_classifier import classify_reply
    result = await classify_reply("Some ambiguous reply.", "test@test.com", "smb")
    assert result["classification"] == "unknown"


@patch("app.reply_classifier._get_bedrock")
async def test_classify_bedrock_error_returns_unknown(mock_bedrock_factory):
    mock_client = MagicMock()
    mock_bedrock_factory.return_value = mock_client
    mock_client.invoke_model.side_effect = Exception("Bedrock unavailable")

    from app.reply_classifier import classify_reply
    result = await classify_reply("Some reply text.", "test@test.com", "vmware")
    assert result["classification"] == "unknown"


@patch("app.reply_classifier._get_bedrock")
async def test_classify_bedrock_non_json_returns_unknown(mock_bedrock_factory):
    mock_client = MagicMock()
    mock_bedrock_factory.return_value = mock_client
    # Return malformed response where body text is not JSON
    bad_body = MagicMock()
    bad_body.read.return_value = json.dumps({
        "content": [{"text": "not a json object"}]
    }).encode()
    mock_response = MagicMock()
    mock_response.__getitem__ = lambda self, k: bad_body if k == "body" else None
    mock_client.invoke_model.return_value = mock_response

    from app.reply_classifier import classify_reply
    result = await classify_reply("Some reply.", "test@test.com", "msp")
    assert result["classification"] == "unknown"


# ── suggested_action completeness ────────────────────────────────────────────

def test_all_classifications_have_suggested_action():
    from app.reply_classifier import CLASSIFICATIONS
    for label in ("interested", "not_now", "not_relevant", "referral",
                  "ooo", "bounce", "unsubscribe", "unknown"):
        assert label in CLASSIFICATIONS
        assert CLASSIFICATIONS[label]


# ── GET /webhook/instantly/stats endpoint ─────────────────────────────────────

async def test_stats_endpoint_empty():
    from httpx import AsyncClient, ASGITransport
    from app.main import app, _webhook_events
    _webhook_events.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/webhook/instantly/stats")
    assert r.status_code == 200
    assert r.json()["total_replies"] == 0
    assert r.json()["interested_count"] == 0


async def test_stats_endpoint_counts_classifications():
    from httpx import AsyncClient, ASGITransport
    from app.main import app, _webhook_events
    _webhook_events.clear()
    _webhook_events.extend([
        {"event_type": "reply", "email": "a@a.com", "reply_text": "Yes!", "campaign_id": "vmware",
         "timestamp": "2026-04-07T10:00:00", "processed": False, "classification": "interested", "raw": {}},
        {"event_type": "reply", "email": "b@b.com", "reply_text": "No", "campaign_id": "msp",
         "timestamp": "2026-04-07T11:00:00", "processed": False, "classification": "not_now", "raw": {}},
        {"event_type": "open", "email": "c@c.com", "reply_text": "", "campaign_id": "msp",
         "timestamp": "2026-04-07T12:00:00", "processed": False, "raw": {}},
    ])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/webhook/instantly/stats")
    data = r.json()
    assert data["total_replies"] == 2
    assert data["interested_count"] == 1
    assert data["classifications"]["interested"] == 1
    assert data["classifications"]["not_now"] == 1
