"""Unit tests for app.ceo_briefing — colour logic, count parsing, Teams card."""
from unittest.mock import AsyncMock, patch

from app.ceo_briefing import _colour, _theme_colour, _try_parse_counts


# ── _colour ───────────────────────────────────────────────────────────────────

def test_colour_green_at_threshold():
    assert _colour(0.80) == "GREEN"


def test_colour_green_above_threshold():
    assert _colour(1.0) == "GREEN"


def test_colour_amber_between_thresholds():
    assert _colour(0.70) == "AMBER"


def test_colour_red_below_amber():
    assert _colour(0.50) == "RED"


def test_colour_none_returns_unknown():
    assert _colour(None) == "UNKNOWN"


# ── _theme_colour ─────────────────────────────────────────────────────────────

def test_theme_green():
    assert _theme_colour("GREEN") == "00B050"


def test_theme_amber():
    assert _theme_colour("AMBER") == "FFC000"


def test_theme_red():
    assert _theme_colour("RED") == "C00000"


def test_theme_unknown_fallback():
    assert _theme_colour("UNKNOWN") == "888888"


# ── _try_parse_counts ─────────────────────────────────────────────────────────

def test_parse_counts_extracts_total_launched():
    text = "You have 12 total launched opportunities."
    counts = _try_parse_counts(text)
    assert counts["total"] == 12


def test_parse_counts_returns_none_when_no_match():
    text = "No data returned."
    counts = _try_parse_counts(text)
    assert counts["total"] is None
    assert counts["aws_launched"] is None
    assert counts["closed_lost"] is None
    assert counts["empty"] is None


def test_parse_counts_extracts_closed_lost():
    text = "3 opportunities have AWS stage Closed Lost."
    counts = _try_parse_counts(text)
    assert counts["closed_lost"] == 3


# ── run_aws_stage_alignment ───────────────────────────────────────────────────

@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value={
    "text": "10 total launched. 8 AWS Launched, 1 Closed Lost, 1 empty.",
})
async def test_run_alignment_parses_response(mock_mcp):
    from app.ceo_briefing import run_aws_stage_alignment
    result = await run_aws_stage_alignment()
    assert result["colour"] in ("GREEN", "AMBER", "RED", "UNKNOWN")
    assert "text" in result
    mock_mcp.assert_called_once()


@patch("app.mcp_client.send_message", new_callable=AsyncMock, side_effect=Exception("timeout"))
async def test_run_alignment_handles_mcp_failure(mock_mcp):
    from app.ceo_briefing import run_aws_stage_alignment
    result = await run_aws_stage_alignment()
    assert result["colour"] == "UNKNOWN"
    assert result["alignment_rate"] is None
    assert "MCP query failed" in result["text"]


# ── post_briefing_to_teams ────────────────────────────────────────────────────

@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_green(mock_post):
    from app.ceo_briefing import post_briefing_to_teams
    alignment = {
        "colour": "GREEN",
        "text": "8 of 10 confirmed.",
        "total": 10,
        "aws_launched": 8,
        "alignment_rate": 0.8,
        "closed_lost": 1,
        "empty": 1,
    }
    result = await post_briefing_to_teams(alignment)
    assert result is True
    card = mock_post.call_args[0][0]
    assert card["themeColor"] == "00B050"
    assert "AWS STAGE ALIGNMENT" in card["title"]


@patch("app.teams._post", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_unknown_no_counts(mock_post):
    from app.ceo_briefing import post_briefing_to_teams
    alignment = {
        "colour": "UNKNOWN",
        "text": "No data.",
        "total": None,
        "aws_launched": None,
        "alignment_rate": None,
        "closed_lost": None,
        "empty": None,
    }
    result = await post_briefing_to_teams(alignment)
    assert result is True
    card = mock_post.call_args[0][0]
    assert card["themeColor"] == "888888"
