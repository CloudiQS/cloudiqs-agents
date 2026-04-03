"""Unit tests for app.ace_control_plane — ACE daily briefing card."""
import json
from unittest.mock import AsyncMock, MagicMock, patch


# ── _has_data ─────────────────────────────────────────────────────────────────

def test_has_data_empty():
    from app.ace_control_plane import _has_data
    assert _has_data("") is False


def test_has_data_sentinel():
    from app.ace_control_plane import _has_data
    assert _has_data("No data available.") is False
    assert _has_data("Query failed — check MCP connection.") is False


def test_has_data_real():
    from app.ace_control_plane import _has_data
    assert _has_data("O123 | Acme | $10K | Holly Ark | GenAI") is True


# ── _parse_rows ───────────────────────────────────────────────────────────────

def test_parse_rows_returns_list():
    from app.ace_control_plane import _parse_rows
    text = "O123 | Acme Corp | $10K | Holly Ark | GenAI"
    rows = _parse_rows(text, "new_opps")
    assert len(rows) == 1
    assert rows[0][1] == "Acme Corp"


def test_parse_rows_empty_returns_empty():
    from app.ace_control_plane import _parse_rows
    assert _parse_rows("", "new_opps") == []


def test_parse_rows_query_failed_returns_empty():
    from app.ace_control_plane import _parse_rows
    assert _parse_rows("Query failed — check MCP connection.", "new_opps") == []


def test_parse_rows_strips_chatbot_lines():
    from app.ace_control_plane import _parse_rows
    text = "Let me check.\nO123 | Acme | $10K | Holly Ark | GenAI\nDone."
    rows = _parse_rows(text, "new_opps")
    assert len(rows) == 1
    assert rows[0][0] == "O123"


# ── _build_what_happened ──────────────────────────────────────────────────────

def test_what_happened_new_opp():
    from app.ace_control_plane import _build_what_happened
    new_opps = [["O123", "MedicusAI", "$60K", "Daniel Rubio", "GenAI Health"]]
    result = _build_what_happened(new_opps, [], [])
    assert any("MedicusAI" in item for item in result)
    assert any("NEW:" in item for item in result)


def test_what_happened_stage_change():
    from app.ace_control_plane import _build_what_happened
    stage_changes = [["O456", "Catalyst", "Qualified", "Technical Validation"]]
    result = _build_what_happened([], stage_changes, [])
    assert any("STAGE CHANGE" in item and "Catalyst" in item for item in result)


def test_what_happened_aws_closed():
    from app.ace_control_plane import _build_what_happened
    misaligned = [["O789", "Healthjet", "Qualified", "Closed Lost"]]
    result = _build_what_happened([], [], misaligned)
    assert any("AWS CLOSED" in item for item in result)
    assert any("Healthjet" in item for item in result)


def test_what_happened_empty_returns_nothing_new():
    from app.ace_control_plane import _build_what_happened
    result = _build_what_happened([], [], [])
    assert any("Nothing new" in item for item in result)


# ── _build_do_this_today ──────────────────────────────────────────────────────

def test_do_this_today_action_required_first():
    from app.ace_control_plane import _build_do_this_today
    ar = [["O123", "ShadAgro", "Fix customer pain point", "3"]]
    result = _build_do_this_today(ar, [], [], [], [])
    assert len(result) >= 1
    assert "ShadAgro" in result[0]
    assert "Action Required" in result[0]


def test_do_this_today_max_5():
    from app.ace_control_plane import _build_do_this_today
    ar = [["O" + str(i), f"Company{i}", "Fix it", "1"] for i in range(10)]
    result = _build_do_this_today(ar, [], [], [], [])
    assert len(result) <= 5


def test_do_this_today_past_close_date():
    from app.ace_control_plane import _build_do_this_today
    past = [["O999", "Institute for Gambling", "30 Mar 2026"]]
    result = _build_do_this_today([], [], past, [], [])
    assert any("Institute for Gambling" in item and "PASSED" in item for item in result)


def test_do_this_today_empty_is_clean():
    from app.ace_control_plane import _build_do_this_today
    result = _build_do_this_today([], [], [], [], [])
    assert len(result) == 1
    assert "clean" in result[0].lower() or "no immediate" in result[0].lower()


# ── _build_where_money ────────────────────────────────────────────────────────

def test_where_money_shows_stages():
    from app.ace_control_plane import _build_where_money
    pipeline = [
        ["Qualified", "80", "£129K"],
        ["Technical Validation", "25", "£51K"],
        ["Committed", "0", "£0"],
    ]
    result = _build_where_money(pipeline, [])
    assert any("Qualified" in line for line in result)
    assert any("Tech Validation" in line for line in result)


def test_where_money_committed_zero_warning():
    from app.ace_control_plane import _build_where_money
    pipeline = [["Committed", "0", "£0"]]
    result = _build_where_money(pipeline, [])
    assert any("CRITICAL" in line for line in result)


def test_where_money_top_deals_shown():
    from app.ace_control_plane import _build_where_money
    tv_deals = [["O123", "Catalyst", "$10K", "Technical Validation"]]
    result = _build_where_money([], tv_deals)
    assert any("Catalyst" in line for line in result)


# ── _build_funding ────────────────────────────────────────────────────────────

def test_funding_poc_eligible():
    from app.ace_control_plane import _build_funding
    tv_deals = [["O123", "Catalyst", "$10K", "Technical Validation"]]
    result = _build_funding(tv_deals)
    assert any("POC" in line for line in result)
    assert any("Catalyst" in line for line in result)


def test_funding_none_eligible():
    from app.ace_control_plane import _build_funding
    result = _build_funding([])
    assert any("No deals" in line or "0 deals" in line for line in result)


# ── _build_cosell ─────────────────────────────────────────────────────────────

def test_cosell_groups_by_rep():
    from app.ace_control_plane import _build_cosell
    rows = [
        ["O1", "Acme", "$10K", "Holly Ark", "Tech Validation"],
        ["O2", "Beta", "$5K", "Holly Ark", "Qualified"],
        ["O3", "Corp", "$3K", "Daniel Rubio", "Qualified"],
    ]
    result = _build_cosell(rows)
    assert any("Holly Ark" in line for line in result)
    assert any("Daniel Rubio" in line for line in result)


def test_cosell_empty():
    from app.ace_control_plane import _build_cosell
    result = _build_cosell([])
    assert any("No active co-sell" in line for line in result)


def test_cosell_has_rep_guidance():
    from app.ace_control_plane import _build_cosell
    rows = [["O1", "Acme", "$10K", "Holly Ark", "Tech Validation"]]
    result = _build_cosell(rows)
    assert any("Thank these reps" in line for line in result)


# ── build_control_plane_card ──────────────────────────────────────────────────

_SAMPLE_DATA = {
    "date": "03 Apr 2026",
    "leads_today": 5,
    "what_happened": ["NEW: MedicusAI (O123) — $60K from Daniel Rubio\n  → Not yet contacted."],
    "do_this_today": ["MedicusAI (O123) — Action Required from AWS\n   Fix: customer pain. 3 days flagged."],
    "where_money": ["Technical Validation: 25 deals (£51K)", "Committed: 0 deals (£0)",
                    "⚠ CRITICAL: Nothing at Committed.\n   Move TV deals to fix this."],
    "funding": ["25 deals at Tech Validation+ qualify for POC funding."],
    "cosell": ["Holly Ark — Catalyst Commodities ($10K)", "→ Thank these reps."],
    "pipeline_facts": [{"title": "Qualified", "value": "80 deals (£129K)"}],
    "new_opps_count": 1,
    "action_req_count": 1,
    "cosell_count": 1,
}


def test_card_is_message_envelope():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card(_SAMPLE_DATA)
    assert card["type"] == "message"
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


def test_card_is_adaptive_card_v14():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card(_SAMPLE_DATA)
    content = card["attachments"][0]["content"]
    assert content["type"] == "AdaptiveCard"
    assert content["version"] == "1.4"
    assert content.get("msteams", {}).get("width") == "Full"


def test_card_header_is_textblock():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card(_SAMPLE_DATA)
    body = card["attachments"][0]["content"]["body"]
    first = body[0]
    assert first["type"] == "TextBlock"
    assert "ACE CONTROL PLANE" in first["text"]
    assert "03 Apr 2026" in first["text"]


def test_card_no_container_backgrounds():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card(_SAMPLE_DATA)
    body = card["attachments"][0]["content"]["body"]
    for elem in body:
        if elem.get("type") == "Container":
            assert "style" not in elem, f"Container with style found: {elem}"


def test_card_has_all_sections():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card(_SAMPLE_DATA)
    card_json = json.dumps(card)
    for heading in ("WHAT HAPPENED", "YOUR ACTIONS TODAY", "WHERE THE MONEY IS",
                    "FUNDING", "CO-SELL", "PIPELINE SNAPSHOT"):
        assert heading in card_json, f"Missing section: {heading}"


def test_card_has_open_partner_central_button():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card(_SAMPLE_DATA)
    card_json = json.dumps(card)
    assert "Open Partner Central" in card_json
    assert "partnercentral.awspartner.com" in card_json


def test_card_header_color_attention_when_action_required():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card({**_SAMPLE_DATA, "action_req_count": 2})
    first = card["attachments"][0]["content"]["body"][0]
    assert first.get("color") == "attention"


def test_card_header_color_warning_when_new_opps_no_ar():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card({**_SAMPLE_DATA, "action_req_count": 0, "new_opps_count": 3})
    first = card["attachments"][0]["content"]["body"][0]
    assert first.get("color") == "warning"


def test_card_header_color_accent_when_clean():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card({**_SAMPLE_DATA, "action_req_count": 0, "new_opps_count": 0})
    first = card["attachments"][0]["content"]["body"][0]
    assert first.get("color") == "accent"


def test_card_data_is_not_raw_mcp():
    from app.ace_control_plane import build_control_plane_card
    card = build_control_plane_card(_SAMPLE_DATA)
    card_json = json.dumps(card)
    for bad in ("serverToolUse", "tooluse_", "ASSISTANT_RESPONSE",
                "I appreciate", "I'll help", "Let me"):
        assert bad not in card_json, f"Raw MCP data in card: {bad}"


# ── run_control_plane integration ─────────────────────────────────────────────

_MCP_RESP = {
    "text": (
        "O123 | MedicusAI | $60K | Daniel Rubio | GenAI Health\n"
    )
}


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=_MCP_RESP)
async def test_run_control_plane_returns_all_keys(mock_send):
    from app.ace_control_plane import run_control_plane
    result = await run_control_plane(stats={"total_leads": 3})
    for key in ("date", "what_happened", "do_this_today", "where_money",
                "funding", "cosell", "pipeline_facts", "leads_today"):
        assert key in result, f"Missing key: {key}"
    assert result["leads_today"] == 3


@patch("app.mcp_client.send_message", new_callable=AsyncMock, side_effect=Exception("timeout"))
async def test_run_control_plane_handles_mcp_failure(mock_send):
    from app.ace_control_plane import run_control_plane
    result = await run_control_plane()
    # Should return without raising — all sections present even if empty
    assert "what_happened" in result
    assert "do_this_today" in result


# ── post_control_plane_to_teams ───────────────────────────────────────────────

@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_post_control_plane_calls_teams(mock_post):
    from app.ace_control_plane import post_control_plane_to_teams
    result = await post_control_plane_to_teams(_SAMPLE_DATA)
    assert result is True
    mock_post.assert_called_once()


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=False)
async def test_post_control_plane_fallback_on_failure(mock_post):
    from app.ace_control_plane import post_control_plane_to_teams
    # First call fails, second (fallback) returns True on the second patch call
    mock_post.side_effect = [False, True]
    result = await post_control_plane_to_teams(_SAMPLE_DATA)
    assert mock_post.call_count == 2
