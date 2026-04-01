"""Unit tests for app.ceo_briefing — response parsing, briefing runner, Teams card."""
import json
from unittest.mock import AsyncMock, patch


# ── _extract_assistant_text ───────────────────────────────────────────────────

def test_extract_parses_nested_json():
    from app.ceo_briefing import _extract_assistant_text
    payload = {
        "text": json.dumps({
            "content": [
                {"type": "ASSISTANT_RESPONSE", "content": {"text": "Pipeline looks healthy."}},
                {"type": "serverToolResult", "content": {"data": "ignored"}},
            ]
        })
    }
    assert _extract_assistant_text(payload) == "Pipeline looks healthy."


def test_extract_joins_multiple_assistant_blocks():
    from app.ceo_briefing import _extract_assistant_text
    payload = {
        "text": json.dumps({
            "content": [
                {"type": "ASSISTANT_RESPONSE", "content": {"text": "Part one."}},
                {"type": "ASSISTANT_RESPONSE", "content": {"text": "Part two."}},
            ]
        })
    }
    result = _extract_assistant_text(payload)
    assert "Part one." in result
    assert "Part two." in result


def test_extract_falls_back_to_raw_on_non_json():
    from app.ceo_briefing import _extract_assistant_text
    assert _extract_assistant_text({"text": "plain text"}) == "plain text"


def test_extract_returns_empty_on_none():
    from app.ceo_briefing import _extract_assistant_text
    assert _extract_assistant_text(None) == ""


def test_extract_returns_empty_on_missing_text_key():
    from app.ceo_briefing import _extract_assistant_text
    assert _extract_assistant_text({}) == ""


# ── _trunc ────────────────────────────────────────────────────────────────────

def test_trunc_short_string_unchanged():
    from app.ceo_briefing import _trunc
    assert _trunc("hello", 10) == "hello"


def test_trunc_long_string_cut_with_ellipsis():
    from app.ceo_briefing import _trunc
    result = _trunc("a" * 900)
    assert len(result) == 800
    assert result.endswith("…")


# ── _mcp helper ───────────────────────────────────────────────────────────────

_MCP_RESPONSE = {
    "text": json.dumps({
        "content": [
            {"type": "ASSISTANT_RESPONSE", "content": {"text": "5 opportunities at Committed stage."}},
        ]
    })
}


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=_MCP_RESPONSE)
async def test_mcp_returns_extracted_text(mock_send):
    from app.ceo_briefing import _mcp
    result = await _mcp("any query")
    assert result == "5 opportunities at Committed stage."
    mock_send.assert_called_once()


@patch("app.mcp_client.send_message", new_callable=AsyncMock, side_effect=Exception("timeout"))
async def test_mcp_returns_fallback_on_exception(mock_send):
    from app.ceo_briefing import _mcp
    result = await _mcp("any query")
    assert result == "Query failed — check MCP connection."


# ── run_briefing ──────────────────────────────────────────────────────────────

@patch("app.ceo_briefing._mcp", new_callable=AsyncMock, return_value="mocked section text")
async def test_run_briefing_returns_all_keys(mock_mcp):
    from app.ceo_briefing import run_briefing
    result = await run_briefing(stats={"total_leads": 3})
    assert "date" in result
    assert "pipeline" in result
    assert "action_required" in result
    assert "closing_soon" in result
    assert "aws_stage" in result
    assert "cosell" in result
    assert "funding" in result
    assert "weekly" in result
    assert result["leads_today"] == 3


@patch("app.ceo_briefing._mcp", new_callable=AsyncMock, return_value="ok")
async def test_run_briefing_handles_none_stats(mock_mcp):
    from app.ceo_briefing import run_briefing
    result = await run_briefing(stats=None)
    assert result["leads_today"] == 0


@patch("app.ceo_briefing._mcp", new_callable=AsyncMock, return_value="ok")
async def test_run_briefing_monday_includes_weekly(mock_mcp):
    from app.ceo_briefing import run_briefing
    import app.ceo_briefing as module
    # Force is_monday by patching datetime
    with patch("app.ceo_briefing.datetime") as mock_dt:
        mock_dt.now.return_value.weekday.return_value = 0  # Monday
        mock_dt.now.return_value.strftime.return_value = "07 Apr 2026"
        result = await run_briefing()
    assert result["is_monday"] is True
    assert "close_date_cleanup" in result["weekly"]
    assert "closed_lost_analysis" in result["weekly"]
    assert "pipeline_velocity" in result["weekly"]


@patch("app.ceo_briefing._mcp", new_callable=AsyncMock, return_value="ok")
async def test_run_briefing_non_monday_empty_weekly(mock_mcp):
    from app.ceo_briefing import run_briefing
    with patch("app.ceo_briefing.datetime") as mock_dt:
        mock_dt.now.return_value.weekday.return_value = 2  # Wednesday
        mock_dt.now.return_value.strftime.return_value = "08 Apr 2026"
        result = await run_briefing()
    assert result["is_monday"] is False
    assert result["weekly"] == {}


# ── post_briefing_to_teams ────────────────────────────────────────────────────

_BRIEFING_DATA = {
    "date": "07 Apr 2026",
    "is_monday": False,
    "pipeline": "5 Committed, 3 Launched",
    "action_required": "Acme Corp: submit win wire",
    "closing_soon": "Acme Corp closes 10 Apr",
    "aws_stage": "8 Launched confirmed, 1 Closed Lost, 0 empty",
    "cosell": "Bob Smith engaged on Acme deal",
    "funding": "Acme eligible for MAP",
    "weekly": {},
    "leads_today": 12,
}


@patch("app.teams.post_to_ceo", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_builds_correct_card(mock_post_to_ceo):
    from app.ceo_briefing import post_briefing_to_teams
    result = await post_briefing_to_teams(_BRIEFING_DATA)
    assert result is True
    card = mock_post_to_ceo.call_args[0][0]
    assert card["themeColor"] == "1F3D7A"
    assert "CLOUDIQS CEO BRIEFING" in card["title"]
    assert "07 Apr 2026" in card["title"]


@patch("app.teams.post_to_ceo", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_routes_to_ceo_channel(mock_post_to_ceo):
    from app.ceo_briefing import post_briefing_to_teams
    await post_briefing_to_teams(_BRIEFING_DATA)
    mock_post_to_ceo.assert_called_once()


@patch("app.teams.post_to_ceo", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_monday_includes_weekly_section(mock_post_to_ceo):
    from app.ceo_briefing import post_briefing_to_teams
    data = dict(_BRIEFING_DATA, is_monday=True, weekly={
        "close_date_cleanup": "3 deals overdue",
        "closed_lost_analysis": "Top reason: budget",
        "pipeline_velocity": "avg 45 days",
    })
    await post_briefing_to_teams(data)
    card = mock_post_to_ceo.call_args[0][0]
    section_titles = [s.get("activityTitle", "") for s in card["sections"]]
    assert any("WEEKLY FOCUS" in t for t in section_titles)
