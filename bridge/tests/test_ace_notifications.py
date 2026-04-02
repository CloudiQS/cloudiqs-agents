"""Unit tests for app.ace_notifications — routing and card content."""
from unittest.mock import AsyncMock, MagicMock, patch


# ── All functions route to teams.post_to_ace ─────────────────────────────────

@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_created_routes_to_ace(mock_fn):
    from app.ace_notifications import notify_created
    lead = MagicMock(company="Acme Ltd", campaign="msp", contact="Jane", arr=50000)
    result = await notify_created("O12345", lead)
    assert result is True
    mock_fn.assert_called_once()
    title = mock_fn.call_args[0][0]
    assert "NEW ACE OPPORTUNITY" in title
    assert "Acme Ltd" in title
    assert "O12345" in title


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_created_formats_arr(mock_fn):
    from app.ace_notifications import notify_created
    lead = MagicMock(company="Beta", campaign="vmware", contact="Bob", arr=120000)
    await notify_created("O99999", lead)
    body = mock_fn.call_args[0][1]
    assert "$120,000" in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_created_no_arr_shows_tbc(mock_fn):
    from app.ace_notifications import notify_created
    lead = MagicMock(company="Gamma", campaign="smb", contact="Sara", arr=None)
    await notify_created("O11111", lead)
    assert "TBC" in mock_fn.call_args[0][1]


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_change_content(mock_fn):
    from app.ace_notifications import notify_stage_change
    await notify_stage_change("O12345", "Acme Ltd", "Committed", "Business Validation")
    title = mock_fn.call_args[0][0]
    body  = mock_fn.call_args[0][1]
    assert "STAGE UPDATE" in title
    assert "Business Validation → Committed" in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_change_no_old_stage(mock_fn):
    from app.ace_notifications import notify_stage_change
    await notify_stage_change("O12345", "Acme Ltd", "Committed")
    body = mock_fn.call_args[0][1]
    assert "Committed" in body
    assert "→ Committed" not in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_hygiene_all_three_sections(mock_fn):
    from app.ace_notifications import notify_hygiene
    report = {
        "action_required": "Acme: submit win wire",
        "stale_launched": "Beta: 45 days",
        "funding_eligible": "Gamma eligible MAP",
    }
    await notify_hygiene(report)
    title = mock_fn.call_args[0][0]
    body  = mock_fn.call_args[0][1]
    assert "ACE HYGIENE REPORT" in title
    assert "ACTION REQUIRED" in body
    assert "FUNDING ELIGIBLE" in body
    assert "Acme: submit win wire" in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_funding_eligible_content(mock_fn):
    from app.ace_notifications import notify_funding_eligible
    await notify_funding_eligible("Acme", "O111", "MAP", "$25,000", "Submit application")
    title = mock_fn.call_args[0][0]
    body  = mock_fn.call_args[0][1]
    assert "FUNDING ELIGIBLE" in title
    assert "MAP" in body
    assert "$25,000" in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_stage_mismatch_content(mock_fn):
    from app.ace_notifications import notify_stage_mismatch
    await notify_stage_mismatch("Acme", "O111", "Launched", "Closed Lost", "Update in ACE")
    title = mock_fn.call_args[0][0]
    body  = mock_fn.call_args[0][1]
    assert "MISMATCH" in title
    assert "Launched" in body
    assert "Closed Lost" in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_action_required_content(mock_fn):
    from app.ace_notifications import notify_action_required
    await notify_action_required("Acme", "O111", "Submit win wire", "2026-04-10")
    title = mock_fn.call_args[0][0]
    body  = mock_fn.call_args[0][1]
    assert "ACTION REQUIRED" in title
    assert "Submit win wire" in body
    assert "2026-04-10" in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_inbound_ao_content(mock_fn):
    from app.ace_notifications import notify_inbound_ao
    await notify_inbound_ao("Acme", "John AWS", "Accept in Partner Central")
    title = mock_fn.call_args[0][0]
    body  = mock_fn.call_args[0][1]
    assert "INBOUND FROM AWS" in title
    assert "John AWS" in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_notify_close_date_warning_content(mock_fn):
    from app.ace_notifications import notify_close_date_warning
    await notify_close_date_warning("Acme", "O111", "2026-04-12", "Committed", 11)
    title = mock_fn.call_args[0][0]
    body  = mock_fn.call_args[0][1]
    assert "CLOSE DATE RISK" in title
    assert "Days left: 11" in body
    assert "Committed" in body


# ── notify_briefing_alerts ────────────────────────────────────────────────────

@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_briefing_alerts_posts_both_sections(mock_fn):
    from app.ace_notifications import notify_briefing_alerts
    data = {
        "date": "07 Apr 2026",
        "action_required": "Acme: submit win wire",
        "aws_stage": "8 Launched confirmed, 1 Closed Lost",
    }
    result = await notify_briefing_alerts(data)
    assert result is True
    assert mock_fn.call_count == 2


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_briefing_alerts_skips_failed_sections(mock_fn):
    from app.ace_notifications import notify_briefing_alerts
    data = {
        "date": "07 Apr 2026",
        "action_required": "Query failed — check MCP connection.",
        "aws_stage": "No data returned.",
    }
    result = await notify_briefing_alerts(data)
    assert result is False
    mock_fn.assert_not_called()


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_briefing_alerts_truncates_long_text(mock_fn):
    from app.ace_notifications import notify_briefing_alerts
    data = {
        "date": "07 Apr 2026",
        "action_required": "A" * 1000,
        "aws_stage": "",
    }
    await notify_briefing_alerts(data)
    body = mock_fn.call_args[0][1]
    assert len(body) <= 800
