"""Unit tests for app.ace_notifications — card building, colour coding, webhook routing."""
from unittest.mock import AsyncMock, MagicMock, patch


# ── _ace_post delegates to teams.post_to_ace ─────────────────────────────────

@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_ace_post_delegates_to_post_to_ace(mock_fn):
    from app.ace_notifications import _ace_post
    card = {"@type": "MessageCard", "title": "test"}
    result = await _ace_post(card)
    assert result is True
    mock_fn.assert_called_once_with(card)


# ── notify_created ────────────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_created_green_card(mock_post):
    from app.ace_notifications import notify_created, _GREEN
    lead = MagicMock(company="Acme Ltd", campaign="msp", contact="Jane Doe", arr=50000)
    await notify_created("O12345", lead)
    card = mock_post.call_args[0][0]
    assert card["themeColor"] == _GREEN
    assert "Acme Ltd" in card["title"]
    assert "O12345" in card["title"]
    assert "NEW ACE OPPORTUNITY" in card["title"]
    assert "MSP" in card["sections"][0]["text"]


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_created_formats_arr(mock_post):
    from app.ace_notifications import notify_created
    lead = MagicMock(company="Beta Corp", campaign="vmware", contact="Bob", arr=120000)
    await notify_created("O99999", lead)
    card = mock_post.call_args[0][0]
    assert "$120,000" in card["sections"][0]["text"]


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_created_no_arr_shows_tbc(mock_post):
    from app.ace_notifications import notify_created
    lead = MagicMock(company="Gamma", campaign="smb", contact="Sara", arr=None)
    await notify_created("O11111", lead)
    assert "TBC" in mock_post.call_args[0][0]["sections"][0]["text"]


# ── notify_stage_change ───────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_change_amber_card(mock_post):
    from app.ace_notifications import notify_stage_change, _AMBER
    await notify_stage_change("O12345", "Acme Ltd", "Committed", "Business Validation")
    card = mock_post.call_args[0][0]
    assert card["themeColor"] == _AMBER
    assert "STAGE UPDATE" in card["title"]
    assert "Business Validation → Committed" in card["sections"][0]["text"]


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_change_no_old_stage(mock_post):
    from app.ace_notifications import notify_stage_change
    await notify_stage_change("O12345", "Acme Ltd", "Committed")
    text = mock_post.call_args[0][0]["sections"][0]["text"]
    assert "Committed" in text
    # No arrow when old_stage not provided
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
    assert "ACE HYGIENE REPORT" in card["title"]
    section_titles = [s.get("activityTitle", "") for s in card["sections"]]
    assert any("ACTION REQUIRED" in t for t in section_titles)
    assert any("FUNDING ELIGIBLE" in t for t in section_titles)


# ── notify_funding_eligible ───────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_funding_eligible_green_card(mock_post):
    from app.ace_notifications import notify_funding_eligible, _GREEN
    await notify_funding_eligible("Acme", "O111", "MAP", "$25,000", "Submit application")
    card = mock_post.call_args[0][0]
    assert card["themeColor"] == _GREEN
    assert "FUNDING ELIGIBLE" in card["title"]
    assert "MAP" in card["sections"][0]["text"]
    assert "$25,000" in card["sections"][0]["text"]


# ── notify_stage_mismatch ─────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_mismatch_red_card(mock_post):
    from app.ace_notifications import notify_stage_mismatch, _RED
    await notify_stage_mismatch("Acme", "O111", "Launched", "Closed Lost", "Update in ACE")
    card = mock_post.call_args[0][0]
    assert card["themeColor"] == _RED
    assert "MISMATCH" in card["title"]
    text = card["sections"][0]["text"]
    assert "Launched" in text
    assert "Closed Lost" in text


# ── notify_action_required ────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_action_required_red_card(mock_post):
    from app.ace_notifications import notify_action_required, _RED
    await notify_action_required("Acme", "O111", "Submit win wire", "2026-04-10")
    card = mock_post.call_args[0][0]
    assert card["themeColor"] == _RED
    assert "ACTION REQUIRED" in card["title"]
    assert "Submit win wire" in card["sections"][0]["text"]
    assert "2026-04-10" in card["sections"][0]["text"]


# ── notify_inbound_ao ─────────────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_inbound_ao_amber_card(mock_post):
    from app.ace_notifications import notify_inbound_ao, _AMBER
    await notify_inbound_ao("Acme", "John AWS", "Accept in Partner Central")
    card = mock_post.call_args[0][0]
    assert card["themeColor"] == _AMBER
    assert "INBOUND FROM AWS" in card["title"]
    assert "John AWS" in card["sections"][0]["text"]


# ── notify_close_date_warning ─────────────────────────────────────────────────

@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_close_date_warning_red_under_7_days(mock_post):
    from app.ace_notifications import notify_close_date_warning, _RED
    await notify_close_date_warning("Acme", "O111", "2026-04-05", "Committed", 5)
    assert mock_post.call_args[0][0]["themeColor"] == _RED


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_close_date_warning_amber_7_to_14_days(mock_post):
    from app.ace_notifications import notify_close_date_warning, _AMBER
    await notify_close_date_warning("Acme", "O111", "2026-04-12", "Business Validation", 11)
    assert mock_post.call_args[0][0]["themeColor"] == _AMBER


@patch("app.ace_notifications._ace_post", new_callable=AsyncMock, return_value=True)
async def test_notify_close_date_body_includes_days(mock_post):
    from app.ace_notifications import notify_close_date_warning
    await notify_close_date_warning("Acme", "O111", "2026-04-12", "Committed", 11)
    text = mock_post.call_args[0][0]["sections"][0]["text"]
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
    assert len(card["sections"][0]["text"]) <= 800
