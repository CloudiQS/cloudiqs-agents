"""
AWS Intelligence endpoint — POST /ace/customer-lookup.

Sends one structured MCP query per company that forces Partner Central
to return key:value pairs in a fixed format. The response is parsed
into discrete fields — no conversational AI text is stored.

Returns empty fields on MCP failure — never blocks the lead pipeline.
Agents call this during research (Step 4) and include the results in the
POST /lead payload as aws_customer, aws_services, aws_region, etc.

Reference: docs/LEAD-GOLD-STANDARD-COMPLETE.md section 7.
"""

import logging

from app import mcp_client
from app.config import get_secret, is_dummy

logger = logging.getLogger("bridge")

# Fields included in the MCP structured prompt
_PROMPT_TEMPLATE = """\
For {company}, provide the following in this exact format, one field per line. No commentary.

AWS_CUSTOMER: Yes/No
SERVICES: [comma separated list]
PRIMARY_REGION: [region]
MONTHLY_SPEND: $[amount]
SPEND_TREND: Growing/Flat/Declining
ACCOUNT_AGE: [years]
SUPPORT_PLAN: Basic/Developer/Business/Enterprise
ACCOUNT_OWNER: [name] ([email])
EXISTING_ACE: [opp IDs or None]
PARTNER_HISTORY: [any previous partner engagements or None]
PROGRAMMES: [MAP/POC/CEI active or None]

If data is not available for a field, write UNKNOWN.\
"""


def _get_catalog() -> str:
    """Return configured catalog name, defaulting to Sandbox."""
    try:
        catalog = get_secret("partner-central/catalog")
        return catalog if not is_dummy(catalog) else "Sandbox"
    except Exception:
        return "Sandbox"


def _parse_structured_response(text: str) -> dict:
    """Parse the key:value MCP response into customer lookup fields.

    Reads lines of the form "KEY: value". Lines that are not in that format,
    or whose value is UNKNOWN, are silently skipped.

    SPEND_TREND, ACCOUNT_AGE, SUPPORT_PLAN, PARTNER_HISTORY, and PROGRAMMES
    are bundled into aws_services as labelled context (no dedicated model field).

    Returns a dict with the same keys as customer_lookup() — all strings / bool.
    """
    parsed: dict[str, str] = {}
    for line in text.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key   = key.strip().upper()
        value = value.strip()
        if value and value.upper() != "UNKNOWN":
            parsed[key] = value

    result: dict = {
        "aws_customer":      None,
        "aws_services":      "",
        "aws_region":        "",
        "aws_spend":         "",
        "aws_account_owner": "",
        "aws_existing_opps": "",
        "ace_opportunities": "",
    }

    # AWS_CUSTOMER → bool (or None if absent/unknown)
    aws_cust = parsed.get("AWS_CUSTOMER", "").lower()
    if aws_cust == "yes":
        result["aws_customer"] = True
    elif aws_cust == "no":
        result["aws_customer"] = False

    result["aws_region"]        = parsed.get("PRIMARY_REGION", "")
    result["aws_spend"]         = parsed.get("MONTHLY_SPEND", "")
    result["aws_account_owner"] = parsed.get("ACCOUNT_OWNER", "")

    # EXISTING_ACE → both opp fields (skip if "None")
    existing = parsed.get("EXISTING_ACE", "")
    if existing and existing.lower() != "none":
        result["ace_opportunities"] = existing
        result["aws_existing_opps"] = existing

    # Build aws_services from SERVICES + additional context fields
    services_parts: list[str] = []
    base_services = parsed.get("SERVICES", "")
    if base_services:
        services_parts.append(base_services)

    for field_key, label in [
        ("SPEND_TREND",      "Spend trend"),
        ("ACCOUNT_AGE",      "Account age"),
        ("SUPPORT_PLAN",     "Support plan"),
        ("PARTNER_HISTORY",  "Partner history"),
        ("PROGRAMMES",       "Programmes"),
    ]:
        val = parsed.get(field_key, "")
        if val and val.lower() not in ("none", "unknown"):
            services_parts.append(f"{label}: {val}")

    result["aws_services"] = " | ".join(services_parts)

    return result


async def customer_lookup(company: str, website: str = "") -> dict:
    """Query Partner Central MCP for AWS customer intelligence.

    Sends a single structured prompt that forces key:value output.
    Parses the response into discrete fields — no raw AI text stored.

    Args:
        company:  Company name (e.g. "UK Tote Group")
        website:  Company website domain (optional, for disambiguation)

    Returns:
        dict with keys:
          aws_customer      (bool | None)  — True/False/None
          aws_services      (str)          — services + spend trend + programmes
          aws_region        (str)          — primary deployment region
          aws_spend         (str)          — monthly spend estimate
          aws_account_owner (str)          — AWS account owner name/email
          aws_existing_opps (str)          — existing ACE opportunity IDs
          ace_opportunities (str)          — same as aws_existing_opps (compat)
    """
    empty: dict = {
        "aws_customer":      None,
        "aws_services":      "",
        "aws_region":        "",
        "aws_spend":         "",
        "aws_account_owner": "",
        "aws_existing_opps": "",
        "ace_opportunities": "",
    }

    catalog     = _get_catalog()
    company_ref = f"{company} ({website})" if website else company
    prompt      = _PROMPT_TEMPLATE.format(company=company_ref)

    try:
        resp = await mcp_client.send_message(prompt, catalog=catalog)
    except Exception as exc:
        logger.warning(
            "ace_customer_lookup_mcp_failed",
            extra={"company": company, "error": str(exc)},
        )
        return empty

    if not resp or not resp.get("text"):
        logger.warning("ace_customer_lookup_empty_response", extra={"company": company})
        return empty

    text = resp["text"].strip()
    result = _parse_structured_response(text)

    logger.info(
        "ace_customer_lookup_ok",
        extra={
            "company":      company,
            "aws_customer": result["aws_customer"],
            "aws_region":   result["aws_region"],
            "aws_spend":    result["aws_spend"],
        },
    )
    return result
