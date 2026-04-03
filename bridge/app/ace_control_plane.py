"""
ACE Control Plane — daily Alliance Lead briefing card.

Runs at 06:00 every weekday via POST /ace/control-plane.
Makes 8 Partner Central MCP queries in PARALLEL (asyncio.gather).
Cross-references with DynamoDB engagement log.

The card has 6 sections:
  1. WHAT HAPPENED SINCE LAST CHECK (new opps, stage changes, AWS closures)
  2. YOUR ACTIONS TODAY (max 5, prioritised: AR > new referrals > past close dates > co-sell stale > stage misaligned)
  3. WHERE THE MONEY IS (live deals by stage, top opportunities)
  4. FUNDING YOU CAN CLAIM (POC/MAP/CEI eligible)
  5. CO-SELL MOMENTUM (active AWS reps and their deals)
  6. PIPELINE SNAPSHOT (counts + revenue by stage, for reference only)

Exported:
  run_control_plane()              -> dict with all sections
  post_control_plane_to_teams(data) -> bool
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app import teams
from app.ace_cards import _action, _factset, _header_tb, _heading, _sep, _tb, _wrap_card
from app.mcp_parser import parse_mcp_response, parse_pipe_rows, parse_structured

logger = logging.getLogger("bridge")

# ── MCP query strings ─────────────────────────────────────────────────────────

_QUERIES: dict[str, str] = {
    "new_opps": (
        "List all opportunities created in the last 48 hours. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | REVENUE | AWS_ACCOUNT_OWNER | USE_CASE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "stage_changes": (
        "List all opportunities with a stage change in the last 48 hours. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | OLD_STAGE | NEW_STAGE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "action_required": (
        "List all opportunities with Action Required status. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | WHAT_NEEDED | DAYS_FLAGGED\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "pipeline_by_stage": (
        "List all open opportunities by stage with count and total expected revenue. "
        "Respond with ONLY this format, one stage per line:\n"
        "STAGE_NAME | COUNT | TOTAL_REVENUE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "tech_validation_plus": (
        "List all open opportunities at Technical Validation stage or beyond "
        "(Technical Validation, Business Validation, Committed). "
        "Sort by revenue descending. Maximum 10 results. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | REVENUE | STAGE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "stage_misaligned": (
        "List open opportunities where the AWS stage differs from the partner stage. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | PARTNER_STAGE | AWS_STAGE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "cosell": (
        "List open opportunities with an active AWS Sales Rep engaged in co-sell. "
        "Sort by revenue descending. Maximum 10 results. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | REVENUE | AWS_REP | STAGE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "past_close_dates": (
        "List open opportunities whose target close date is in the past. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | CLOSE_DATE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
}

# ── Section schemas ───────────────────────────────────────────────────────────

_SCHEMAS: dict[str, dict] = {
    "new_opps":          {"fields": 5},
    "stage_changes":     {"fields": 4},
    "action_required":   {"fields": 4},
    "pipeline_by_stage": {"fields": 3},
    "tech_validation_plus": {"fields": 4},
    "stage_misaligned":  {"fields": 4},
    "cosell":            {"fields": 5},
    "past_close_dates":  {"fields": 3},
}

_EMPTY_SENTINELS = ("No data available.", "Query failed", "None found")


def _has_data(text: str) -> bool:
    if not text:
        return False
    return not any(text.startswith(s) for s in _EMPTY_SENTINELS)


def _parse_rows(raw: str, key: str) -> list[list[str]]:
    """Parse pipe-delimited rows for a section, or return []."""
    if not raw or raw.startswith("Query failed"):
        return []
    schema = _SCHEMAS.get(key, {})
    fields = schema.get("fields", 4)
    return parse_pipe_rows(raw, fields)


async def _mcp(query: str) -> str:
    """Run one MCP query. Returns empty string on failure."""
    from app import mcp_client
    try:
        result = await mcp_client.send_message(query, catalog="AWS")
        text = parse_mcp_response(result)
        return text if text else "No data available."
    except Exception as exc:
        logger.warning("ace_control_plane_mcp_failed",
                       extra={"error": str(exc), "query": query[:60]})
        return "Query failed — check MCP connection."


def _slug(name: str) -> str:
    """Quick slug for DynamoDB lookup without importing knowledge module."""
    import re
    s = name.lower().strip()
    s = re.sub(r"[&+]", "and", s)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s)
    return s.strip("-") or "unknown"


def _last_contact(company: str) -> Optional[str]:
    """Return ISO date of last outreach to a company, or None."""
    try:
        from app import knowledge
        event = knowledge.get_last_event(_slug(company), "outreach_sent")
        if event:
            return event.get("created_at", "")[:10]
        return None
    except Exception:
        return None


def _contacted_since(company: str, days: int) -> bool:
    """True if outreach was sent to this company in the last N days."""
    last = _last_contact(company)
    if not last:
        return False
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        contact_date = datetime.fromisoformat(last).date()
        return contact_date >= cutoff
    except Exception:
        return False


# ── Section builders ──────────────────────────────────────────────────────────

def _build_what_happened(
    new_opps: list[list[str]],
    stage_changes: list[list[str]],
    stage_misaligned: list[list[str]],
) -> list[str]:
    """Return list of bullet strings for WHAT HAPPENED section."""
    items: list[str] = []

    # New AWS referrals / opportunities
    for row in new_opps[:5]:
        opp_id = row[0]
        company = row[1]
        revenue = row[2]
        rep = row[3]
        use_case = row[4] if len(row) > 4 else ""
        line = f"NEW: {company} ({opp_id}) — {revenue} from {rep}"
        if use_case:
            line += f"  —  {use_case}"
        contacted = _contacted_since(company, 2)
        if contacted:
            line += f"\n  → Contacted recently. Awaiting reply."
        else:
            line += f"\n  → Not yet contacted."
        items.append(line)

    # Stage changes
    for row in stage_changes[:5]:
        opp_id = row[0]
        company = row[1]
        old_stage = row[2]
        new_stage = row[3]
        items.append(f"STAGE CHANGE: {company} ({opp_id})  {old_stage} → {new_stage}")

    # AWS-closed deals (AWS stage = Closed/Lost but partner hasn't closed)
    aws_closed = [
        r for r in stage_misaligned
        if "closed" in r[3].lower() or "lost" in r[3].lower()
    ]
    if aws_closed:
        names = ", ".join(r[1] for r in aws_closed[:5])
        items.append(
            f"AWS CLOSED: {names} ({len(aws_closed)} deal{'s' if len(aws_closed) != 1 else ''})\n"
            f"  → AWS closed these administratively. Close on your side too."
        )

    if not items:
        items.append("Nothing new since last check.")

    return items


def _build_do_this_today(
    action_required: list[list[str]],
    new_opps: list[list[str]],
    past_close_dates: list[list[str]],
    cosell: list[list[str]],
    stage_misaligned: list[list[str]],
) -> list[str]:
    """Return max 5 numbered action strings, most urgent first."""
    actions: list[str] = []

    # 1. Action Required from AWS (SLA: 5 business days, highest priority)
    for row in action_required:
        if len(actions) >= 5:
            break
        opp_id = row[0]
        company = row[1]
        what = row[2]
        days = row[3]
        actions.append(
            f"{company} ({opp_id}) — Action Required from AWS\n"
            f"   Fix: {what}. {days} days since flagged. SLA: 5 business days."
        )

    # 2. New AWS referrals not yet contacted (< 7 days, SLA clock running)
    for row in new_opps:
        if len(actions) >= 5:
            break
        company = row[1]
        revenue = row[2]
        rep = row[3]
        if not _contacted_since(company, 1):
            actions.append(
                f"{company} — {revenue} — AWS Referral from {rep}\n"
                f"   Not yet contacted. Contact today — day 1 of 5-day SLA."
            )

    # 3. Past close dates
    for row in past_close_dates:
        if len(actions) >= 5:
            break
        opp_id = row[0]
        company = row[1]
        close_date = row[2]
        actions.append(
            f"{company} ({opp_id}) — close date PASSED {close_date}\n"
            f"   Update the date or close the deal."
        )

    # 4. Co-sell deals with no update in 14+ days
    for row in cosell:
        if len(actions) >= 5:
            break
        company = row[1]
        revenue = row[2]
        rep = row[3]
        if not _contacted_since(company, 14):
            actions.append(
                f"{company} — {revenue} — co-sell with {rep}\n"
                f"   No update in 14+ days. Update the rep before they disengage."
            )

    # 5. Stage misalignments where AWS is ahead
    for row in stage_misaligned:
        if len(actions) >= 5:
            break
        opp_id = row[0]
        company = row[1]
        partner_stage = row[2]
        aws_stage = row[3]
        if "closed" not in aws_stage.lower() and "lost" not in aws_stage.lower():
            actions.append(
                f"{company} ({opp_id}) — stage misaligned\n"
                f"   Partner: {partner_stage}  |  AWS: {aws_stage}  — align before next review."
            )

    if not actions:
        actions.append("Pipeline is clean. No immediate actions required.")

    return actions


def _build_where_money(
    pipeline_by_stage: list[list[str]],
    tech_validation_plus: list[list[str]],
) -> list[str]:
    """Return formatted lines for WHERE THE MONEY IS section."""
    lines: list[str] = []

    # Parse pipeline for stage-level totals
    stage_map: dict[str, tuple[str, str]] = {}  # stage → (count, revenue)
    for row in pipeline_by_stage:
        stage = row[0]
        count = row[1]
        revenue = row[2]
        stage_map[stage.upper()] = (count, revenue)

    # Show live stages in order (skip Launched — done)
    stage_order = [
        ("COMMITTED",           "Committed"),
        ("BUSINESS_VALIDATION", "Business Validation"),
        ("TECHNICAL_VALIDATION","Tech Validation"),
        ("QUALIFIED",           "Qualified"),
        ("PROSPECT",            "Prospect"),
    ]

    committed_count = 0
    for stage_key, stage_label in stage_order:
        entry = stage_map.get(stage_key) or stage_map.get(stage_key.replace("_", " ")) or stage_map.get(stage_label.upper())
        if entry:
            count, revenue = entry
            if "committed" in stage_key.lower():
                try:
                    committed_count = int(count)
                except ValueError:
                    committed_count = 0 if count == "0" else 1
            lines.append(f"{stage_label}: {count} deals ({revenue})")

    # Committed warning
    if committed_count == 0:
        lines.append(
            "⚠ CRITICAL: Nothing at Committed. Cannot claim funding.\n"
            "   Move Tech Validation deals to proposals to fix this."
        )

    # Top deals at Tech Validation+
    tv_deals = [r for r in tech_validation_plus if len(r) >= 4]
    if tv_deals:
        lines.append("")
        lines.append("Top deals closest to revenue:")
        for row in tv_deals[:5]:
            opp_id = row[0]
            company = row[1]
            revenue = row[2]
            stage = row[3]
            lines.append(f"  {company} ({opp_id}) — {revenue} — {stage}")

    return lines


def _build_funding(tech_validation_plus: list[list[str]]) -> list[str]:
    """Return funding eligibility summary lines."""
    lines: list[str] = []

    poc_eligible = [
        r for r in tech_validation_plus
        if "tech" in r[3].lower() or "business" in r[3].lower() or "committed" in r[3].lower()
    ]
    map_eligible = [r for r in tech_validation_plus if "migration" in " ".join(r).lower()]
    cei_eligible = [r for r in tech_validation_plus if "greenfield" in " ".join(r).lower()]

    n_poc = len(poc_eligible)
    if n_poc > 0:
        lines.append(
            f"{n_poc} deal{'s' if n_poc != 1 else ''} at Tech Validation+ qualify for POC funding.\n"
            f"   Submit POC request 14 days before planned activity."
        )
        lines.append("   Top candidates:")
        for row in poc_eligible[:3]:
            opp_id = row[0]
            company = row[1]
            revenue = row[2]
            lines.append(f"   - {company} ({opp_id}) — {revenue}")
    else:
        lines.append("No deals currently qualify for POC funding.")

    n_map = len(map_eligible)
    lines.append(
        f"{n_map} deal{'s' if n_map != 1 else ''} qualify for MAP (migration workload required)."
        if n_map > 0 else "0 deals qualify for MAP (need migration workload)."
    )

    n_cei = len(cei_eligible)
    lines.append(
        f"{n_cei} deal{'s' if n_cei != 1 else ''} qualify for CEI (greenfield customer required)."
        if n_cei > 0 else "0 deals qualify for CEI (need greenfield customer)."
    )

    return lines


def _build_cosell(cosell_rows: list[list[str]]) -> list[str]:
    """Return formatted co-sell lines grouped by AWS rep."""
    if not cosell_rows:
        return ["No active co-sell engagements found."]

    # Group by rep name
    reps: dict[str, list] = {}
    for row in cosell_rows:
        if len(row) < 5:
            continue
        rep = row[3]
        if rep not in reps:
            reps[rep] = []
        reps[rep].append(row)

    lines: list[str] = []
    for rep, deals in sorted(reps.items(), key=lambda x: len(x[1]), reverse=True)[:6]:
        total_rev = len(deals)  # approximation without revenue parsing
        deal_list = ", ".join(r[1] for r in deals[:3])
        extra = f" + {len(deals) - 3} more" if len(deals) > 3 else ""
        lines.append(f"{rep} — {deal_list}{extra}")

    lines.append("")
    lines.append("→ Thank these reps. They are sending you business.")
    lines.append("→ Update them on progress. They check ACE too.")

    return lines


def _build_pipeline_snapshot(pipeline_by_stage: list[list[str]]) -> list[dict]:
    """Return FactSet facts for pipeline snapshot."""
    facts: list[dict] = []
    total_deals = 0
    total_rev_str = ""

    stage_order = ["Prospect", "Qualified", "Technical Validation",
                   "Business Validation", "Committed", "Launched"]

    stage_map: dict[str, tuple[str, str]] = {}
    for row in pipeline_by_stage:
        stage_map[row[0].upper()] = (row[1], row[2])
        # Also try title case
        stage_map[row[0].title()] = (row[1], row[2])

    for stage in stage_order:
        entry = stage_map.get(stage.upper()) or stage_map.get(stage)
        if entry:
            count, revenue = entry
            facts.append({"title": stage, "value": f"{count} deals ({revenue})"})
            try:
                total_deals += int(count)
            except ValueError:
                pass

    return facts


# ── Main runner ───────────────────────────────────────────────────────────────

async def run_control_plane(stats: Optional[dict] = None) -> dict:
    """Run all 8 MCP queries in PARALLEL and build the control plane data.

    Returns structured dict with all sections ready for card building
    or direct JSON response via GET /ace/control-plane.
    """
    # Run all 8 queries in parallel
    keys = list(_QUERIES.keys())
    raw_results = await asyncio.gather(
        *[_mcp(q) for q in _QUERIES.values()],
        return_exceptions=True,
    )

    raw: dict[str, str] = {}
    for key, result in zip(keys, raw_results):
        if isinstance(result, Exception):
            logger.warning("ace_control_plane_gather_exception",
                           extra={"section": key, "error": str(result)})
            raw[key] = "Query failed — check MCP connection."
        else:
            raw[key] = result or "No data available."

    # Parse all sections into structured rows
    parsed = {key: _parse_rows(raw[key], key) for key in keys}

    # Build each section
    what_happened = _build_what_happened(
        parsed["new_opps"],
        parsed["stage_changes"],
        parsed["stage_misaligned"],
    )
    do_this_today = _build_do_this_today(
        parsed["action_required"],
        parsed["new_opps"],
        parsed["past_close_dates"],
        parsed["cosell"],
        parsed["stage_misaligned"],
    )
    where_money = _build_where_money(
        parsed["pipeline_by_stage"],
        parsed["tech_validation_plus"],
    )
    funding = _build_funding(parsed["tech_validation_plus"])
    cosell_lines = _build_cosell(parsed["cosell"])
    pipeline_facts = _build_pipeline_snapshot(parsed["pipeline_by_stage"])

    leads_today = (stats or {}).get("total_leads", 0)

    return {
        "date":             datetime.now().strftime("%d %b %Y"),
        "leads_today":      leads_today,
        "what_happened":    what_happened,
        "do_this_today":    do_this_today,
        "where_money":      where_money,
        "funding":          funding,
        "cosell":           cosell_lines,
        "pipeline_facts":   pipeline_facts,
        # Raw section data for the JSON response
        "new_opps_count":   len(parsed["new_opps"]),
        "action_req_count": len(parsed["action_required"]),
        "cosell_count":     len(parsed["cosell"]),
    }


# ── Teams card ────────────────────────────────────────────────────────────────

def build_control_plane_card(data: dict) -> dict:
    """Build the ACE Control Plane Adaptive Card.

    Bold TextBlock headers. No coloured Container backgrounds.
    Horizontal rules via separator TextBlocks.
    Under 20 KB. msteams: width Full.
    """
    date          = data.get("date", datetime.now().strftime("%d %b %Y"))
    what_happened = data.get("what_happened") or []
    do_this_today = data.get("do_this_today") or []
    where_money   = data.get("where_money") or []
    funding       = data.get("funding") or []
    cosell        = data.get("cosell") or []
    pipeline_facts = data.get("pipeline_facts") or []
    leads_today   = data.get("leads_today", 0)
    action_count  = data.get("action_req_count", 0)
    new_count     = data.get("new_opps_count", 0)

    # Header color: red if action required, amber if new opps, else accent
    color = "attention" if action_count > 0 else ("warning" if new_count > 0 else "accent")

    body: list[dict] = []

    # ── Header ────────────────────────────────────────────────────────────────
    body.append(_header_tb(f"● ACE CONTROL PLANE — {date}", color=color))
    if leads_today:
        body.append(_tb(f"SDR agents found {leads_today} leads today.", isSubtle=True, spacing="none"))

    # ── WHAT HAPPENED SINCE LAST CHECK ────────────────────────────────────────
    body.append(_sep())
    body.append(_heading("WHAT HAPPENED SINCE LAST CHECK"))
    for item in what_happened[:6]:
        for line in item.split("\n"):
            line = line.strip()
            if line:
                body.append(_tb(f"• {line}" if not line.startswith("→") else f"  {line}",
                                spacing="none"))

    # ── YOUR ACTIONS TODAY ────────────────────────────────────────────────────
    body.append(_sep())
    body.append(_heading("YOUR ACTIONS TODAY"))
    for i, action in enumerate(do_this_today[:5], 1):
        lines = action.split("\n")
        body.append(_tb(f"{i}. {lines[0].strip()}", spacing="none" if i > 1 else "small",
                        weight="bolder" if i == 1 else "default"))
        for detail_line in lines[1:]:
            detail = detail_line.strip()
            if detail:
                body.append(_tb(f"   {detail}", spacing="none", isSubtle=True))

    # ── WHERE THE MONEY IS ────────────────────────────────────────────────────
    body.append(_sep())
    body.append(_heading("WHERE THE MONEY IS"))
    for line in where_money[:12]:
        if line == "":
            continue
        if line.startswith("⚠"):
            parts = line.split("\n")
            body.append(_tb(parts[0], spacing="small", color="attention"))
            for p in parts[1:]:
                if p.strip():
                    body.append(_tb(p.strip(), spacing="none", isSubtle=True))
        elif line.startswith("Top deals"):
            body.append(_tb(line, spacing="small", weight="bolder", size="small"))
        elif line.startswith("  "):
            body.append(_tb(line.strip(), spacing="none", isSubtle=True))
        else:
            body.append(_tb(line, spacing="none"))

    # ── FUNDING YOU CAN CLAIM ─────────────────────────────────────────────────
    body.append(_sep())
    body.append(_heading("FUNDING YOU CAN CLAIM"))
    for line in funding[:8]:
        if line == "":
            continue
        parts = line.split("\n")
        body.append(_tb(parts[0].strip(), spacing="none"))
        for p in parts[1:]:
            if p.strip():
                body.append(_tb(p.strip(), spacing="none", isSubtle=True))

    # ── CO-SELL MOMENTUM ──────────────────────────────────────────────────────
    body.append(_sep())
    body.append(_heading("CO-SELL MOMENTUM"))
    for line in cosell[:10]:
        if line == "":
            continue
        if line.startswith("→"):
            body.append(_tb(line, spacing="small", isSubtle=True))
        else:
            body.append(_tb(line, spacing="none"))

    # ── PIPELINE SNAPSHOT ─────────────────────────────────────────────────────
    if pipeline_facts:
        body.append(_sep())
        body.append(_heading("PIPELINE SNAPSHOT"))
        body.append(_factset(pipeline_facts[:8]))

    # ── Action button ─────────────────────────────────────────────────────────
    actions = [_action("Open Partner Central",
                       "https://partnercentral.awspartner.com/opportunities")]

    return _wrap_card(body, actions)


async def post_control_plane_to_teams(data: dict) -> bool:
    """Build the control plane card and post to CEO/ACE channel."""
    card = build_control_plane_card(data)
    webhook_key = teams._resolve_webhook("teams/ceo-webhook-url")
    ok = await teams._post_raw(card, webhook_key)
    if not ok:
        date  = data.get("date", "")
        items = data.get("do_this_today") or []
        title = f"ACE CONTROL PLANE — {date}"
        body_text = "\n".join(f"{i+1}. {item.split(chr(10))[0]}" for i, item in enumerate(items[:5]))
        simple = teams._build_simple(title, body_text or "Check Partner Central for updates.")
        ok = await teams._post_raw(simple, webhook_key)
    return ok
