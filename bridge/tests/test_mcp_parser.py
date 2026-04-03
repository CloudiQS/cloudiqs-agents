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


def test_strip_removes_this_request_requires():
    from app.mcp_parser import strip_narrative
    text = "This request requires me to.\nO123 | Acme | action | 5"
    result = strip_narrative(text)
    assert "This request requires" not in result
    assert "O123" in result


def test_strip_removes_deal_progression_advisor():
    from app.mcp_parser import strip_narrative
    text = "The deal_progression_advisor agent will help you.\nO123 data row."
    result = strip_narrative(text)
    assert "deal_progression_advisor" not in result


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


# ── parse_pipe_rows ───────────────────────────────────────────────────────────

def test_parse_pipe_rows_basic():
    from app.mcp_parser import parse_pipe_rows
    text = "O123 | Acme Ltd | Missing close date | 5"
    rows = parse_pipe_rows(text, 4)
    assert rows == [["O123", "Acme Ltd", "Missing close date", "5"]]


def test_parse_pipe_rows_multiple_rows():
    from app.mcp_parser import parse_pipe_rows
    text = "O123 | Acme | Issue A | 3\nO456 | Beta Corp | Issue B | 7"
    rows = parse_pipe_rows(text, 4)
    assert len(rows) == 2
    assert rows[0][0] == "O123"
    assert rows[1][0] == "O456"


def test_parse_pipe_rows_strips_chatbot_lines():
    from app.mcp_parser import parse_pipe_rows
    text = (
        "Let me check the pipeline.\n"
        "O123 | Acme | Missing date | 5\n"
        "I found 1 opportunity.\n"
        "O456 | Beta | No ARR | 2"
    )
    rows = parse_pipe_rows(text, 4)
    assert len(rows) == 2
    assert rows[0][0] == "O123"
    assert rows[1][0] == "O456"


def test_parse_pipe_rows_rejects_wrong_field_count():
    from app.mcp_parser import parse_pipe_rows
    text = "O123 | Acme | Only three fields"
    rows = parse_pipe_rows(text, 4)
    assert rows == []


def test_parse_pipe_rows_rejects_empty_field():
    from app.mcp_parser import parse_pipe_rows
    # Second field is blank — should be rejected
    text = "O123 |  | Missing company | 5"
    rows = parse_pipe_rows(text, 4)
    assert rows == []


def test_parse_pipe_rows_empty_input():
    from app.mcp_parser import parse_pipe_rows
    assert parse_pipe_rows("", 4) == []
    assert parse_pipe_rows("   \n   ", 4) == []


def test_parse_pipe_rows_rejects_header_row():
    from app.mcp_parser import parse_pipe_rows
    # Column header line — all fields are ALL_CAPS_UNDERSCORE
    text = "OPP_ID | COMPANY | ISSUE | DAYS_REMAINING\nO123 | Acme | Missing date | 5"
    rows = parse_pipe_rows(text, 4)
    assert len(rows) == 1
    assert rows[0][0] == "O123"


def test_parse_pipe_rows_three_fields():
    from app.mcp_parser import parse_pipe_rows
    text = "O789 | Gamma Ltd | MAP"
    rows = parse_pipe_rows(text, 3)
    assert rows == [["O789", "Gamma Ltd", "MAP"]]


def test_parse_pipe_rows_strips_whitespace_from_fields():
    from app.mcp_parser import parse_pipe_rows
    text = "  O123  |  Acme Ltd  |  Missing date  |  5  "
    rows = parse_pipe_rows(text, 4)
    assert rows == [["O123", "Acme Ltd", "Missing date", "5"]]


def test_parse_pipe_rows_rejects_this_request_requires():
    from app.mcp_parser import parse_pipe_rows
    text = "This request requires | me | to | access\nO123 | Acme | action | 3"
    rows = parse_pipe_rows(text, 4)
    assert len(rows) == 1
    assert rows[0][0] == "O123"


def test_parse_pipe_rows_rejects_would_you_like():
    from app.mcp_parser import parse_pipe_rows
    text = "Would you like | me | to | help\nO123 | Acme | action | 3"
    rows = parse_pipe_rows(text, 4)
    assert len(rows) == 1


def test_parse_pipe_rows_returns_empty_on_pure_narrative():
    from app.mcp_parser import parse_pipe_rows
    text = "Let me analyze this.\nI'll check the pipeline.\nHere's what I found."
    rows = parse_pipe_rows(text, 4)
    assert rows == []


def test_parse_pipe_rows_mixed_field_counts_kept_separately():
    from app.mcp_parser import parse_pipe_rows
    text = "O123 | Acme | MAP\nO456 | Beta | POC | extra_field"
    # Requesting 3 fields — only first row matches
    rows = parse_pipe_rows(text, 3)
    assert len(rows) == 1
    assert rows[0][2] == "MAP"


# ── parse_structured ──────────────────────────────────────────────────────────

def test_parse_structured_basic_key_value():
    from app.mcp_parser import parse_structured
    text = "AWS_CUSTOMER: Yes\nSERVICES: EC2, S3"
    result = parse_structured(text)
    assert result["AWS_CUSTOMER"] == "Yes"
    assert result["SERVICES"] == "EC2, S3"


def test_parse_structured_strips_commentary():
    from app.mcp_parser import parse_structured
    text = "Let me analyze that.\nAWS_CUSTOMER: Yes\nI found the data."
    result = parse_structured(text)
    assert "AWS_CUSTOMER" in result
    assert result["AWS_CUSTOMER"] == "Yes"


def test_parse_structured_rejects_non_alpha_keys():
    from app.mcp_parser import parse_structured
    text = "123_KEY: value\nAWS_CUSTOMER: Yes"
    result = parse_structured(text)
    assert "AWS_CUSTOMER" in result
    assert "123_KEY" not in result


def test_parse_structured_skips_empty_values():
    from app.mcp_parser import parse_structured
    text = "AWS_CUSTOMER: \nSERVICES: EC2"
    result = parse_structured(text)
    assert "AWS_CUSTOMER" not in result
    assert result["SERVICES"] == "EC2"


def test_parse_structured_empty_text_returns_empty_dict():
    from app.mcp_parser import parse_structured
    assert parse_structured("") == {}


def test_parse_structured_none_lines_ignored():
    from app.mcp_parser import parse_structured
    text = "AWS_CUSTOMER: Yes\nEXISTING_ACE: None"
    result = parse_structured(text)
    assert result["AWS_CUSTOMER"] == "Yes"
    assert result["EXISTING_ACE"] == "None"   # stored, not filtered (caller decides)


def test_parse_structured_uppercase_keys():
    from app.mcp_parser import parse_structured
    text = "primary_region: eu-west-1"
    result = parse_structured(text)
    assert "PRIMARY_REGION" in result
    assert result["PRIMARY_REGION"] == "eu-west-1"


def test_parse_structured_strips_would_you_like():
    from app.mcp_parser import parse_structured
    text = "Would you like me to continue?\nAWS_CUSTOMER: Yes"
    result = parse_structured(text)
    assert "AWS_CUSTOMER" in result
    assert len(result) == 1


def test_parse_structured_strips_sure_prefix():
    from app.mcp_parser import parse_structured
    text = "Sure, here are the results.\nSERVICES: EC2"
    result = parse_structured(text)
    assert "SERVICES" in result
    assert len(result) == 1


def test_parse_structured_handles_colons_in_value():
    from app.mcp_parser import parse_structured
    text = "ACCOUNT_OWNER: James O'Brien (james@amazon.com)"
    result = parse_structured(text)
    assert result["ACCOUNT_OWNER"] == "James O'Brien (james@amazon.com)"
