"""Unit tests for app.ace_hygiene — health score, action plan, Teams card."""
import json
from unittest.mock import AsyncMock, patch


# ── _count_items ──────────────────────────────────────────────────────────────

def test_count_items_empty():
    from app.ace_hygiene import _count_items
    assert _count_items("") == 0


def test_count_items_none_found():
    from app.ace_hygiene import _count_items
    assert _count_items("None found.") == 0


def test_count_items_query_failed():
    from app.ace_hygiene import _count_items
    assert _count_items("Query failed — check MCP connection.") == 0


def test_count_items_no_data_available():
    from app.ace_hygiene import _count_items
    assert _count_items("No data available.") == 0


def test_count_items_with_pipe_rows():
    from app.ace_hygiene import _count_items
    text = "O12345 | Acme Corp | Missing date | 3d left\nO67890 | Beta Ltd | No ARR | 7d left"
    assert _count_items(text) == 2


def test_count_items_single_row():
    from app.ace_hygiene import _count_items
    assert _count_items("O123 | Acme | MAP") == 1


# ── _has_content ──────────────────────────────────────────────────────────────

def test_has_content_with_data():
    from app.ace_hygiene import _has_content
    assert _has_content("O123 | Acme | action | 5") is True


def test_has_content_no_data_available():
    from app.ace_hygiene import _has_content
    assert _has_content("No data available.") is False


def test_has_content_query_failed():
    from app.ace_hygiene import _has_content
    assert _has_content("Query failed — check MCP connection.") is False


def test_has_content_empty():
    from app.ace_hygiene import _has_content
    assert _has_content("") is False


# ── _parse_section ────────────────────────────────────────────────────────────

def test_parse_section_action_required_formats_row():
    from app.ace_hygiene import _parse_section
    raw = "O123 | Acme Ltd | Missing close date | 5"
    result = _parse_section(raw, "action_required")
    assert "O123" in result
    assert "Acme Ltd" in result
    assert "5d left" in result


def test_parse_section_strips_chatbot_lines():
    from app.ace_hygiene import _parse_section
    raw = "Let me check.\nO456 | Beta Corp | No ARR | 7\nI found 1 opportunity."
    result = _parse_section(raw, "action_required")
    assert "O456" in result
    assert "Let me" not in result
    assert "I found" not in result


def test_parse_section_returns_no_data_on_pure_narrative():
    from app.ace_hygiene import _parse_section
    raw = "Let me analyze.\nI'll check the pipeline.\nHere's what I found."
    result = _parse_section(raw, "action_required")
    assert result == "No data available."


def test_parse_section_stale_launched_format():
    from app.ace_hygiene import _parse_section
    raw = "O789 | UK Tote Group | £240k | 45"
    result = _parse_section(raw, "stale_launched")
    assert "45 days stale" in result
    assert "UK Tote Group" in result


def test_parse_section_funding_eligible_three_fields():
    from app.ace_hygiene import _parse_section
    raw = "O111 | Delta Ltd | MAP"
    result = _parse_section(raw, "funding_eligible")
    assert "Delta Ltd" in result
    assert "MAP" in result


def test_parse_section_aws_stage_format():
    from app.ace_hygiene import _parse_section
    raw = "O222 | Acme | Qualified | Prospect"
    result = _parse_section(raw, "aws_stage")
    assert "AWS: Qualified" in result
    assert "Partner: Prospect" in result


def test_parse_section_cosell_format():
    from app.ace_hygiene import _parse_section
    raw = "O333 | Gamma Corp | James O'Brien | £120k"
    result = _parse_section(raw, "cosell")
    assert "Rep: James O'Brien" in result


def test_parse_section_passthrough_query_failed():
    from app.ace_hygiene import _parse_section
    raw = "Query failed — check MCP connection."
    result = _parse_section(raw, "action_required")
    assert result == raw


def test_parse_section_caps_at_10_rows():
    from app.ace_hygiene import _parse_section
    rows = "\n".join(f"O{i} | Co{i} | Issue {i} | {i}" for i in range(15))
    result = _parse_section(rows, "action_required")
    assert result.count("d left") == 10


# ── _compute_health_score ─────────────────────────────────────────────────────

def test_health_score_perfect():
    from app.ace_hygiene import _compute_health_score
    assert _compute_health_score({}) == 10


def test_health_score_action_required_deducts():
    from app.ace_hygiene import _compute_health_score
    sections = {"action_required": "O1 | Acme | submit | 3\nO2 | Beta | update | 7"}
    score = _compute_health_score(sections)
    assert score < 10


def test_health_score_capped_at_zero():
    from app.ace_hygiene import _compute_health_score
    sections = {
        "action_required":  "\n".join(f"O{i} | Co{i} | action | {i}" for i in range(10)),
        "stale_launched":   "\n".join(f"O{i} | Co{i} | £100k | {i}" for i in range(5)),
        "aws_stage":        "\n".join(f"O{i} | Co{i} | Qual | Pros" for i in range(5)),
        "past_close_dates": "\n".join(f"O{i} | Co{i} | 2025-01-0{i%9+1}" for i in range(5)),
    }
    assert _compute_health_score(sections) == 0


def test_health_score_cosell_bonus_capped_at_10():
    from app.ace_hygiene import _compute_health_score
    sections = {"cosell": "O1 | Acme | Bob Smith | £100k"}
    assert _compute_health_score(sections) == 10


def test_health_score_max_10():
    from app.ace_hygiene import _compute_health_score
    assert _compute_health_score({}) <= 10


def test_health_score_stale_deducts_2():
    from app.ace_hygiene import _compute_health_score
    sections = {"stale_launched": "O1 | Acme | £100k | 45"}
    assert _compute_health_score(sections) == 8


# ── _score_label ──────────────────────────────────────────────────────────────

def test_score_label_good():
    from app.ace_hygiene import _score_label
    assert _score_label(9) == "GOOD"
    assert _score_label(8) == "GOOD"


def test_score_label_fair():
    from app.ace_hygiene import _score_label
    assert _score_label(7) == "FAIR"
    assert _score_label(5) == "FAIR"


def test_score_label_poor():
    from app.ace_hygiene import _score_label
    assert _score_label(4) == "POOR"
    assert _score_label(0) == "POOR"


# ── _build_action_plan ────────────────────────────────────────────────────────

def test_action_plan_urgent_first():
    from app.ace_hygiene import _build_action_plan
    sections = {
        "action_required":  "O1 | Acme | submit win wire | 3",
        "past_close_dates": "O2 | Beta | 2026-01-01",
    }
    plan = _build_action_plan(sections)
    assert len(plan) >= 2
    assert "URGENT" in plan[0]


def test_action_plan_clean_pipeline():
    from app.ace_hygiene import _build_action_plan
    plan = _build_action_plan({})
    assert len(plan) == 1
    assert "clean" in plan[0].lower() or "no immediate" in plan[0].lower()


def test_action_plan_funding_medium_priority():
    from app.ace_hygiene import _build_action_plan
    sections = {"funding_eligible": "O3 | Corp | MAP"}
    plan = _build_action_plan(sections)
    assert any("MEDIUM" in p for p in plan)


# ── MCP prompt format ─────────────────────────────────────────────────────────

def test_queries_contain_respond_with_only():
    from app.ace_hygiene import _QUERIES
    for key, query in _QUERIES.items():
        assert "Respond with ONLY" in query, f"Query '{key}' missing 'Respond with ONLY'"


def test_queries_contain_pipe_format():
    from app.ace_hygiene import _QUERIES
    for key, query in _QUERIES.items():
        assert "|" in query, f"Query '{key}' missing pipe-delimited format"


def test_queries_contain_no_other_text_instruction():
    from app.ace_hygiene import _QUERIES
    for key, query in _QUERIES.items():
        assert "other text" in query.lower(), (
            f"Query '{key}' missing 'other text' instruction"
        )


# ── _mcp helper ───────────────────────────────────────────────────────────────

_MCP_RESPONSE = {
    "text": json.dumps({
        "content": [
            {"type": "ASSISTANT_RESPONSE", "content": {
                "text": "Let me check.\nO12345 | Acme Corp | Missing date | 5"
            }},
        ]
    })
}


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=_MCP_RESPONSE)
async def test_mcp_strips_narrative(mock_send):
    from app.ace_hygiene import _mcp
    result = await _mcp("any query")
    assert "Let me" not in result
    assert "Acme Corp" in result


@patch("app.mcp_client.send_message", new_callable=AsyncMock, side_effect=Exception("timeout"))
async def test_mcp_returns_fallback_on_exception(mock_send):
    from app.ace_hygiene import _mcp
    result = await _mcp("any query")
    assert result == "Query failed — check MCP connection."


# ── run_hygiene ───────────────────────────────────────────────────────────────

@patch("app.ace_hygiene._mcp", new_callable=AsyncMock,
       return_value="O1 | Acme | needs action | 3")
async def test_run_hygiene_returns_all_keys(mock_mcp):
    from app.ace_hygiene import run_hygiene
    result = await run_hygiene()
    for key in ("date", "health_score", "health_label", "action_plan",
                "action_required", "stale_launched", "funding_eligible",
                "aws_stage", "past_close_dates", "cosell"):
        assert key in result


@patch("app.ace_hygiene._mcp", new_callable=AsyncMock, return_value="No data available.")
async def test_run_hygiene_clean_pipeline_score_10(mock_mcp):
    from app.ace_hygiene import run_hygiene
    result = await run_hygiene()
    assert result["health_score"] == 10
    assert result["health_label"] == "GOOD"


@patch("app.ace_hygiene._mcp", new_callable=AsyncMock,
       return_value="O1 | Acme | action required | 3")
async def test_run_hygiene_action_required_lowers_score(mock_mcp):
    from app.ace_hygiene import run_hygiene
    result = await run_hygiene()
    assert result["health_score"] < 10


# ── _build_hygiene_card (no Container backgrounds) ───────────────────────────

_DATA = {
    "date": "07 Apr 2026",
    "health_score": 7,
    "health_label": "FAIR",
    "action_plan": ["HIGH: Update past close dates.", "MEDIUM: Submit funding apps."],
    "action_required":  "No data available.",
    "stale_launched":   "O1 | Acme Ltd | £100k | 45 days stale",
    "funding_eligible": "O2 | Beta Corp | MAP",
    "aws_stage":        "No data available.",
    "past_close_dates": "O3 | Corp Ltd | 2026-03-01",
    "cosell":           "O4 | Delta | Bob Smith | £80k",
}


def test_build_hygiene_card_is_message_envelope():
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    assert card["type"] == "message"
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


def test_build_hygiene_card_no_coloured_container_backgrounds():
    """Card body must NOT contain any Container with a style attribute."""
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    body = card["attachments"][0]["content"]["body"]
    for item in body:
        if item.get("type") == "Container":
            assert "style" not in item, (
                f"Found Container with style — coloured backgrounds are forbidden: {item}"
            )


def test_build_hygiene_card_header_is_textblock_not_container():
    """The header must be a plain TextBlock, not a Container."""
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    body = card["attachments"][0]["content"]["body"]
    header = body[0]
    assert header["type"] == "TextBlock"
    assert header.get("weight") == "bolder"


def test_build_hygiene_card_header_contains_score():
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    card_json = json.dumps(card)
    assert "7/10" in card_json
    assert "FAIR" in card_json


def test_build_hygiene_card_header_color_warning_at_7():
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    header = card["attachments"][0]["content"]["body"][0]
    assert header.get("color") == "warning"


def test_build_hygiene_card_header_color_good_at_8():
    from app.ace_hygiene import _build_hygiene_card
    data = {**_DATA, "health_score": 8, "health_label": "GOOD"}
    card = _build_hygiene_card(data)
    header = card["attachments"][0]["content"]["body"][0]
    assert header.get("color") == "good"


def test_build_hygiene_card_header_color_attention_at_4():
    from app.ace_hygiene import _build_hygiene_card
    data = {**_DATA, "health_score": 4, "health_label": "POOR"}
    card = _build_hygiene_card(data)
    header = card["attachments"][0]["content"]["body"][0]
    assert header.get("color") == "attention"


def test_build_hygiene_card_has_do_this_today():
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    card_json = json.dumps(card)
    assert "DO THIS TODAY" in card_json
    assert "HIGH: Update past close dates" in card_json


def test_build_hygiene_card_has_health_score_factset():
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    card_json = json.dumps(card)
    assert "Pipeline Health" in card_json
    assert "Run Date" in card_json


def test_build_hygiene_card_shows_section_data():
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    card_json = json.dumps(card)
    assert "Acme Ltd" in card_json          # stale_launched row
    assert "Beta Corp" in card_json         # funding_eligible row
    assert "Bob Smith" in card_json         # cosell row


def test_build_hygiene_card_skips_empty_sections():
    from app.ace_hygiene import _build_hygiene_card
    card = _build_hygiene_card(_DATA)
    card_json = json.dumps(card)
    # action_required and aws_stage are "No data available." — headings should be absent
    # (they won't appear since _has_content returns False for those sections)
    body = card["attachments"][0]["content"]["body"]
    text_blocks = [b.get("text", "") for b in body if b.get("type") == "TextBlock"]
    # Should not show a section heading for sections with no data
    assert not any("ACTION REQUIRED" in t and "No data" not in t
                   and "ACTION PLAN" not in t
                   for t in text_blocks), "ACTION REQUIRED should not appear with no data"


def test_build_hygiene_card_under_20kb():
    from app.ace_hygiene import _build_hygiene_card
    data = {
        "date": "07 Apr 2026",
        "health_score": 5,
        "health_label": "FAIR",
        "action_plan": [
            "URGENT: Resolve Action Required items.",
            "HIGH: Update past close dates.",
            "MEDIUM: Submit funding applications.",
        ],
        "action_required":  "\n".join(
            f"O{i} | Company {i} | Missing field | {i+1}" for i in range(5)
        ),
        "stale_launched":   "\n".join(
            f"O{i} | Company {i} | £100k | {30+i} days stale" for i in range(5)
        ),
        "funding_eligible": "\n".join(
            f"O{i} | Company {i} | MAP" for i in range(3)
        ),
        "aws_stage":        "No data available.",
        "past_close_dates": "O99 | Final Corp | 2026-01-15",
        "cosell":           "O88 | Active Ltd | James O'Brien | £250k",
    }
    card = _build_hygiene_card(data)
    assert len(json.dumps(card)) < 20_480


# ── post_hygiene_to_teams ─────────────────────────────────────────────────────

@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_post_hygiene_calls_post_to_ace(mock_raw):
    from app.ace_hygiene import post_hygiene_to_teams
    result = await post_hygiene_to_teams(_DATA)
    assert result is True
    mock_raw.assert_called_once()


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_post_hygiene_title_has_score(mock_raw):
    from app.ace_hygiene import post_hygiene_to_teams
    await post_hygiene_to_teams(_DATA)
    card_json = json.dumps(mock_raw.call_args[0][0])
    assert "ACE HYGIENE" in card_json
    assert "7/10" in card_json
    assert "FAIR" in card_json


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_post_hygiene_body_has_do_this_today(mock_raw):
    from app.ace_hygiene import post_hygiene_to_teams
    await post_hygiene_to_teams(_DATA)
    card_json = json.dumps(mock_raw.call_args[0][0])
    assert "DO THIS TODAY" in card_json
    assert "HIGH:" in card_json


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_post_hygiene_has_health_score_factset(mock_raw):
    from app.ace_hygiene import post_hygiene_to_teams
    await post_hygiene_to_teams(_DATA)
    card_json = json.dumps(mock_raw.call_args[0][0])
    assert "Pipeline Health" in card_json
    assert "7/10" in card_json


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_post_hygiene_no_container_backgrounds(mock_raw):
    from app.ace_hygiene import post_hygiene_to_teams
    await post_hygiene_to_teams(_DATA)
    card = mock_raw.call_args[0][0]
    body = card["attachments"][0]["content"]["body"]
    for item in body:
        if item.get("type") == "Container":
            assert "style" not in item, "Container with style (coloured background) found"


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=False)
async def test_post_hygiene_fallback_on_failure(mock_raw):
    from app.ace_hygiene import post_hygiene_to_teams
    # First call fails (full card), second call is fallback simple payload
    mock_raw.return_value = False
    result = await post_hygiene_to_teams(_DATA)
    assert mock_raw.call_count == 2   # tried card, then fallback
