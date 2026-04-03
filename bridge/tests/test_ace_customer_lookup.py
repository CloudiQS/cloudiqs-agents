"""Unit tests for app.ace_customer_lookup — structured MCP prompt + key:value parser."""
from unittest.mock import AsyncMock, patch


# ── _parse_structured_response ────────────────────────────────────────────────

_FULL_RESPONSE = """\
AWS_CUSTOMER: Yes
SERVICES: EC2, S3, RDS, CloudFront
PRIMARY_REGION: eu-west-1
MONTHLY_SPEND: $42,000
SPEND_TREND: Growing
ACCOUNT_AGE: 4 years
SUPPORT_PLAN: Business
ACCOUNT_OWNER: James O'Brien (james.obrien@amazon.com)
EXISTING_ACE: O1234567, O7654321
PARTNER_HISTORY: Engaged with AWS ProServe in 2024
PROGRAMMES: MAP active
"""

_MINIMAL_RESPONSE = """\
AWS_CUSTOMER: No
SERVICES: UNKNOWN
PRIMARY_REGION: UNKNOWN
MONTHLY_SPEND: UNKNOWN
SPEND_TREND: UNKNOWN
ACCOUNT_AGE: UNKNOWN
SUPPORT_PLAN: UNKNOWN
ACCOUNT_OWNER: UNKNOWN
EXISTING_ACE: None
PARTNER_HISTORY: None
PROGRAMMES: None
"""


def test_parse_aws_customer_yes():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert result["aws_customer"] is True


def test_parse_aws_customer_no():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_MINIMAL_RESPONSE)
    assert result["aws_customer"] is False


def test_parse_aws_customer_absent_is_none():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response("SERVICES: EC2")
    assert result["aws_customer"] is None


def test_parse_services():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert "EC2" in result["aws_services"]
    assert "S3" in result["aws_services"]


def test_parse_region():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert result["aws_region"] == "eu-west-1"


def test_parse_spend():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert result["aws_spend"] == "$42,000"


def test_parse_account_owner():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert "James O'Brien" in result["aws_account_owner"]
    assert "james.obrien@amazon.com" in result["aws_account_owner"]


def test_parse_existing_ace_opp_ids():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert "O1234567" in result["ace_opportunities"]
    assert "O7654321" in result["ace_opportunities"]
    assert result["aws_existing_opps"] == result["ace_opportunities"]


def test_parse_existing_ace_none_stays_empty():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_MINIMAL_RESPONSE)
    assert result["ace_opportunities"] == ""
    assert result["aws_existing_opps"] == ""


def test_parse_spend_trend_in_aws_services():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert "Spend trend: Growing" in result["aws_services"]


def test_parse_support_plan_in_aws_services():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert "Support plan: Business" in result["aws_services"]


def test_parse_programmes_in_aws_services():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert "Programmes: MAP active" in result["aws_services"]


def test_parse_partner_history_in_aws_services():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    assert "Partner history:" in result["aws_services"]


def test_parse_unknown_values_skipped():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_MINIMAL_RESPONSE)
    # All UNKNOWN → empty strings (except aws_customer which is False from "No")
    assert result["aws_region"] == ""
    assert result["aws_spend"] == ""
    assert result["aws_account_owner"] == ""


def test_parse_none_values_not_in_services():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_MINIMAL_RESPONSE)
    assert "none" not in result["aws_services"].lower()


def test_parse_empty_text_returns_empty_fields():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response("")
    assert result["aws_customer"] is None
    assert result["aws_services"] == ""
    assert result["aws_region"] == ""


def test_parse_ignores_non_key_value_lines():
    from app.ace_customer_lookup import _parse_structured_response
    text = "Let me analyze that.\nAWS_CUSTOMER: Yes\nSERVICES: EC2\nHere is what I found."
    result = _parse_structured_response(text)
    # Should still parse the valid key:value lines
    assert result["aws_customer"] is True
    assert "EC2" in result["aws_services"]


def test_parse_result_has_all_expected_keys():
    from app.ace_customer_lookup import _parse_structured_response
    result = _parse_structured_response(_FULL_RESPONSE)
    for key in ("aws_customer", "aws_services", "aws_region", "aws_spend",
                "aws_account_owner", "aws_existing_opps", "ace_opportunities"):
        assert key in result, f"Missing key: {key}"


# ── MCP prompt format ─────────────────────────────────────────────────────────

def test_prompt_template_contains_all_fields():
    from app.ace_customer_lookup import _PROMPT_TEMPLATE
    required_fields = [
        "AWS_CUSTOMER", "SERVICES", "PRIMARY_REGION", "MONTHLY_SPEND",
        "SPEND_TREND", "ACCOUNT_AGE", "SUPPORT_PLAN", "ACCOUNT_OWNER",
        "EXISTING_ACE", "PARTNER_HISTORY", "PROGRAMMES",
    ]
    for field in required_fields:
        assert field in _PROMPT_TEMPLATE, f"Field '{field}' missing from prompt template"


def test_prompt_template_has_no_commentary_instruction():
    from app.ace_customer_lookup import _PROMPT_TEMPLATE
    assert "No commentary" in _PROMPT_TEMPLATE


def test_prompt_template_has_unknown_fallback():
    from app.ace_customer_lookup import _PROMPT_TEMPLATE
    assert "UNKNOWN" in _PROMPT_TEMPLATE


def test_prompt_template_uses_company_placeholder():
    from app.ace_customer_lookup import _PROMPT_TEMPLATE
    assert "{company}" in _PROMPT_TEMPLATE


# ── customer_lookup() integration ─────────────────────────────────────────────

_MCP_RESP = {
    "text": (
        "AWS_CUSTOMER: Yes\n"
        "SERVICES: EC2, S3\n"
        "PRIMARY_REGION: eu-west-1\n"
        "MONTHLY_SPEND: $20,000\n"
        "SPEND_TREND: Flat\n"
        "ACCOUNT_AGE: 2 years\n"
        "SUPPORT_PLAN: Developer\n"
        "ACCOUNT_OWNER: Alice Smith (alice@amazon.com)\n"
        "EXISTING_ACE: O9876543\n"
        "PARTNER_HISTORY: None\n"
        "PROGRAMMES: None\n"
    )
}


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=_MCP_RESP)
async def test_customer_lookup_returns_parsed_fields(mock_send):
    from app.ace_customer_lookup import customer_lookup
    result = await customer_lookup("UK Tote Group")
    assert result["aws_customer"] is True
    assert "EC2" in result["aws_services"]
    assert result["aws_region"] == "eu-west-1"
    assert result["aws_spend"] == "$20,000"
    assert "Alice Smith" in result["aws_account_owner"]
    assert "O9876543" in result["ace_opportunities"]


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=_MCP_RESP)
async def test_customer_lookup_includes_website_in_prompt(mock_send):
    from app.ace_customer_lookup import customer_lookup
    await customer_lookup("UK Tote Group", website="uktote.co.uk")
    prompt_sent = mock_send.call_args[0][0]
    assert "uktote.co.uk" in prompt_sent


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=_MCP_RESP)
async def test_customer_lookup_prompt_contains_all_fields(mock_send):
    from app.ace_customer_lookup import customer_lookup
    await customer_lookup("Acme Ltd")
    prompt_sent = mock_send.call_args[0][0]
    for field in ("AWS_CUSTOMER", "SERVICES", "PRIMARY_REGION", "MONTHLY_SPEND",
                  "EXISTING_ACE", "PROGRAMMES"):
        assert field in prompt_sent


@patch("app.mcp_client.send_message", new_callable=AsyncMock, side_effect=Exception("timeout"))
async def test_customer_lookup_returns_empty_on_mcp_failure(mock_send):
    from app.ace_customer_lookup import customer_lookup
    result = await customer_lookup("Acme Ltd")
    assert result["aws_customer"] is None
    assert result["aws_services"] == ""
    assert result["aws_region"] == ""


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value={"text": ""})
async def test_customer_lookup_returns_unknown_on_blank_response(mock_send):
    """Blank MCP response (e.g. 'outside the scope') → aws_customer='unknown', not None."""
    from app.ace_customer_lookup import customer_lookup
    result = await customer_lookup("Acme Ltd")
    assert result["aws_customer"] == "unknown"


@patch("app.mcp_client.send_message", new_callable=AsyncMock, return_value=None)
async def test_customer_lookup_returns_unknown_on_none_response(mock_send):
    """None MCP response (no reply) → aws_customer='unknown', empty other fields."""
    from app.ace_customer_lookup import customer_lookup
    result = await customer_lookup("Acme Ltd")
    assert result["aws_customer"] == "unknown"
