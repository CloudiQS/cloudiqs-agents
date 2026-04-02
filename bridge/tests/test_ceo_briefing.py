"""Unit tests for app.ceo_briefing — parsing, narrative stripping, card formatting."""
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
    assert "Part one." in result and "Part two." in result


def test_extract_falls_back_to_raw_on_non_json():
    from app.ceo_briefing import _extract_assistant_text
    assert _extract_assistant_text({"text": "plain text"}) == "plain text"


def test_extract_returns_empty_on_none():
    from app.ceo_briefing import _extract_assistant_text
    assert _extract_assistant_text(None) == ""


# ── _strip_narrative ──────────────────────────────────────────────────────────

def test_strip_removes_ill_help_line():
    from app.ceo_briefing import _strip_narrative
    text = "I'll help you with that.\nAcme Corp: Action Required."
    assert "I'll help" not in _strip_narrative(text)
    assert "Acme Corp" in _strip_narrative(text)


def test_strip_removes_let_me_line():
    from app.ceo_briefing import _strip_narrative
    text = "Let me fetch your pipeline data.\n5 deals at Committed stage."
    result = _strip_narrative(text)
    assert "Let me" not in result
    assert "5 deals" in result


def test_strip_keeps_data_lines():
    from app.ceo_briefing import _strip_narrative
    text = "Prospect: 8\nQualified: 4\nLaunched: 2"
    assert _strip_narrative(text) == text


def test_strip_collapses_consecutive_blanks():
    from app.ceo_briefing import _strip_narrative
    text = "Line one.\n\n\n\nLine two."
    result = _strip_narrative(text)
    assert "\n\n\n" not in result
    assert "Line one." in result
    assert "Line two." in result


def test_strip_removes_multiple_narrative_prefixes():
    from app.ceo_briefing import _strip_narrative
    text = (
        "I'll analyze your data now.\n"
        "Great!\n"
        "Fetching results...\n"
        "Acme Ltd closes 10 Apr."
    )
    result = _strip_narrative(text)
    assert "Acme Ltd" in result
    assert "I'll analyze" not in result
    assert "Great!" not in result
    assert "Fetching" not in result


# ── _clean ────────────────────────────────────────────────────────────────────

def test_clean_truncates_to_budget():
    from app.ceo_briefing import _clean
    long_text = "Acme Corp deal. " * 100
    result = _clean(long_text, 200)
    assert len(result) < len(long_text)
    assert "..." in result


def test_clean_returns_no_data_when_empty():
    from app.ceo_briefing import _clean
    assert _clean("", 200) == "No data."


def test_clean_strips_narrative_before_truncating():
    from app.ceo_briefing import _clean
    text = "Let me fetch that.\nAcme Corp: 5 deals."
    result = _clean(text, 500)
    assert "Let me" not in result
    assert "Acme Corp" in result


# ── _theme_for ────────────────────────────────────────────────────────────────

def test_theme_red_when_action_required():
    from app.ceo_briefing import _theme_for
    assert _theme_for({"action_required": "Acme: submit win wire"}) == "C00000"


def test_theme_amber_when_closing_soon_no_action():
    from app.ceo_briefing import _theme_for
    assert _theme_for({
        "action_required": "No data.",
        "closing_soon": "Acme closes 10 Apr",
    }) == "FFC000"


def test_theme_blue_when_nothing_urgent():
    from app.ceo_briefing import _theme_for
    assert _theme_for({}) == "1F3D7A"


# ── _mcp helper ───────────────────────────────────────────────────────────────

_MCP_RESPONSE = {
    "text": json.dumps({
        "content": [
            {"type": "ASSISTANT_RESPONSE", "content": {
                "text": "Let me fetch that.\n5 opportunities at Committed stage."
            }},
        ]
    })
}


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=_MCP_RESPONSE)
async def test_mcp_strips_narrative_from_response(mock_send):
    from app.ceo_briefing import _mcp
    result = await _mcp("any query")
    assert "Let me" not in result
    assert "5 opportunities" in result


@patch("app.mcp_client.send_message", new_callable=AsyncMock, side_effect=Exception("timeout"))
async def test_mcp_returns_fallback_on_exception(mock_send):
    from app.ceo_briefing import _mcp
    result = await _mcp("any query")
    assert result == "Query failed — check MCP connection."


# ── run_briefing ──────────────────────────────────────────────────────────────

@patch("app.ceo_briefing._mcp", new_callable=AsyncMock, return_value="5 deals at Committed.")
async def test_run_briefing_returns_all_keys(mock_mcp):
    from app.ceo_briefing import run_briefing
    result = await run_briefing(stats={"total_leads": 3})
    for key in ("date", "pipeline", "action_required", "closing_soon",
                "aws_stage", "cosell", "funding", "aws_actions", "rep_activity",
                "weekly", "leads_today"):
        assert key in result
    assert result["leads_today"] == 3


@patch("app.ceo_briefing._mcp", new_callable=AsyncMock, return_value="ok")
async def test_run_briefing_monday_has_weekly(mock_mcp):
    from app.ceo_briefing import run_briefing
    with patch("app.ceo_briefing.datetime") as mock_dt:
        mock_dt.now.return_value.weekday.return_value = 0
        mock_dt.now.return_value.strftime.return_value = "06 Apr 2026"
        result = await run_briefing()
    assert result["is_monday"] is True
    assert "close_date_cleanup" in result["weekly"]


@patch("app.ceo_briefing._mcp", new_callable=AsyncMock, return_value="ok")
async def test_run_briefing_non_monday_empty_weekly(mock_mcp):
    from app.ceo_briefing import run_briefing
    with patch("app.ceo_briefing.datetime") as mock_dt:
        mock_dt.now.return_value.weekday.return_value = 2
        mock_dt.now.return_value.strftime.return_value = "08 Apr 2026"
        result = await run_briefing()
    assert result["is_monday"] is False
    assert result["weekly"] == {}


# ── post_briefing_to_teams ────────────────────────────────────────────────────

_DATA = {
    "date": "02 Apr 2026",
    "is_monday": False,
    "pipeline": "Prospect: 8 | Qualified: 4",
    "action_required": "Acme: submit win wire",
    "closing_soon": "Acme closes 10 Apr",
    "aws_stage": "8 confirmed, 1 Closed Lost",
    "cosell": "Bob Smith on Acme",
    "funding": "Acme eligible MAP $25k",
    "weekly": {},
    "leads_today": 12,
}


@patch("app.teams.post_to_ceo", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_calls_post_to_ceo(mock_post):
    from app.ceo_briefing import post_briefing_to_teams
    await post_briefing_to_teams(_DATA)
    mock_post.assert_called_once()
    kwargs = mock_post.call_args[1]
    title = kwargs.get("title") or mock_post.call_args[0][0]
    assert "CEO BRIEFING" in title
    assert "02 Apr 2026" in title


@patch("app.teams.post_to_ceo", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_includes_pipeline_in_body(mock_post):
    from app.ceo_briefing import post_briefing_to_teams
    await post_briefing_to_teams(_DATA)
    kwargs = mock_post.call_args[1]
    body_text = kwargs.get("body_text") or mock_post.call_args[0][1]
    assert "PIPELINE" in body_text
    assert "ENGINE" not in body_text  # ENGINE is in facts, not body


@patch("app.teams.post_to_ceo", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_engine_in_facts(mock_post):
    from app.ceo_briefing import post_briefing_to_teams
    await post_briefing_to_teams(_DATA)
    # facts is passed as keyword arg
    kwargs = mock_post.call_args[1]
    facts = kwargs.get("facts") or (mock_post.call_args[0][2] if len(mock_post.call_args[0]) > 2 else [])
    assert any(f["title"] == "Leads today" and f["value"] == "12" for f in facts)


@patch("app.teams.post_to_ceo", new_callable=AsyncMock, return_value=True)
async def test_post_briefing_monday_weekly_in_body(mock_post):
    from app.ceo_briefing import post_briefing_to_teams
    data = dict(_DATA, is_monday=True, weekly={
        "close_date_cleanup": "3 overdue",
        "closed_lost_analysis": "Budget",
        "pipeline_velocity": "45 days",
    })
    await post_briefing_to_teams(data)
    kwargs = mock_post.call_args[1]
    body_text = kwargs.get("body_text") or mock_post.call_args[0][1]
    assert "WEEKLY" in body_text
