"""
AWS Intelligence endpoint — POST /ace/customer-lookup.

Runs two MCP queries per company:
  Query 1 — ACE pipeline check: do we have existing opportunities?
  Query 2 — AWS customer profile: services, spend, region, account owner.

Returns empty fields on MCP failure — never blocks the lead pipeline.
Agents call this during research (Step 4) and include the results in the
POST /lead payload as aws_customer, aws_services, aws_region, etc.

Reference: docs/LEAD-GOLD-STANDARD-COMPLETE.md section 7.
"""

import logging

from app import mcp_client
from app.config import get_secret, is_dummy

logger = logging.getLogger("bridge")

# Max chars to store from MCP free-text responses
_MAX_TEXT = 400


def _get_catalog() -> str:
    """Return configured catalog name, defaulting to Sandbox."""
    try:
        catalog = get_secret("partner-central/catalog")
        return catalog if not is_dummy(catalog) else "Sandbox"
    except Exception:
        return "Sandbox"


async def customer_lookup(company: str, website: str = "") -> dict:
    """Query Partner Central MCP for ACE opps and AWS customer profile.

    Args:
        company:  Company name (e.g. "UK Tote Group")
        website:  Company website domain (optional, for disambiguation)

    Returns:
        dict with keys:
          aws_customer (bool | None)
          aws_services (str)
          aws_region   (str)
          aws_spend    (str)
          aws_account_owner (str)
          aws_existing_opps (str)
          ace_opportunities (str)
    """
    result: dict = {
        "aws_customer":      None,
        "aws_services":      "",
        "aws_region":        "",
        "aws_spend":         "",
        "aws_account_owner": "",
        "aws_existing_opps": "",
        "ace_opportunities": "",
    }

    catalog = _get_catalog()
    company_ref = f"{company} ({website})" if website else company

    # ── Query 1: ACE pipeline check ───────────────────────────────────────
    try:
        ace_resp = await mcp_client.send_message(
            f"Do we have any existing ACE opportunities for {company_ref}? "
            "Show opportunity ID, stage, revenue, and last update date.",
            catalog=catalog,
        )
        if ace_resp and ace_resp.get("text"):
            text = ace_resp["text"].strip()[:_MAX_TEXT]
            result["ace_opportunities"] = text
            result["aws_existing_opps"] = text
            logger.info("ace_lookup_ok", extra={"company": company})
    except Exception as exc:
        logger.warning("ace_lookup_failed", extra={"company": company, "error": str(exc)})

    # ── Query 2: AWS customer profile ─────────────────────────────────────
    try:
        profile_resp = await mcp_client.send_message(
            f"Is {company_ref} an AWS customer? What services do they use, "
            "what is their monthly spend, who is their account owner, "
            "what region are they deployed in?",
            catalog=catalog,
        )
        if profile_resp and profile_resp.get("text"):
            text = profile_resp["text"].strip()
            # MCP returned data — company is in the AWS partner system
            result["aws_customer"] = True
            # Store the raw profile text as aws_services for now.
            # The research-agent can parse structured fields if needed.
            result["aws_services"] = text[:_MAX_TEXT]
            logger.info("aws_profile_ok", extra={"company": company})
        else:
            result["aws_customer"] = False
    except Exception as exc:
        logger.warning("aws_profile_failed", extra={"company": company, "error": str(exc)})

    return result
