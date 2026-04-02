"""Unit tests for app.mcp_parser — shared MCP response parsing."""
import json


# ── parse_mcp_response ────────────────────────────────────────────────────────

def test_parse_assistant_response_block():
    from app.mcp_parser import parse_mcp_response
    result = {
        "text": json.dumps({
            "content": [
                {"type": "ASSISTANT_RESPONSE", "content": {"text": "5 deals at Committed."}},
                {"type": "serverToolResult", "content": {"data": "ignored"}},
            ]
        })
    }
    assert parse_mcp_response(result) == "5 deals at Committed."


def test_parse_multiple_assistant_blocks():
    from app.mcp_parser import parse_mcp_response
    result = {
        "text": json.dumps({
            "content": [
                {"type": "ASSISTANT_RESPONSE", "content": {"text": "Part one."}},
                {"type": "ASSISTANT_RESPONSE", "content": {"text": "Part two."}},
            ]
        })
    }
    text = parse_mcp_response(result)
    assert "Part one." in text
    assert "Part two." in text


def test_parse_strips_narrative():
    from app.mcp_parser import parse_mcp_response
    result = {
        "text": json.dumps({
            "content": [
                {"type": "ASSISTANT_RESPONSE", "content": {
                    "text": "Let me fetch that.\n5 deals at Committed.\nI'll analyze now."
                }},
            ]
        })
    }
    text = parse_mcp_response(result)
    assert "Let me" not in text
    assert "I'll analyze" not in text
    assert "5 deals" in text


def test_parse_falls_back_to_raw_on_non_json():
    from app.mcp_parser import parse_mcp_response
    result = {"text": "Plain text response"}
    assert "Plain text response" in parse_mcp_response(result)


def test_parse_returns_empty_on_none():
    from app.mcp_parser import parse_mcp_response
    assert parse_mcp_response(None) == ""


def test_parse_returns_empty_on_empty_text():
    from app.mcp_parser import parse_mcp_response
    assert parse_mcp_response({"text": ""}) == ""


def test_parse_text_type_blocks():
    from app.mcp_parser import parse_mcp_response
    result = {
        "text": json.dumps({
            "content": [
                {"type": "text", "text": "Pipeline data here."},
            ]
        })
    }
    assert "Pipeline data here." in parse_mcp_response(result)


# ── strip_narrative ───────────────────────────────────────────────────────────

def test_strip_removes_let_me():
    from app.mcp_parser import strip_narrative
    text = "Let me get that.\nActual data: 5 items."
    result = strip_narrative(text)
    assert "Let me" not in result
    assert "5 items" in result


def test_strip_removes_filler_line():
    from app.mcp_parser import strip_narrative
    text = "Sure!\nHere is your data.\nPipeline: 8 deals."
    result = strip_narrative(text)
    assert "Sure!" not in result
    assert "8 deals" in result


def test_strip_preserves_data():
    from app.mcp_parser import strip_narrative
    text = "Prospect: 8\nQualified: 4\nLaunched: 2"
    assert strip_narrative(text) == text


def test_strip_collapses_blanks():
    from app.mcp_parser import strip_narrative
    text = "Line one.\n\n\n\nLine two."
    result = strip_narrative(text)
    assert "\n\n\n" not in result


# ── truncate ──────────────────────────────────────────────────────────────────

def test_truncate_short_text_unchanged():
    from app.mcp_parser import truncate
    text = "Short text."
    assert truncate(text, 800) == text


def test_truncate_long_text():
    from app.mcp_parser import truncate
    text = "A" * 1000
    result = truncate(text, 800)
    assert len(result) < len(text)
    assert "..." in result


def test_truncate_breaks_on_newline():
    from app.mcp_parser import truncate
    text = "First line.\nSecond line very long " + "x" * 800
    result = truncate(text, 50)
    assert "First line." in result


# ── extract_facts ─────────────────────────────────────────────────────────────

def test_extract_facts_basic():
    from app.mcp_parser import extract_facts
    text = "Stage: Prospect\nDeals: 8\nRevenue: £200K"
    facts = extract_facts(text)
    assert {"title": "Stage", "value": "Prospect"} in facts
    assert {"title": "Deals", "value": "8"} in facts


def test_extract_facts_max_10():
    from app.mcp_parser import extract_facts
    text = "\n".join(f"Key{i}: Value{i}" for i in range(20))
    assert len(extract_facts(text)) <= 10


def test_extract_facts_skips_long_lines():
    from app.mcp_parser import extract_facts
    long_line = "Key: " + "V" * 200
    facts = extract_facts(long_line)
    assert len(facts) == 0


def test_extract_facts_strips_bullets():
    from app.mcp_parser import extract_facts
    text = "- Stage: Prospect\n* Count: 5"
    facts = extract_facts(text)
    assert any(f["title"] == "Stage" for f in facts)
    assert any(f["title"] == "Count" for f in facts)
