"""
Real-time ACE pipeline notifications to the #ace-updates Teams channel.

All posts use teams.post_to_ace(title, body_text, facts).

Public API:
  notify_created(opp_id, lead)
  notify_stage_change(opp_id, company, new_stage, old_stage)
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


async def notify_created(opp_id: str, lead) -> bool:
    company  = getattr(lead, "company", "Unknown")
    campaign = (getattr(lead, "campaign", "") or "").upper()
    contact  = getattr(lead, "contact", "")
    arr      = getattr(lead, "arr", None)
    arr_str  = f"${arr:,}" if arr else "TBC"
    title = f"NEW ACE OPPORTUNITY | {company} | {opp_id}"
    body  = f"Stage: Prospect | Campaign: {campaign} | ARR: {arr_str} | Contact: {contact}"
    logger.info("ace_notify_created", extra={"opp_id": opp_id, "company": company})
    return await teams.post_to_ace(title, body)


async def notify_stage_change(
    opp_id: str,
    company: str,
    new_stage: str,
    old_stage: str = "",
) -> bool:
    from_str = f"{old_stage} → " if old_stage else ""
    title = f"STAGE UPDATE | {company} | {opp_id}"
    body  = f"Stage: {from_str}{new_stage}"
    logger.info("ace_notify_stage_change", extra={"opp_id": opp_id, "new_stage": new_stage})
    return await teams.post_to_ace(title, body)


async def notify_hygiene(report: dict) -> bool:
    today = datetime.now().strftime("%d %b %Y")
    title = f"ACE HYGIENE REPORT — {today}"
    parts = []
    for section, label in [
        ("action_required", "ACTION REQUIRED"),
        ("stale_launched",  "STALE LAUNCHED (30+ days)"),
        ("funding_eligible","FUNDING ELIGIBLE"),
    ]:
        val = report.get(section, "None")
        parts.append(f"**{label}**\n{val}")
    body = "\n\n".join(parts)
    return await teams.post_to_ace(title, body)


async def notify_funding_eligible(
    company: str,
    opp_id: str,
    program: str,
    amount: str = "",
    action: str = "",
) -> bool:
    title = f"FUNDING ELIGIBLE | {company} | {opp_id}"
    parts = [f"Program: {program}"]
    if amount:
        parts.append(f"Estimated: {amount}")
    if action:
        parts.append(f"Action: {action}")
    body = " | ".join(parts)
    logger.info("ace_notify_funding", extra={"opp_id": opp_id, "program": program})
    return await teams.post_to_ace(title, body)


async def notify_stage_mismatch(
    company: str,
    opp_id: str,
    partner_stage: str,
    aws_stage: str,
    action: str = "",
) -> bool:
    title = f"MISMATCH | {company} | {opp_id}"
    body  = f"Partner says: {partner_stage} | AWS says: {aws_stage}"
    if action:
        body += f" | Action: {action}"
    logger.warning("ace_notify_mismatch", extra={"opp_id": opp_id, "aws_stage": aws_stage})
    return await teams.post_to_ace(title, body)


async def notify_action_required(
    company: str,
    opp_id: str,
    what_needed: str,
    deadline: str = "",
) -> bool:
    title = f"ACTION REQUIRED | {company} | {opp_id}"
    body  = f"AWS needs: {what_needed}"
    if deadline:
        body += f" | Deadline: {deadline}"
    logger.warning("ace_notify_action_required", extra={"opp_id": opp_id, "company": company})
    return await teams.post_to_ace(title, body)


async def notify_inbound_ao(
    company: str,
    aws_contact: str,
    action: str = "Review and accept/reject in Partner Central",
) -> bool:
    title = f"INBOUND FROM AWS | {company}"
    body  = f"AWS Contact: {aws_contact} | Action: {action}"
    logger.info("ace_notify_inbound_ao", extra={"company": company})
    return await teams.post_to_ace(title, body)


async def notify_close_date_warning(
    company: str,
    opp_id: str,
    close_date: str,
    stage: str,
    days_left: int,
) -> bool:
    title = f"CLOSE DATE RISK | {company} | {opp_id}"
    body  = f"Closes: {close_date} | Current stage: {stage} | Days left: {days_left}"
    logger.warning("ace_notify_close_risk", extra={"opp_id": opp_id, "days_left": days_left})
    return await teams.post_to_ace(title, body)


async def notify_briefing_alerts(briefing_data: dict) -> bool:
    """Post action_required and aws_stage from CEO briefing to ACE channel."""
    posted = False
    today  = briefing_data.get("date", datetime.now().strftime("%d %b %Y"))

    action_text = briefing_data.get("action_required", "")
    if action_text and "Query failed" not in action_text and "No data" not in action_text:
        await teams.post_to_ace(f"ACTION REQUIRED — {today}", action_text[:800])
        posted = True

    aws_text = briefing_data.get("aws_stage", "")
    if aws_text and "Query failed" not in aws_text and "No data" not in aws_text:
        await teams.post_to_ace(f"AWS STAGE TRUTH — {today}", aws_text[:800])
        posted = True

    return posted
