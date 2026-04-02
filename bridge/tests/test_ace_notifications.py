"""Unit tests for app.ace_notifications — card building and webhook routing."""
from unittest.mock import AsyncMock, MagicMock, patch


def _body(card: dict) -> list:
    """Extract Adaptive Card body from a card dict."""
    return card["attachments"][0]["content"]["body"]


def _title(card: dict) -> str:
    return _body(card)[0]["text"]


def _content(card: dict) -> str:
    """Body text of the second element (content after the title)."""
    return _body(card)[1]["text"]


# ── _ace_post delegates to teams.post_to_ace ─────────────────────────────────

@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_ace_post_delegates_to_post_to_ace(mock_fn):
    from app.ace_notifications import _ace_post
    card = {"attachments": []}
    result = await _ace_post(card)
    assert result is True
    mock_fn.assert_called_once_with(card)


# ── notify_created ────────────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_created_title_and_content(mock_post):
    from app.ace_notifications import notify_created
    lead = MagicMock(company="Acme Ltd", campaign="msp", contact="Jane Doe", arr=50000)
    await notify_created("O12345", lead)
    card = mock_post.call_args[0][0]
    assert "NEW ACE OPPORTUNITY" in _title(card)
    assert "Acme Ltd" in _title(card)
    assert "O12345" in _title(card)
    assert "MSP" in _content(card)


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_created_formats_arr(mock_post):
    from app.ace_notifications import notify_created
    lead = MagicMock(company="Beta Corp", campaign="vmware", contact="Bob", arr=120000)
    await notify_created("O99999", lead)
    assert "$120,000" in _content(mock_post.call_args[0][0])


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_created_no_arr_shows_tbc(mock_post):
    from app.ace_notifications import notify_created
    lead = MagicMock(company="Gamma", campaign="smb", contact="Sara", arr=None)
    await notify_created("O11111", lead)
    assert "TBC" in _content(mock_post.call_args[0][0])


# ── notify_stage_change ───────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_change_title_and_content(mock_post):
    from app.ace_notifications import notify_stage_change
    await notify_stage_change("O12345", "Acme Ltd", "Committed", "Business Validation")
    card = mock_post.call_args[0][0]
    assert "STAGE UPDATE" in _title(card)
    assert "Business Validation → Committed" in _content(card)


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_change_no_old_stage(mock_post):
    from app.ace_notifications import notify_stage_change
    await notify_stage_change("O12345", "Acme Ltd", "Committed")
    text = _content(mock_post.call_args[0][0])
    assert "Committed" in text
    assert "→ Committed" not in text


# ── notify_hygiene ────────────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_hygiene_posts_all_three_sections(mock_post):
    from app.ace_notifications import notify_hygiene
    report = {
        "action_required": "Acme: submit win wire",
        "stale_launched": "Beta: 45 days no update",
        "funding_eligible": "Gamma eligible for MAP",
    }
    await notify_hygiene(report)
    card = mock_post.call_args[0][0]
    body_texts = [b.get("text", "") for b in _body(card)]
    assert any("ACE HYGIENE REPORT" in t for t in body_texts)
    assert any("ACTION REQUIRED" in t for t in body_texts)
    assert any("FUNDING ELIGIBLE" in t for t in body_texts)
    assert any("Acme: submit win wire" in t for t in body_texts)


# ── notify_funding_eligible ───────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_funding_eligible_title_and_content(mock_post):
    from app.ace_notifications import notify_funding_eligible
    await notify_funding_eligible("Acme", "O111", "MAP", "$25,000", "Submit application")
    card = mock_post.call_args[0][0]
    assert "FUNDING ELIGIBLE" in _title(card)
    assert "MAP" in _content(card)
    assert "$25,000" in _content(card)


# ── notify_stage_mismatch ─────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_mismatch_title_and_content(mock_post):
    from app.ace_notifications import notify_stage_mismatch
    await notify_stage_mismatch("Acme", "O111", "Launched", "Closed Lost", "Update in ACE")
    card = mock_post.call_args[0][0]
    assert "MISMATCH" in _title(card)
    text = _content(card)
    assert "Launched" in text
    assert "Closed Lost" in text


# ── notify_action_required ────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_action_required_title_and_content(mock_post):
    from app.ace_notifications import notify_action_required
    await notify_action_required("Acme", "O111", "Submit win wire", "2026-04-10")
    card = mock_post.call_args[0][0]
    assert "ACTION REQUIRED" in _title(card)
    assert "Submit win wire" in _content(card)
    assert "2026-04-10" in _content(card)


# ── notify_inbound_ao ─────────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_inbound_ao_title_and_content(mock_post):
    from app.ace_notifications import notify_inbound_ao
    await notify_inbound_ao("Acme", "John AWS", "Accept in Partner Central")
    card = mock_post.call_args[0][0]
    assert "INBOUND FROM AWS" in _title(card)
    assert "John AWS" in _content(card)


# ── notify_close_date_warning ─────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_close_date_warning_title(mock_post):
    from app.ace_notifications import notify_close_date_warning
    await notify_close_date_warning("Acme", "O111", "2026-04-05", "Committed", 5)
    assert "CLOSE DATE RISK" in _title(mock_post.call_args[0][0])


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_close_date_warning_amber_title(mock_post):
    from app.ace_notifications import notify_close_date_warning
    await notify_close_date_warning("Acme", "O111", "2026-04-12", "Business Validation", 11)
    assert "CLOSE DATE RISK" in _title(mock_post.call_args[0][0])


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_close_date_body_includes_days(mock_post):
    from app.ace_notifications import notify_close_date_warning
    await notify_close_date_warning("Acme", "O111", "2026-04-12", "Committed", 11)
    text = _content(mock_post.call_args[0][0])
    assert "Days left: 11" in text
    assert "Committed" in text


# ── notify_briefing_alerts ────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_briefing_alerts_posts_both_sections(mock_post):
    from app.ace_notifications import notify_briefing_alerts
    data = {
        "date": "07 Apr 2026",
        "action_required": "Acme: submit win wire",
        "aws_stage": "8 Launched confirmed, 1 Closed Lost",
    }
    result = await notify_briefing_alerts(data)
    assert result is True
    assert mock_post.call_count == 2


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_briefing_alerts_skips_failed_sections(mock_post):
    from app.ace_notifications import notify_briefing_alerts
    data = {
        "date": "07 Apr 2026",
        "action_required": "Query failed — check MCP connection.",
        "aws_stage": "No data returned.",
    }
    result = await notify_briefing_alerts(data)
    assert result is False
    mock_post.assert_not_called()


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_briefing_alerts_truncates_long_text(mock_post):
    from app.ace_notifications import notify_briefing_alerts
    data = {
        "date": "07 Apr 2026",
        "action_required": "A" * 1000,
        "aws_stage": "",
    }
    await notify_briefing_alerts(data)
    card = mock_post.call_args[0][0]
    assert len(_content(card)) <= 800
