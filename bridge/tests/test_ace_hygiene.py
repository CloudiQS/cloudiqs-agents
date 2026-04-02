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


def test_count_items_with_data():
    from app.ace_hygiene import _count_items
    text = "O12345 Acme Corp: submit win wire\nO67890 Beta Ltd: update close date"
    assert _count_items(text) >= 1


# ── _compute_health_score ─────────────────────────────────────────────────────

def test_health_score_perfect():
    from app.ace_hygiene import _compute_health_score
    assert _compute_health_score({}) == 10


def test_health_score_action_required_deducts():
    from app.ace_hygiene import _compute_health_score
    sections = {"action_required": "O1 Acme: submit\nO2 Beta: update"}
    score = _compute_health_score(sections)
    assert score < 10


def test_health_score_capped_at_zero():
    from app.ace_hygiene import _compute_health_score
    sections = {
        "action_required":  "\n".join(f"O{i} Co{i}: action" for i in range(10)),
        "stale_launched":   "\n".join(f"O{i} Co{i}: stale"  for i in range(5)),
        "aws_stage":        "\n".join(f"O{i} Co{i}: mismatch" for i in range(5)),
        "past_close_dates": "\n".join(f"O{i} Co{i}: past"   for i in range(5)),
    }
    assert _compute_health_score(sections) == 0


def test_health_score_cosell_bonus():
    from app.ace_hygiene import _compute_health_score
    # A perfect pipeline plus co-sell should stay at 10 (cannot exceed 10)
    sections = {"cosell": "Bob Smith on Acme"}
    assert _compute_health_score(sections) == 10


def test_health_score_max_10():
    from app.ace_hygiene import _compute_health_score
    assert _compute_health_score({}) <= 10


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
        "action_required":  "O1 Acme: submit win wire",
        "past_close_dates": "O2 Beta: overdue",
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
    sections = {"funding_eligible": "O3 Corp eligible MAP $25k"}
    plan = _build_action_plan(sections)
    assert any("MEDIUM" in p for p in plan)


# ── _mcp helper ───────────────────────────────────────────────────────────────

_MCP_RESPONSE = {
    "text": json.dumps({
        "content": [
            {"type": "ASSISTANT_RESPONSE", "content": {
                "text": "Let me check.\nO12345 Acme Corp: Action Required."
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

@patch("app.ace_hygiene._mcp", new_callable=AsyncMock, return_value="O1 Acme: needs action")
async def test_run_hygiene_returns_all_keys(mock_mcp):
    from app.ace_hygiene import run_hygiene
    result = await run_hygiene()
    for key in ("date", "health_score", "health_label", "action_plan",
                "action_required", "stale_launched", "funding_eligible",
                "aws_stage", "past_close_dates", "cosell"):
        assert key in result


@patch("app.ace_hygiene._mcp", new_callable=AsyncMock, return_value="None found.")
async def test_run_hygiene_clean_pipeline_score_10(mock_mcp):
    from app.ace_hygiene import run_hygiene
    result = await run_hygiene()
    assert result["health_score"] == 10
    assert result["health_label"] == "GOOD"


@patch("app.ace_hygiene._mcp", new_callable=AsyncMock, return_value="O1 Acme: action required")
async def test_run_hygiene_action_required_lowers_score(mock_mcp):
    from app.ace_hygiene import run_hygiene
    result = await run_hygiene()
    assert result["health_score"] < 10


# ── post_hygiene_to_teams ─────────────────────────────────────────────────────

_DATA = {
    "date": "07 Apr 2026",
    "health_score": 7,
    "health_label": "FAIR",
    "action_plan": ["HIGH: Update past close dates.", "MEDIUM: Submit funding apps."],
    "action_required": "None found.",
    "stale_launched": "O1 Acme: 45 days stale",
    "funding_eligible": "O2 Beta eligible MAP $25k",
    "aws_stage": "None found.",
    "past_close_dates": "O3 Corp: 2026-03-01",
    "cosell": "Bob Smith on O4",
}


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
async def test_post_hygiene_body_has_action_plan(mock_raw):
    from app.ace_hygiene import post_hygiene_to_teams
    await post_hygiene_to_teams(_DATA)
    card_json = json.dumps(mock_raw.call_args[0][0])
    assert "ACTION PLAN" in card_json
    assert "HIGH:" in card_json


@patch("app.teams._post_raw", new_callable=AsyncMock, return_value=True)
async def test_post_hygiene_facts_has_score(mock_raw):
    from app.ace_hygiene import post_hygiene_to_teams
    await post_hygiene_to_teams(_DATA)
    card_json = json.dumps(mock_raw.call_args[0][0])
    assert "Health score" in card_json
    assert "7/10" in card_json
