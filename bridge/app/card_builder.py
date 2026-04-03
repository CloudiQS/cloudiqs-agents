"""
ZoomInfo-grade lead intelligence card builder.

Builds Adaptive Card v1.4 Power Automate webhook payloads.
Cards present every piece of research the SDR gathered so a salesperson
can pick up the phone and have an intelligent conversation immediately.

Layout follows the gold standard spec (docs/LEAD-GOLD-STANDARD-COMPLETE.md).
Inline bold labels for company data; section headers for grouped content.

Public API:
  build_lead_card(lead_data)  -> dict  (Power Automate message envelope)
"""

import logging

logger = logging.getLogger("bridge")


# ── ICP score → text colour ───────────────────────────────────────────────────

def _icp_style(icp: int) -> str:
    """Return Adaptive Card color name based on ICP score."""
    if icp >= 8:
        return "good"       # green
    if icp >= 5:
        return "warning"    # amber
    return "attention"      # red


# ── Primitive builders ────────────────────────────────────────────────────────

def _heading(text: str) -> dict:
    """Accent-coloured bold section header on its own line."""
    return {
        "type": "TextBlock",
        "text": text,
        "weight": "bolder",
        "size": "small",
        "color": "accent",
        "spacing": "medium",
        "wrap": False,
    }


def _tb(text: str, **kw) -> dict:
    """Plain TextBlock — wraps by default."""
    return {"type": "TextBlock", "text": text, "wrap": True, **kw}


def _sep() -> dict:
    """Thin horizontal separator."""
    return {"type": "TextBlock", "text": " ", "separator": True, "spacing": "small"}


def _action(title: str, url: str) -> dict:
    return {"type": "Action.OpenUrl", "title": title, "url": url}


def _wrap_card(body: list, actions: list | None = None) -> dict:
    """Wrap body in the Power Automate message envelope."""
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


# ── Lead card ─────────────────────────────────────────────────────────────────

def build_lead_card(lead_data: dict) -> dict:
    """Build a ZoomInfo-grade lead intelligence card.

    Layout per docs/LEAD-GOLD-STANDARD-COMPLETE.md section 2:
    - COMPANY: inline bold labels (Company: **Name**, Website: [link], etc.)
    - TECH STACK: separator + heading + body text
    - PEOPLE: separator + heading + contact blocks (all found contacts)
    - AWS INTELLIGENCE: separator + heading + inline label:value lines
    - WHY NOW / PAIN / OUR PLAY / TALK TRACK: one separator + grouped headings
    - NEWS: separator + heading + bullet items
    - CRM footer + action buttons

    Empty fields are always omitted. No placeholder text.
    """
    # ── Extract all fields ─────────────────────────────────────────────────
    company     = lead_data.get("company", "Unknown")
    campaign    = (lead_data.get("campaign") or "").upper()
    icp         = int(lead_data.get("icp_score") or 0)

    # Contact
    contact     = lead_data.get("contact", "") or ""
    job_title   = lead_data.get("job_title", "") or ""
    email_addr  = lead_data.get("email", "") or ""
    phone       = lead_data.get("phone", "") or ""
    # Support both "linkedin" (new) and "linkedin_url" (legacy field name)
    linkedin    = lead_data.get("linkedin", "") or lead_data.get("linkedin_url", "") or ""
    dm_bg       = lead_data.get("decision_maker_background", "") or ""
    li_activity = lead_data.get("linkedin_activity", "") or ""

    # Company
    website      = lead_data.get("website", "") or ""
    company_phone = lead_data.get("company_phone", "") or ""
    general_phone = lead_data.get("general_phone", "") or ""
    employees    = lead_data.get("employees")
    location     = lead_data.get("location", "") or ""
    ch_number    = lead_data.get("companies_house_number", "") or ""
    # Support both "sic_codes" (new) and "sic_code" (legacy)
    sic_codes    = lead_data.get("sic_codes", "") or lead_data.get("sic_code", "") or ""
    revenue      = lead_data.get("revenue", "") or ""
    founded      = lead_data.get("founded_year")
    description  = lead_data.get("company_description", "") or ""
    tech_stack   = lead_data.get("tech_stack", "") or ""

    # Intelligence
    signal      = lead_data.get("signal", "") or ""
    pain        = lead_data.get("pain", "") or ""
    play        = lead_data.get("play", "") or ""
    talk_track  = lead_data.get("talk_track", "") or ""
    recent_news = lead_data.get("recent_news") or []
    other_cons  = lead_data.get("other_contacts") or []

    # AWS Intelligence
    aws_customer      = lead_data.get("aws_customer")       # bool or None
    aws_services      = lead_data.get("aws_services", "") or ""
    aws_region        = lead_data.get("aws_region", "") or ""
    aws_spend         = lead_data.get("aws_spend", "") or ""
    aws_account_owner = lead_data.get("aws_account_owner", "") or ""
    # Support both "ace_opportunities" (new) and "aws_existing_opps" (legacy)
    ace_opps = (
        lead_data.get("ace_opportunities", "")
        or lead_data.get("aws_existing_opps", "")
        or ""
    )

    # CRM
    hubspot_deal = lead_data.get("hubspot_deal_id", "") or ""
    hubspot_con  = lead_data.get("hubspot_contact_id", "") or ""
    instantly_id = lead_data.get("instantly_lead_id", "") or ""
    deal_name    = lead_data.get("deal_name", "") or ""

    body: list[dict] = []
    actions: list[dict] = []

    # ── HEADER ────────────────────────────────────────────────────────────
    icp_color = _icp_style(icp)
    body.append(_tb(
        f"● NEW LEAD  |  ICP {icp}/10  |  {campaign}",
        weight="bolder", size="large", color=icp_color,
    ))

    # ── COMPANY ───────────────────────────────────────────────────────────
    body.append(_tb(f"Company: **{company}**", spacing="medium"))

    if description:
        body.append(_tb(f"Description: {description}", spacing="none"))

    if website:
        url    = website if website.startswith("http") else f"https://{website}"
        domain = url.replace("https://", "").replace("http://", "").rstrip("/")
        body.append(_tb(f"Website: [{domain}]({url})", spacing="none"))

    meta_parts: list[str] = []
    if employees:
        meta_parts.append(f"Employees: {employees}")
    if location and location not in ("GB", ""):
        meta_parts.append(f"Location: {location}")
    if founded:
        meta_parts.append(f"Est. {founded}")
    if meta_parts:
        body.append(_tb(" | ".join(meta_parts), spacing="none"))

    if ch_number:
        ch_url = (
            f"https://find-and-update.company-information.service.gov.uk"
            f"/company/{ch_number}"
        )
        body.append(_tb(f"Companies House: [{ch_number}]({ch_url})", spacing="none"))

    if sic_codes:
        body.append(_tb(f"SIC: {sic_codes}", spacing="none"))

    if revenue:
        body.append(_tb(f"Revenue: {revenue}", spacing="none"))

    if company_phone:
        body.append(_tb(f"📞 Switchboard: {company_phone}", spacing="none"))

    if general_phone:
        body.append(_tb(f"📞 General: {general_phone}", spacing="none"))

    # ── TECH STACK ────────────────────────────────────────────────────────
    if tech_stack:
        body.append(_sep())
        body.append(_heading("TECH STACK"))
        body.append(_tb(tech_stack, spacing="small"))

    # ── PEOPLE ────────────────────────────────────────────────────────────
    body.append(_sep())
    body.append(_heading("PEOPLE"))

    def _contact_block(
        name: str,
        title: str,
        email: str,
        phone_num: str,
        li_url: str,
        background: str,
        activity: str = "",
    ) -> None:
        if not name:
            return
        label = f"**{name}**" + (f"  |  {title}" if title else "")
        body.append(_tb(label, spacing="small"))
        if email:
            body.append(_tb(f"Email: [{email}](mailto:{email})", spacing="none"))
        if phone_num:
            body.append(_tb(f"Direct: 📞 {phone_num}", spacing="none"))
        if li_url:
            body.append(_tb(f"LinkedIn: [Profile]({li_url})", spacing="none"))
        if activity:
            body.append(_tb(f"Recent: {activity}", spacing="none", isSubtle=True))
        if background:
            body.append(_tb(f"Background: {background}", spacing="none", isSubtle=True))

    _contact_block(contact, job_title, email_addr, phone, linkedin, dm_bg, li_activity)

    for oc in other_cons:
        if isinstance(oc, dict) and oc.get("name"):
            _contact_block(
                oc.get("name", ""),
                oc.get("title", ""),
                oc.get("email") or "",
                oc.get("phone") or "",
                oc.get("linkedin") or "",
                oc.get("background") or "",
            )

    # ── AWS INTELLIGENCE ──────────────────────────────────────────────────
    has_aws = any([
        ace_opps,
        aws_customer is not None,
        aws_services,
        aws_spend,
        aws_account_owner,
    ])
    if has_aws:
        body.append(_sep())
        body.append(_heading("AWS INTELLIGENCE"))

        if ace_opps:
            body.append(_tb(f"ACE: {ace_opps}", spacing="small"))

        if aws_customer is True:
            body.append(_tb("AWS Customer: Yes", spacing="none"))
        elif aws_customer is False:
            body.append(_tb(
                "AWS Customer: No profile found — potential net new customer",
                spacing="none",
            ))

        services_line = aws_services
        if services_line and aws_region:
            services_line = f"{services_line} ({aws_region})"
        if services_line:
            body.append(_tb(f"Services: {services_line}", spacing="none"))

        if aws_spend:
            body.append(_tb(f"Spend: {aws_spend}", spacing="none"))

        if aws_account_owner:
            body.append(_tb(f"Account Owner: {aws_account_owner}", spacing="none"))

    # ── WHY NOW + PAIN + OUR PLAY + TALK TRACK ────────────────────────────
    if any([signal, pain, play, talk_track]):
        body.append(_sep())

        if signal:
            body.append(_heading("WHY NOW"))
            body.append(_tb(signal, spacing="small"))

        if pain:
            body.append(_heading("PAIN"))
            body.append(_tb(pain, spacing="small"))

        if play:
            body.append(_heading("OUR PLAY"))
            body.append(_tb(play, spacing="small"))

        if talk_track:
            body.append(_heading("TALK TRACK"))
            body.append(_tb(f'"{talk_track}"', spacing="small", isSubtle=True))

    # ── NEWS ──────────────────────────────────────────────────────────────
    if recent_news:
        body.append(_sep())
        body.append(_heading("NEWS"))
        for item in recent_news[:5]:
            if item:
                body.append(_tb(f"• {item}", spacing="none"))

    # ── CRM FOOTER ────────────────────────────────────────────────────────
    body.append(_sep())
    if deal_name:
        body.append(_tb(deal_name, isSubtle=True, size="small", spacing="small"))

    crm_parts: list[str] = []
    if hubspot_con:
        crm_parts.append(f"HubSpot: {hubspot_con}")
    if hubspot_deal:
        crm_parts.append(f"Deal: {hubspot_deal}")
    crm_parts.append(f"Instantly: {'enrolled' if instantly_id else 'skipped'}")
    body.append(_tb(
        " | ".join(crm_parts),
        isSubtle=True,
        size="small",
        spacing="none" if deal_name else "small",
    ))

    # ── ACTION BUTTONS ────────────────────────────────────────────────────
    first_name = contact.split()[0] if contact else "Contact"

    if phone:
        tel_uri = "tel:" + phone.replace(" ", "")
        actions.append(_action(f"📞 Call {first_name}", tel_uri))

    if company_phone:
        tel_uri = "tel:" + company_phone.replace(" ", "")
        actions.append(_action("📞 Switchboard", tel_uri))

    if hubspot_deal:
        actions.append(_action(
            "Open HubSpot",
            f"https://app.hubspot.com/contacts/0/deal/{hubspot_deal}",
        ))
    elif hubspot_con:
        actions.append(_action(
            "Open HubSpot",
            f"https://app.hubspot.com/contacts/0/contact/{hubspot_con}",
        ))

    if linkedin:
        actions.append(_action("LinkedIn", linkedin))

    if website:
        url = website if website.startswith("http") else f"https://{website}"
        actions.append(_action("Website", url))

    return _wrap_card(body, actions if actions else None)
