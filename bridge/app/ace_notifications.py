"""
Real-time ACE pipeline notifications to the #ace-updates Teams channel.

These are live operational alerts — not the weekly hygiene report.
Each function posts a compact MessageCard with a colour-coded left border:

  GREEN  (#00B050) — good news:   opportunity created, funding eligible
  AMBER  (#FFC000) — watch:       stage change, inbound AO, close date risk
  RED    (#C00000) — act now:     mismatch, action required from AWS

All posts go to secret "teams/ace-webhook-url" with fallback to
"teams/webhook-url".

Public API:
  notify_created(opp_id, lead)
  notify_stage_change(opp_id, company, new_stage, old_stage, company)
  notify_hygiene(report)
  notify_funding_eligible(company, opp_id, program, amount, action)
  notify_stage_mismatch(company, opp_id, partner_stage, aws_stage, action)
  notify_action_required(company, opp_id, what_needed, deadline)
  notify_inbound_ao(company, aws_contact, action)
  notify_close_date_warning(company, opp_id, close_date, stage, days_left)
  notify_briefing_alerts(briefing_data)
"""

import logging
from datetime import datetime
from typing import Optional

from app import teams

logger = logging.getLogger("bridge")

# Colour constants
_GREEN = "00B050"
_AMBER = "FFC000"
_RED = "C00000"
_BLUE = "0078D4"


# ── Internal helper ───────────────────────────────────────────────────────────

async def _ace_post(card: dict) -> bool:
    """Post a card to ace-webhook-url, falling back to teams/webhook-url."""
    from app.config import get_secret, is_dummy
    key = get_secret("teams/ace-webhook-url")
    webhook_key = "teams/ace-webhook-url" if not is_dummy(key) else "teams/webhook-url"
    return await teams._post(card, webhook_key=webhook_key)


def _card(colour: str, title: str, summary: str, body: str) -> dict:
    """Build a compact single-section MessageCard."""
    return {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": colour,
        "summary": summary,
        "title": title,
        "sections": [{"text": body}],
    }


# ── 1. New opportunity created ────────────────────────────────────────────────

async def notify_created(opp_id: str, lead) -> bool:
    """Post a green card when a new ACE opportunity is successfully created.

    Args:
        opp_id: ACE opportunity ID (e.g. "O14608392")
        lead:   LeadPayload object with company, campaign, arr, contact fields
    """
    company = getattr(lead, "company", "Unknown")
    campaign = (getattr(lead, "campaign", "") or "").upper()
    contact = getattr(lead, "contact", "")
    arr = getattr(lead, "arr", None)
    arr_str = f"${arr:,}" if arr else "TBC"

    title = f"NEW ACE OPPORTUNITY | {company} | {opp_id}"
    body = (
        f"Stage: Prospect | Campaign: {campaign} | "
        f"ARR: {arr_str} | Contact: {contact}"
    )
    card = _card(_GREEN, title, f"New ACE opportunity: {company}", body)
    logger.info("ace_notify_created", extra={"opp_id": opp_id, "company": company})
    return await _ace_post(card)


# ── 2. Stage change ───────────────────────────────────────────────────────────

async def notify_stage_change(
    opp_id: str,
    company: str,
    new_stage: str,
    old_stage: str = "",
) -> bool:
    """Post an amber card when an ACE opportunity stage is updated."""
    from_str = f"{old_stage} → " if old_stage else ""
    title = f"STAGE UPDATE | {company} | {opp_id}"
    body = f"Stage: {from_str}{new_stage}"
    card = _card(_AMBER, title, f"Stage change: {company}", body)
    logger.info(
        "ace_notify_stage_change",
        extra={"opp_id": opp_id, "company": company, "new_stage": new_stage},
    )
    return await _ace_post(card)


# ── 3. Weekly hygiene report ──────────────────────────────────────────────────

async def notify_hygiene(report: dict) -> bool:
    """Post the weekly ACE hygiene report to the ACE channel.

    Args:
        report: dict with keys action_required, stale_launched, funding_eligible
    """
    today = datetime.now().strftime("%d %b %Y")
    card = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": _BLUE,
        "summary": f"ACE Hygiene Report {today}",
        "title": f"ACE HYGIENE REPORT — {today}",
        "sections": [
            {
                "activityTitle": "ACTION REQUIRED",
                "activityText": report.get("action_required", "None"),
            },
            {
                "activityTitle": "STALE LAUNCHED (30+ days no update)",
                "activityText": report.get("stale_launched", "None"),
            },
            {
                "activityTitle": "FUNDING ELIGIBLE",
                "activityText": report.get("funding_eligible", "None"),
            },
        ],
    }
    return await _ace_post(card)


# ── 4. Funding eligible ───────────────────────────────────────────────────────

async def notify_funding_eligible(
    company: str,
    opp_id: str,
    program: str,
    amount: str = "",
    action: str = "",
) -> bool:
    """Post a green card when a deal is eligible for AWS funding."""
    amount_str = f" | Estimated: {amount}" if amount else ""
    action_str = f" | Action: {action}" if action else ""
    title = f"FUNDING ELIGIBLE | {company} | {opp_id}"
    body = f"Program: {program}{amount_str}{action_str}"
    card = _card(_GREEN, title, f"Funding eligible: {company}", body)
    logger.info(
        "ace_notify_funding",
        extra={"opp_id": opp_id, "company": company, "program": program},
    )
    return await _ace_post(card)


# ── 5. AWS stage mismatch ─────────────────────────────────────────────────────

async def notify_stage_mismatch(
    company: str,
    opp_id: str,
    partner_stage: str,
    aws_stage: str,
    action: str = "",
) -> bool:
    """Post a red card when partner-reported stage does not match AWS stage."""
    action_str = f" | Action: {action}" if action else ""
    title = f"MISMATCH | {company} | {opp_id}"
    body = (
        f"Partner says: {partner_stage} | AWS says: {aws_stage}{action_str}"
    )
    card = _card(_RED, title, f"Stage mismatch: {company}", body)
    logger.warning(
        "ace_notify_mismatch",
        extra={"opp_id": opp_id, "company": company, "aws_stage": aws_stage},
    )
    return await _ace_post(card)


# ── 6. Action required from AWS ───────────────────────────────────────────────

async def notify_action_required(
    company: str,
    opp_id: str,
    what_needed: str,
    deadline: str = "",
) -> bool:
    """Post a red card when AWS requires action on an opportunity."""
    deadline_str = f" | Deadline: {deadline}" if deadline else ""
    title = f"ACTION REQUIRED | {company} | {opp_id}"
    body = f"AWS needs: {what_needed}{deadline_str}"
    card = _card(_RED, title, f"Action required: {company}", body)
    logger.warning(
        "ace_notify_action_required",
        extra={"opp_id": opp_id, "company": company},
    )
    return await _ace_post(card)


# ── 7. Inbound AWS Originated lead ────────────────────────────────────────────

async def notify_inbound_ao(
    company: str,
    aws_contact: str,
    action: str = "Review and accept/reject in Partner Central",
) -> bool:
    """Post an amber card when an AWS Originated invitation is detected."""
    title = f"INBOUND FROM AWS | {company}"
    body = f"AWS Contact: {aws_contact} | Action: {action}"
    card = _card(_AMBER, title, f"Inbound AO: {company}", body)
    logger.info("ace_notify_inbound_ao", extra={"company": company})
    return await _ace_post(card)


# ── 8. Close date warning ─────────────────────────────────────────────────────

async def notify_close_date_warning(
    company: str,
    opp_id: str,
    close_date: str,
    stage: str,
    days_left: int,
) -> bool:
    """Post a red (<7 days) or amber (7-14 days) card for close date risk."""
    colour = _RED if days_left <= 7 else _AMBER
    title = f"CLOSE DATE RISK | {company} | {opp_id}"
    body = (
        f"Closes: {close_date} | Current stage: {stage} | "
        f"Days left: {days_left}"
    )
    card = _card(colour, title, f"Close date risk: {company}", body)
    logger.warning(
        "ace_notify_close_risk",
        extra={"opp_id": opp_id, "company": company, "days_left": days_left},
    )
    return await _ace_post(card)


# ── CEO briefing → ACE channel summary ───────────────────────────────────────

async def notify_briefing_alerts(briefing_data: dict) -> bool:
    """Post action_required and aws_stage summary from CEO briefing to ACE channel.

    Called after POST /ceo/briefing completes. Posts two summary cards if the
    sections contain non-trivial content (not just "No data returned." or failure).
    """
    posted = False
    today = briefing_data.get("date", datetime.now().strftime("%d %b %Y"))

    action_text = briefing_data.get("action_required", "")
    if action_text and "Query failed" not in action_text and "No data" not in action_text:
        card = _card(
            _RED,
            f"ACTION REQUIRED — {today}",
            f"ACE actions required {today}",
            action_text[:800],
        )
        await _ace_post(card)
        posted = True

    aws_text = briefing_data.get("aws_stage", "")
    if aws_text and "Query failed" not in aws_text and "No data" not in aws_text:
        card = _card(
            _AMBER,
            f"AWS STAGE TRUTH — {today}",
            f"AWS stage alignment {today}",
            aws_text[:800],
        )
        await _ace_post(card)
        posted = True

    return posted
