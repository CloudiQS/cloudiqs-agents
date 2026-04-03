"""
ZoomInfo-grade lead intelligence card builder.

Builds Adaptive Card v1.4 Power Automate webhook payloads.
Cards present every piece of research the SDR gathered so a salesperson
can pick up the phone and have an intelligent conversation immediately.

Public API:
  build_lead_card(lead_data)  -> dict  (Power Automate message envelope)
"""

import logging

logger = logging.getLogger("bridge")

# ── ICP score → container style ───────────────────────────────────────────────

def _icp_style(icp: int) -> str:
    if icp >= 8:
        return "good"       # green
    if icp >= 5:
        return "warning"    # amber
    return "attention"      # red


# ── Primitive builders ────────────────────────────────────────────────────────

def _heading(text: str) -> dict:
    """Accent-coloured bold section header."""
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
            "content": card,
        }],
    }


# ── Lead card ─────────────────────────────────────────────────────────────────

def build_lead_card(lead_data: dict) -> dict:
    """Build a ZoomInfo-grade lead intelligence card.

    Shows every piece of research the SDR found: company profile, tech stack,
    multiple contacts, signal, pain, recent news, play, and talk track.
    All URLs, emails, and phone numbers are clickable.
    Sections are omitted if empty — no placeholder text.
    """
    # ── Extract all fields ─────────────────────────────────────────────────
    company     = lead_data.get("company", "Unknown")
    campaign    = (lead_data.get("campaign") or "").upper()
    icp         = int(lead_data.get("icp_score") or 0)

    # Contact
    contact     = lead_data.get("contact", "")
    job_title   = lead_data.get("job_title", "")
    email_addr  = lead_data.get("email", "")
    phone       = lead_data.get("phone", "") or ""
    comp_phone  = lead_data.get("company_phone", "") or ""
    linkedin    = lead_data.get("linkedin_url", "") or ""
    dm_bg       = lead_data.get("decision_maker_background", "") or ""
    li_activity = lead_data.get("linkedin_activity", "") or ""

    # Company
    website     = lead_data.get("website", "") or ""
    employees   = lead_data.get("employees")
    location    = lead_data.get("location", "") or ""
    ch_number   = lead_data.get("companies_house_number", "") or ""
    sic_code    = lead_data.get("sic_code", "") or ""
    revenue     = lead_data.get("revenue", "") or ""
    founded     = lead_data.get("founded_year")
    description = lead_data.get("company_description", "") or ""
    tech_stack  = lead_data.get("tech_stack", "") or ""

    # Intelligence
    signal      = lead_data.get("signal", "") or ""
    pain        = lead_data.get("pain", "") or ""
    play        = lead_data.get("play", "") or ""
    talk_track  = lead_data.get("talk_track", "") or ""
    recent_news = lead_data.get("recent_news") or []
    other_cons  = lead_data.get("other_contacts") or []

    # CRM
    hubspot_deal = lead_data.get("hubspot_deal_id", "") or ""
    hubspot_con  = lead_data.get("hubspot_contact_id", "") or ""
    instantly_id = lead_data.get("instantly_lead_id", "") or ""
    deal_name    = lead_data.get("deal_name", "") or ""

    body: list[dict] = []
    actions: list[dict] = []

    # ── Header — readable on any background ──────────────────────────────
    icp_dot_color = _icp_style(icp)   # "good" | "warning" | "attention"
    body.append({
        "type": "ColumnSet",
        "columns": [
            {
                "type": "Column",
                "width": "auto",
                "items": [{"type": "TextBlock", "text": "●", "color": icp_dot_color, "size": "large", "weight": "bolder"}],
            },
            {
                "type": "Column",
                "width": "stretch",
                "items": [_tb(
                    f"NEW LEAD  |  ICP {icp}/10  |  {campaign}",
                    weight="bolder", size="large", color="accent",
                )],
            },
        ],
    })

    # ── COMPANY ───────────────────────────────────────────────────────────
    body.append(_heading("COMPANY"))
    body.append(_tb(company, weight="bolder", size="medium"))

    if description:
        body.append(_tb(description, isSubtle=True, spacing="none"))

    if website:
        url    = website if website.startswith("http") else f"https://{website}"
        domain = url.replace("https://", "").replace("http://", "").rstrip("/")
        body.append(_tb(f"[{domain}]({url})", spacing="none"))

    # One-line company meta
    meta_parts: list[str] = []
    if employees:
        meta_parts.append(f"{employees} employees")
    if location and location not in ("GB", ""):
        meta_parts.append(location)
    if founded:
        meta_parts.append(f"Est. {founded}")
    if meta_parts:
        body.append(_tb(" | ".join(meta_parts), isSubtle=True, spacing="none"))

    if ch_number:
        ch_url = (
            f"https://find-and-update.company-information.service.gov.uk"
            f"/company/{ch_number}"
        )
        body.append(_tb(f"[Companies House: {ch_number}]({ch_url})", spacing="none"))

    if sic_code:
        body.append(_tb(f"SIC: {sic_code}", isSubtle=True, spacing="none"))

    if revenue:
        body.append(_tb(f"Revenue: {revenue}", spacing="none"))

    if comp_phone:
        body.append(_tb(f"📞 {comp_phone}", spacing="none"))

    # ── TECH STACK ────────────────────────────────────────────────────────
    if tech_stack:
        body.append(_sep())
        body.append(_heading("TECH STACK"))
        body.append(_tb(tech_stack))

    # ── PEOPLE ────────────────────────────────────────────────────────────
    body.append(_sep())
    body.append(_heading("PEOPLE"))

    def _contact_block(
        name: str,
        title: str,
        email: str,
        phone: str,
        linkedin: str,
        background: str,
    ) -> None:
        if not name:
            return
        label = f"**{name}**" + (f"  |  {title}" if title else "")
        body.append(_tb(label))
        links: list[str] = []
        if email:
            links.append(f"[{email}](mailto:{email})")
        if linkedin:
            short = linkedin.replace("https://", "").replace("http://", "").rstrip("/")
            links.append(f"[{short}]({linkedin})")
        if links:
            body.append(_tb("  ·  ".join(links), spacing="none", isSubtle=True))
        if phone:
            body.append(_tb(f"📞 {phone}", spacing="none", isSubtle=True))
        if li_activity and name == contact:   # only for primary
            body.append(_tb(f"Recent: {li_activity}", spacing="none", isSubtle=True))
        if background:
            body.append(_tb(f"Background: {background}", spacing="none", isSubtle=True))

    _contact_block(contact, job_title, email_addr, phone, linkedin, dm_bg)

    for oc in (other_cons or []):
        if isinstance(oc, dict) and oc.get("name"):
            body.append(_tb(" ", spacing="small"))   # small gap between contacts
            _contact_block(
                oc.get("name", ""),
                oc.get("title", ""),
                oc.get("email", ""),
                oc.get("phone", ""),
                oc.get("linkedin", ""),
                oc.get("background", ""),
            )

    # ── WHY NOW ───────────────────────────────────────────────────────────
    if signal:
        body.append(_sep())
        body.append(_heading("WHY NOW"))
        body.append(_tb(signal))

    # ── PAIN ──────────────────────────────────────────────────────────────
    if pain:
        body.append(_sep())
        body.append(_heading("PAIN"))
        body.append(_tb(pain))

    # ── RECENT NEWS ───────────────────────────────────────────────────────
    if recent_news:
        body.append(_sep())
        body.append(_heading("RECENT NEWS"))
        for item in recent_news[:5]:   # cap at 5
            if item:
                body.append(_tb(f"- {item}", spacing="none"))

    # ── OUR PLAY ──────────────────────────────────────────────────────────
    if play:
        body.append(_sep())
        body.append(_heading("OUR PLAY"))
        body.append(_tb(play))

    # ── TALK TRACK ────────────────────────────────────────────────────────
    if talk_track:
        body.append(_sep())
        body.append(_heading("TALK TRACK"))
        body.append(_tb(f'"{talk_track}"', isSubtle=True))

    # ── CRM FOOTER ────────────────────────────────────────────────────────
    footer_parts: list[str] = []
    if deal_name:
        footer_parts.append(deal_name)
    crm: list[str] = []
    if hubspot_con:
        crm.append(f"HubSpot: {hubspot_con}")
    if hubspot_deal:
        crm.append(f"Deal: {hubspot_deal}")
    crm.append(f"Instantly: {'enrolled' if instantly_id else 'skipped'}")
    footer_parts.append(" | ".join(crm))

    body.append(_sep())
    for i, line in enumerate(footer_parts):
        body.append(_tb(line, isSubtle=True, size="small", spacing="none" if i else "small"))

    # ── ACTION BUTTONS ────────────────────────────────────────────────────
    call_number = phone or comp_phone
    if call_number:
        # tel: URI must not contain spaces
        tel_uri = "tel:" + call_number.replace(" ", "")
        actions.append(_action(f"📞 Call", tel_uri))

    if hubspot_deal:
        hs_url = f"https://app.hubspot.com/contacts/0/deal/{hubspot_deal}"
        actions.append(_action("Open HubSpot", hs_url))
    elif hubspot_con:
        hs_url = f"https://app.hubspot.com/contacts/0/contact/{hubspot_con}"
        actions.append(_action("Open HubSpot", hs_url))

    if linkedin:
        actions.append(_action("LinkedIn", linkedin))

    return _wrap_card(body, actions if actions else None)
