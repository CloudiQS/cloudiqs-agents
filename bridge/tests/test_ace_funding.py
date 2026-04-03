"""Unit tests for app.ace_funding — funding eligibility checker."""
import json
from unittest.mock import AsyncMock, patch


# ── _count_eligible ───────────────────────────────────────────────────────────

def test_count_eligible_empty():
    from app.ace_funding import _count_eligible
    assert _count_eligible("") == 0


def test_count_eligible_none_found():
    from app.ace_funding import _count_eligible
    assert _count_eligible("None found.") == 0


def test_count_eligible_with_items():
    from app.ace_funding import _count_eligible
    text = "O1 Acme: MAP $25k\nO2 Beta: POC $10k"
    assert _count_eligible(text) >= 1


# ── _mcp helper ───────────────────────────────────────────────────────────────

_MCP_RESPONSE = {
    "text": json.dumps({
        "content": [
            {"type": "ASSISTANT_RESPONSE", "content": {
                "text": "Let me check.\nO1 Acme Corp: eligible MAP $25k."
            }},
        ]
    })
}


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=_MCP_RESPONSE)
async def test_mcp_strips_narrative(mock_send):
    from app.ace_funding import _mcp
    result = await _mcp("any query")
    assert "Let me" not in result
    assert "Acme Corp" in result


@patch("app.mcp_client.send_message", new_callable=AsyncMock, side_effect=Exception("timeout"))
async def test_mcp_returns_fallback_on_exception(mock_send):
    from app.ace_funding import _mcp
    result = await _mcp("any query")
    assert result == "Query failed — check MCP connection."


# ── run_funding_check ─────────────────────────────────────────────────────────

@patch("app.ace_funding._mcp", new_callable=AsyncMock, return_value="O1 Acme: MAP $25k")
async def test_run_funding_check_returns_all_keys(mock_mcp):
    from app.ace_funding import run_funding_check
    result = await run_funding_check()
    for key in ("date", "eligible", "active", "programs", "eligible_count", "action_items"):
        assert key in result


@patch("app.ace_funding._mcp", new_callable=AsyncMock, return_value="None found.")
async def test_run_funding_check_no_eligible(mock_mcp):
    from app.ace_funding import run_funding_check
    result = await run_funding_check()
    assert result["eligible_count"] == 0
    assert len(result["action_items"]) >= 1


@patch("app.ace_funding._mcp", new_callable=AsyncMock,
       return_value="O1 | Acme Ltd | MAP | $25,000\nO2 | Beta Corp | POC | $10,000")
async def test_run_funding_check_action_items_include_submit(mock_mcp):
    from app.ace_funding import run_funding_check
    result = await run_funding_check()
    assert result["eligible_count"] >= 1
    assert any("Submit" in a or "submit" in a for a in result["action_items"])


# ── post_funding_to_teams ─────────────────────────────────────────────────────

_DATA = {
    "date": "07 Apr 2026",
    "eligible": "O1 Acme Corp: eligible MAP $25k\nO2 Beta: POC $10k",
    "active": "O3 Corp: MAP application pending",
    "programs": "MAP: up to $25k for cloud migrations",
    "eligible_count": 2,
    "action_items": ["Submit funding applications for 2 eligible opportunities."],
}


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_post_funding_calls_post_to_ace(mock_post):
    from app.ace_funding import post_funding_to_teams
    result = await post_funding_to_teams(_DATA)
    assert result is True
    mock_post.assert_called_once()


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_post_funding_title_has_count(mock_post):
    from app.ace_funding import post_funding_to_teams
    await post_funding_to_teams(_DATA)
    kwargs = mock_post.call_args[1]
    title = kwargs.get("title") or mock_post.call_args[0][0]
    assert "ACE FUNDING" in title
    assert "2 eligible" in title


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_post_funding_body_has_actions(mock_post):
    from app.ace_funding import post_funding_to_teams
    await post_funding_to_teams(_DATA)
    kwargs = mock_post.call_args[1]
    body = kwargs.get("body_text") or mock_post.call_args[0][1]
    assert "ACTIONS" in body
    assert "ELIGIBLE" in body


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_post_funding_facts_has_eligible_count(mock_post):
    from app.ace_funding import post_funding_to_teams
    await post_funding_to_teams(_DATA)
    kwargs = mock_post.call_args[1]
    facts = kwargs.get("facts") or []
    assert any(f["title"] == "Eligible opportunities" and f["value"] == "2" for f in facts)
