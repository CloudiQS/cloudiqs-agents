"""
ACE pipeline card system — 6 Adaptive Card v1.4 types for the ACE channel.

All cards:
  - Power Automate webhook envelope (type: message, attachments)
  - "msteams": {"width": "Full"} on every card
  - Parsed data — no raw MCP dumps, no pipe tables
  - Under 20 KB
  - Action buttons where appropriate
  - Readable TextBlock headers (no coloured Container backgrounds)

Public API (all return dict — Power Automate message envelope):
  build_daily_scorecard(data)       — pipeline metrics + next steps
  build_action_required_card(data)  — specific issues + SLA countdown
  build_new_referral_card(data)     — incoming AWS referral
  build_stage_advice_card(data)     — how to advance an opportunity
  build_stage_change_card(data)     — stage movement notification
  build_stale_deals_card(data)      — weekly stale deals digest

Each function documents its expected data keys.
"""

import logging
from typing import Optional

logger = logging.getLogger("bridge")


# ── Primitives (shared with card_builder.py pattern) ─────────────────────────

def _tb(text: str, **kw) -> dict:
    return {"type": "TextBlock", "text": text, "wrap": True, **kw}


def _heading(text: str) -> dict:
    return {
        "type": "TextBlock",
        "text": text,
        "weight": "bolder",
        "size": "small",
        "color": "accent",
        "spacing": "medium",
        "wrap": False,
    }


def _sep() -> dict:
    return {"type": "TextBlock", "text": " ", "separator": True, "spacing": "small"}


def _factset(facts: list) -> dict:
    return {"type": "FactSet", "facts": facts[:10], "spacing": "small"}


def _action(title: str, url: str) -> dict:
    return {"type": "Action.OpenUrl", "title": title, "url": url}


def _wrap_card(body: list, actions: Optional[list] = None) -> dict:
    card: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": card,
        }],
    }


def _header_tb(text: str, color: str = "accent") -> dict:
    """Bold large header TextBlock — no Container, just coloured text."""
    return _tb(text, weight="bolder", size="large", color=color)


# ── 1. Daily scorecard ────────────────────────────────────────────────────────

def build_daily_scorecard(data: dict) -> dict:
    """Daily ACE pipeline scorecard.

    data keys:
        date (str)               — display date, e.g. "3 Apr 2026"
        total_opps (int)         — total open opportunities
        by_stage (dict)          — {stage_name: count}
        action_required (int)    — count of opps needing action
        funding_eligible (int)   — count eligible for MAP/POC/CEI
        cosell_active (int)      — count with active co-sell reps
        next_steps (list[str])   — prioritised actions (max 5)
        health_score (int)       — 0-10
        subtitle (str)           — optional subtitle
    """
    date          = data.get("date", "")
    total         = data.get("total_opps", 0)
    by_stage      = data.get("by_stage") or {}
    action_req    = data.get("action_required", 0)
    funding_elig  = data.get("funding_eligible", 0)
    cosell        = data.get("cosell_active", 0)
    next_steps    = (data.get("next_steps") or [])[:5]
    health        = int(data.get("health_score") or 0)
    subtitle      = data.get("subtitle", "")

    color = "good" if health >= 7 else ("warning" if health >= 4 else "attention")
    title = f"ACE DAILY SCORECARD — {date}" if date else "ACE DAILY SCORECARD"

    body: list[dict] = []
    body.append(_header_tb(f"● {title}", color=color))

    if subtitle:
        body.append(_tb(subtitle, isSubtle=True, spacing="none"))

    # Pipeline facts
    facts = [{"title": "Open Opportunities", "value": str(total)}]
    if action_req:
        facts.append({"title": "Action Required", "value": str(action_req)})
    if funding_elig:
        facts.append({"title": "Funding Eligible", "value": str(funding_elig)})
    if cosell:
        facts.append({"title": "Co-Sell Active", "value": str(cosell)})
    facts.append({"title": "Health Score", "value": f"{health}/10"})

    body.append(_sep())
    body.append(_heading("PIPELINE"))
    body.append(_factset(facts))

    # Stage breakdown
    if by_stage:
        body.append(_sep())
        body.append(_heading("BY STAGE"))
        stage_facts = [
            {"title": stage, "value": str(count)}
            for stage, count in list(by_stage.items())[:8]
        ]
        body.append(_factset(stage_facts))

    # Next steps
    if next_steps:
        body.append(_sep())
        body.append(_heading("TODAY'S FOCUS"))
        for i, step in enumerate(next_steps, 1):
            body.append(_tb(f"{i}. {step}", spacing="none" if i > 1 else "small"))

    return _wrap_card(body)


# ── 2. Action required ────────────────────────────────────────────────────────

def build_action_required_card(data: dict) -> dict:
    """ACE action required notification — specific issues with SLA countdown.

    data keys:
        company (str)            — company name
        opp_id (str)             — ACE opportunity ID
        stage (str)              — current stage
        issues (list[str])       — list of specific issues to fix
        sla_deadline (str)       — date/time by which action needed
        days_remaining (int)     — days until SLA breach (optional)
        action (str)             — recommended next action
        aws_rep (str)            — AWS rep name (optional)
        hubspot_deal_id (str)    — HubSpot deal ID for action button
    """
    company      = data.get("company", "Unknown")
    opp_id       = data.get("opp_id", "")
    stage        = data.get("stage", "")
    issues       = (data.get("issues") or [])[:8]
    sla_deadline = data.get("sla_deadline", "")
    days_left    = data.get("days_remaining")
    action       = data.get("action", "")
    aws_rep      = data.get("aws_rep", "")
    hs_deal      = data.get("hubspot_deal_id", "")

    sla_color = "attention"
    if days_left is not None:
        sla_color = "good" if days_left > 7 else ("warning" if days_left > 2 else "attention")

    opp_ref = f"{company}  |  {opp_id}" if opp_id else company
    body: list[dict] = []
    body.append(_header_tb(f"● ACE ACTION REQUIRED  |  {opp_ref}", color=sla_color))

    facts = []
    if stage:
        facts.append({"title": "Stage", "value": stage})
    if sla_deadline:
        label = f"SLA ({days_left}d remaining)" if days_left is not None else "SLA Deadline"
        facts.append({"title": label, "value": sla_deadline})
    if aws_rep:
        facts.append({"title": "AWS Rep", "value": aws_rep})

    if facts:
        body.append(_sep())
        body.append(_factset(facts))

    if issues:
        body.append(_sep())
        body.append(_heading("ISSUES TO FIX"))
        for issue in issues:
            body.append(_tb(f"• {issue}", spacing="none"))

    if action:
        body.append(_sep())
        body.append(_tb(f"**ACTION:** {action}", color="attention", spacing="small"))

    actions: list[dict] = []
    if hs_deal:
        actions.append(_action("Open HubSpot", f"https://app.hubspot.com/contacts/0/deal/{hs_deal}"))
    if opp_id:
        actions.append(_action("Open ACE", f"https://partnercentral.awspartner.com/opportunity/{opp_id}"))

    return _wrap_card(body, actions if actions else None)


# ── 3. New AWS referral ───────────────────────────────────────────────────────

def build_new_referral_card(data: dict) -> dict:
    """New inbound AWS referral (AO) notification.

    data keys:
        company (str)            — company name
        opp_id (str)             — ACE opportunity ID
        contact (str)            — primary contact name
        contact_title (str)      — contact job title
        contact_email (str)      — contact email
        contact_phone (str)      — contact phone
        aws_rep (str)            — referring AWS rep
        description (str)        — opportunity description
        estimated_arr (str)      — estimated ARR
        close_date (str)         — target close date
        action (str)             — recommended action
        hubspot_deal_id (str)    — HubSpot deal ID
        linkedin (str)           — contact LinkedIn URL
    """
    company    = data.get("company", "Unknown")
    opp_id     = data.get("opp_id", "")
    contact    = data.get("contact", "")
    title      = data.get("contact_title", "")
    email      = data.get("contact_email", "")
    phone      = data.get("contact_phone", "")
    aws_rep    = data.get("aws_rep", "")
    desc       = data.get("description", "")
    arr        = data.get("estimated_arr", "")
    close_date = data.get("close_date", "")
    action     = data.get("action", "")
    hs_deal    = data.get("hubspot_deal_id", "")
    linkedin   = data.get("linkedin", "")

    body: list[dict] = []
    opp_ref = f"{company}  |  {opp_id}" if opp_id else company
    body.append(_header_tb(f"● NEW AWS REFERRAL  |  {opp_ref}", color="good"))

    if aws_rep:
        body.append(_tb(f"Referred by: {aws_rep}", isSubtle=True, spacing="none"))

    # Company facts
    facts = []
    if arr:
        facts.append({"title": "Estimated ARR", "value": arr})
    if close_date:
        facts.append({"title": "Target Close", "value": close_date})

    if facts:
        body.append(_sep())
        body.append(_factset(facts))

    if desc:
        body.append(_sep())
        body.append(_heading("OPPORTUNITY"))
        body.append(_tb(desc))

    # Contact block
    if contact:
        body.append(_sep())
        body.append(_heading("CONTACT"))
        label = f"**{contact}**" + (f"  |  {title}" if title else "")
        body.append(_tb(label, spacing="small"))
        if email:
            body.append(_tb(f"Email: [{email}](mailto:{email})", spacing="none"))
        if phone:
            body.append(_tb(f"Direct: 📞 {phone}", spacing="none"))
        if linkedin:
            body.append(_tb(f"LinkedIn: [Profile]({linkedin})", spacing="none"))

    if action:
        body.append(_sep())
        body.append(_tb(f"**ACTION:** {action}", color="accent", spacing="small"))

    actions: list[dict] = []
    if phone:
        actions.append(_action(f"📞 Call {contact.split()[0] if contact else 'Contact'}",
                               "tel:" + phone.replace(" ", "")))
    if hs_deal:
        actions.append(_action("Open HubSpot", f"https://app.hubspot.com/contacts/0/deal/{hs_deal}"))
    if opp_id:
        actions.append(_action("Open ACE", f"https://partnercentral.awspartner.com/opportunity/{opp_id}"))
    if linkedin:
        actions.append(_action("LinkedIn", linkedin))

    return _wrap_card(body, actions if actions else None)


# ── 4. Stage progression advice ───────────────────────────────────────────────

def build_stage_advice_card(data: dict) -> dict:
    """ACE stage progression advice — what to do to advance an opportunity.

    data keys:
        company (str)            — company name
        opp_id (str)             — ACE opportunity ID
        current_stage (str)      — current ACE stage
        next_stage (str)         — target next stage
        required_fields (list)   — fields that must be completed
        missing_fields (list)    — fields currently empty/invalid
        tips (list[str])         — practical tips for advancement
        aws_rep (str)            — AWS rep (optional)
        hubspot_deal_id (str)    — HubSpot deal ID
    """
    company        = data.get("company", "Unknown")
    opp_id         = data.get("opp_id", "")
    current_stage  = data.get("current_stage", "")
    next_stage     = data.get("next_stage", "")
    required       = (data.get("required_fields") or [])[:10]
    missing        = (data.get("missing_fields") or [])[:10]
    tips           = (data.get("tips") or [])[:6]
    aws_rep        = data.get("aws_rep", "")
    hs_deal        = data.get("hubspot_deal_id", "")

    opp_ref = f"{company}  |  {opp_id}" if opp_id else company
    body: list[dict] = []
    body.append(_header_tb(f"● STAGE ADVICE  |  {opp_ref}", color="accent"))

    facts = []
    if current_stage:
        facts.append({"title": "Current Stage", "value": current_stage})
    if next_stage:
        facts.append({"title": "Target Stage", "value": next_stage})
    if aws_rep:
        facts.append({"title": "AWS Rep", "value": aws_rep})
    if facts:
        body.append(_sep())
        body.append(_factset(facts))

    if missing:
        body.append(_sep())
        body.append(_heading("MISSING FIELDS"))
        for f in missing:
            body.append(_tb(f"• {f}", spacing="none", color="attention"))

    if required and not missing:
        body.append(_sep())
        body.append(_heading("REQUIRED FIELDS"))
        for f in required:
            body.append(_tb(f"• {f}", spacing="none"))

    if tips:
        body.append(_sep())
        body.append(_heading("HOW TO ADVANCE"))
        for i, tip in enumerate(tips, 1):
            body.append(_tb(f"{i}. {tip}", spacing="none" if i > 1 else "small"))

    actions: list[dict] = []
    if hs_deal:
        actions.append(_action("Open HubSpot", f"https://app.hubspot.com/contacts/0/deal/{hs_deal}"))
    if opp_id:
        actions.append(_action("Open ACE", f"https://partnercentral.awspartner.com/opportunity/{opp_id}"))

    return _wrap_card(body, actions if actions else None)


# ── 5. Stage change notification ──────────────────────────────────────────────

def build_stage_change_card(data: dict) -> dict:
    """ACE stage change notification.

    data keys:
        company (str)            — company name
        opp_id (str)             — ACE opportunity ID
        old_stage (str)          — previous stage
        new_stage (str)          — new stage
        direction (str)          — "forward" | "backward" | "unchanged"
        unlocked (list[str])     — what this stage change now enables
        action (str)             — recommended next action
        aws_rep (str)            — AWS rep (optional)
        hubspot_deal_id (str)    — HubSpot deal ID
        arr (str)                — estimated ARR (optional)
    """
    company    = data.get("company", "Unknown")
    opp_id     = data.get("opp_id", "")
    old_stage  = data.get("old_stage", "")
    new_stage  = data.get("new_stage", "")
    direction  = data.get("direction", "forward")
    unlocked   = (data.get("unlocked") or [])[:5]
    action     = data.get("action", "")
    aws_rep    = data.get("aws_rep", "")
    hs_deal    = data.get("hubspot_deal_id", "")
    arr        = data.get("arr", "")

    color = "good" if direction == "forward" else (
        "attention" if direction == "backward" else "warning"
    )
    stage_str = f"{old_stage} → {new_stage}" if old_stage else new_stage
    opp_ref = f"{company}  |  {opp_id}" if opp_id else company

    body: list[dict] = []
    body.append(_header_tb(f"● STAGE CHANGE  |  {opp_ref}", color=color))
    body.append(_tb(stage_str, weight="bolder", spacing="none"))

    facts = []
    if aws_rep:
        facts.append({"title": "AWS Rep", "value": aws_rep})
    if arr:
        facts.append({"title": "Est. ARR", "value": arr})
    if facts:
        body.append(_sep())
        body.append(_factset(facts))

    if unlocked:
        body.append(_sep())
        body.append(_heading("NOW UNLOCKED"))
        for item in unlocked:
            body.append(_tb(f"✓ {item}", spacing="none"))

    if action:
        body.append(_sep())
        body.append(_tb(f"**NEXT:** {action}", color="accent", spacing="small"))

    actions: list[dict] = []
    if hs_deal:
        actions.append(_action("Open HubSpot", f"https://app.hubspot.com/contacts/0/deal/{hs_deal}"))
    if opp_id:
        actions.append(_action("Open ACE", f"https://partnercentral.awspartner.com/opportunity/{opp_id}"))

    return _wrap_card(body, actions if actions else None)


# ── 6. Weekly stale deals ─────────────────────────────────────────────────────

def build_stale_deals_card(data: dict) -> dict:
    """Weekly stale deals digest — top 5 + total count.

    data keys:
        week_ending (str)        — week ending date
        stale_total (int)        — total stale deal count
        stale_threshold_days (int) — days without update to be considered stale
        top_deals (list[dict])   — up to 5 deals, each with:
            company (str)
            opp_id (str)
            stage (str)
            last_update (str)    — date of last update
            days_since (int)     — days since last update
            recommended_action (str) — optional
        action (str)             — overall recommended action
    """
    week_ending  = data.get("week_ending", "")
    stale_total  = data.get("stale_total", 0)
    threshold    = data.get("stale_threshold_days", 30)
    top_deals    = (data.get("top_deals") or [])[:5]
    action       = data.get("action", "")

    color = "good" if stale_total == 0 else ("warning" if stale_total <= 3 else "attention")
    title_date = f" — {week_ending}" if week_ending else ""
    stale_label = f"{stale_total} deal{'s' if stale_total != 1 else ''} stale ({threshold}+ days)"

    body: list[dict] = []
    body.append(_header_tb(f"● STALE DEALS{title_date}  |  {stale_label}", color=color))

    if stale_total == 0:
        body.append(_tb("No stale deals this week. Pipeline is healthy.", spacing="small"))
        return _wrap_card(body)

    for i, deal in enumerate(top_deals):
        company   = deal.get("company", "Unknown")
        opp_id    = deal.get("opp_id", "")
        stage     = deal.get("stage", "")
        last_upd  = deal.get("last_update", "")
        days      = deal.get("days_since", 0)
        rec_action = deal.get("recommended_action", "")

        body.append(_sep())
        ref = f"**{company}**" + (f"  |  {opp_id}" if opp_id else "")
        body.append(_tb(ref, spacing="small" if i > 0 else "medium"))

        detail_parts = []
        if stage:
            detail_parts.append(f"Stage: {stage}")
        if days:
            detail_parts.append(f"{days} days since update")
        if last_upd:
            detail_parts.append(f"Last: {last_upd}")
        if detail_parts:
            body.append(_tb("  ·  ".join(detail_parts), spacing="none", isSubtle=True))

        if rec_action:
            body.append(_tb(f"→ {rec_action}", spacing="none", color="attention"))

    if action:
        body.append(_sep())
        body.append(_tb(f"**ACTION:** {action}", color="accent", spacing="small"))

    return _wrap_card(body)
