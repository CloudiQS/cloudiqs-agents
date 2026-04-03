"""
CEO Briefing — comprehensive daily morning briefing for Steve.

Runs at 06:00 every day via POST /ceo/briefing (called by ceo-ops agent).
Makes 8 Partner Central MCP queries in PARALLEL (asyncio.gather) covering
pipeline scorecard, actions required, deals closing soon, AWS stage truth,
co-sell activity, funding eligibility, AWS recommended actions, and rep
activity. Combines with today's HubSpot lead stats and Q2 target data.

On Mondays, adds a weekly hygiene section (3 more parallel MCP queries).

All queries use structured pipe-delimited or key:value format so responses
are parseable data, not conversational AI text. Any commentary lines are
stripped by parse_pipe_rows() and parse_structured().

Q2 TARGET: £400,000 pipeline by 30 June 2026.

GET /ceo/briefing returns the same structured data as JSON without posting.

Exported:
  run_briefing(stats)           -> dict with all briefing sections
  post_briefing_to_teams(data)  -> bool
  _extract_assistant_text(r)    -> str  (legacy, prefer mcp_parser.parse_mcp_response)
  _strip_narrative(text)        -> str  (legacy, prefer mcp_parser.strip_narrative)
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from app import teams
from app.ace_cards import _factset, _header_tb, _heading, _sep, _tb, _wrap_card
from app.mcp_parser import (
    extract_facts,
    parse_mcp_response,
    parse_pipe_rows,
    parse_structured,
    strip_narrative,
    strip_pipe_tables,
    truncate,
)

logger = logging.getLogger("bridge")

# Q2 2026 targets
Q2_PIPELINE_TARGET = 400_000   # £400K
Q2_START = "2026-04-01"
Q2_END   = "2026-06-30"

# Per-section character budgets (kept for _clean() backward compatibility)
_BUDGET = {
    "do_today":  500,
    "pipeline":  300,
    "aws_truth": 200,
    "at_risk":   300,
    "funding":   280,
    "cosell":    220,
    "weekly":    400,
}

# Pipe-delimited sections: expected field count + display format
_PIPE_SECTIONS: dict[str, dict] = {
    "action_required": {"fields": 4, "fmt": "{0} | {1} | {2} | {3}d left"},
    "closing_soon":    {"fields": 3, "fmt": "{0} | {1} | closes {2}"},
    "cosell":          {"fields": 4, "fmt": "{0} | {1} | Rep: {2} | {3}"},
    "funding":         {"fields": 3, "fmt": "{0} | {1} | {2}"},
    "aws_actions":     {"fields": 3, "fmt": "{0} | {1} | {2}"},
}


# ── Legacy parsing helpers (kept for backward compatibility) ──────────────────

def _extract_assistant_text(result) -> str:
    """Legacy: extract text from MCP response. Use mcp_parser.parse_mcp_response instead."""
    return parse_mcp_response(result)


def _strip_narrative(text: str) -> str:
    """Legacy: strip MCP narrative. Use mcp_parser.strip_narrative instead."""
    return strip_narrative(text)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _clean(text: str, budget: int) -> str:
    """Strip narrative and pipe tables, then truncate to budget chars."""
    cleaned = strip_narrative(text) if text else ""
    cleaned = strip_pipe_tables(cleaned)
    if not cleaned:
        return "No data."
    return truncate(cleaned, budget)


def _has_content(text: str) -> bool:
    """True if section has real data (not a failure/empty placeholder)."""
    if not text:
        return False
    return not any(text.startswith(p) for p in ("No data", "Query failed", "None"))


def _theme_for(data: dict) -> str:
    """Pick card border colour based on urgency (legacy hex colours)."""
    if _has_content(data.get("action_required", "")):
        return "C00000"   # red — AWS wants something NOW
    if _has_content(data.get("closing_soon", "")):
        return "FFC000"   # amber — deals at risk
    return "1F3D7A"        # blue — normal morning briefing


def _card_color(data: dict) -> str:
    """Named Adaptive Card color for CEO card header."""
    if _has_content(data.get("action_required", "")):
        return "attention"
    if _has_content(data.get("closing_soon", "")):
        return "warning"
    return "accent"


def _fmt_pipe(raw: str, key: str) -> str:
    """Parse pipe-delimited MCP response for one section, format for display."""
    if not raw or raw.startswith("Query failed"):
        return raw if raw else "No data."
    schema = _PIPE_SECTIONS[key]
    rows = parse_pipe_rows(raw, schema["fields"])
    if not rows:
        return "No data."
    fmt = schema["fmt"]
    return "\n".join(fmt.format(*r) for r in rows[:10])


def _fmt_kv(raw: str) -> str:
    """Parse key:value MCP response, format as label: value lines."""
    if not raw or raw.startswith("Query failed"):
        return raw if raw else "No data."
    parsed = parse_structured(raw)
    if not parsed:
        return "No data."
    return "\n".join(
        f"{k.replace('_', ' ').title()}: {v}" for k, v in parsed.items()
    )


async def _mcp(query: str) -> str:
    """Run one Partner Central MCP query. Returns fallback string on failure."""
    from app import mcp_client
    try:
        result = await mcp_client.send_message(query, catalog="AWS")
        text = parse_mcp_response(result)
        return text if text else "No data."
    except Exception as exc:
        logger.warning(
            "ceo_briefing_mcp_query_failed",
            extra={"error": str(exc), "query": query[:60]},
        )
        return "Query failed — check MCP connection."


# ── Main briefing runner ──────────────────────────────────────────────────────

async def run_briefing(stats: Optional[dict] = None) -> dict:
    """Run all CEO briefing queries in PARALLEL and return structured data.

    8 core queries run simultaneously via asyncio.gather (not sequentially).
    All queries use structured formats — pipe-delimited for lists, key:value
    for summaries — so MCP cannot inject conversational commentary.

    On Mondays, 3 additional weekly queries also run in parallel.
    Total wall-clock time: ~60-90s instead of 4-8 minutes.
    """
    is_monday = datetime.now().weekday() == 0

    # ── 8 core queries (parallel, structured format) ──────────────────────────
    queries = {
        "pipeline": (
            "List all open opportunities by stage with count and total expected revenue. "
            "Respond with ONLY this format, one stage per line:\n"
            "STAGE_NAME: COUNT opportunities, £REVENUE total\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "action_required": (
            "List all opportunities with Action Required status. "
            "Respond with ONLY this format, one opportunity per line:\n"
            "OPP_ID | COMPANY | ISSUE | DAYS_REMAINING\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "closing_soon": (
            "List open opportunities with target close dates within 30 days. "
            "Respond with ONLY this format, one opportunity per line:\n"
            "OPP_ID | COMPANY | CLOSE_DATE\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "aws_stage": (
            "Compare AWS stage vs partner stage for all open opportunities. "
            "Respond with ONLY this format:\n"
            "ALIGNED: COUNT\n"
            "MISALIGNED: COUNT\n"
            "NO_AWS_STAGE: COUNT\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "cosell": (
            "List open opportunities with an active AWS Sales Rep engaged in co-sell. "
            "Maximum 5 results, sorted by revenue descending. "
            "Respond with ONLY this format, one opportunity per line:\n"
            "OPP_ID | COMPANY | REP_NAME | REVENUE\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "funding": (
            "List open opportunities at Business Validation, Technical Validation, "
            "or Committed stage eligible for MAP, POC, or CEI funding. "
            "Respond with ONLY this format, one opportunity per line:\n"
            "OPP_ID | COMPANY | PROGRAM\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "aws_actions": (
            "List open opportunities with incomplete AWS recommended actions. "
            "Respond with ONLY this format, one opportunity per line:\n"
            "OPP_ID | COMPANY | ACTION_REQUIRED\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "rep_activity": (
            "List the top 5 AWS Sales Reps most active on my opportunities this month. "
            "Respond with ONLY this format, one rep per line:\n"
            "REP_NAME: OPPORTUNITY_COUNT opportunities\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
    }

    raw_results = await asyncio.gather(
        *[_mcp(q) for q in queries.values()],
        return_exceptions=True,
    )

    sections: dict = {}
    for key, raw in zip(queries.keys(), raw_results):
        if isinstance(raw, Exception):
            logger.warning("ceo_briefing_gather_exception", extra={"section": key, "error": str(raw)})
            sections[key] = "Query failed — check MCP connection."
        elif key in _PIPE_SECTIONS:
            sections[key] = _fmt_pipe(raw, key)
        else:
            sections[key] = _fmt_kv(raw)

    # ── Monday weekly queries (parallel, structured format) ───────────────────
    weekly: dict = {}
    if is_monday:
        weekly_queries = {
            "close_date_cleanup": (
                "Count open opportunities with close dates in the past. "
                "Respond with ONLY this format:\n"
                "OVERDUE: COUNT\n"
                "Do not include headers, explanations, or any other text."
            ),
            "closed_lost_analysis": (
                "List the top 3 reasons for Closed Lost opportunities. "
                "Respond with ONLY this format, one reason per line:\n"
                "REASON: COUNT deals\n"
                "Do not include headers, explanations, or any other text."
            ),
            "pipeline_velocity": (
                "What is the average days from Qualified to Launched stage. "
                "Respond with ONLY this format:\n"
                "AVERAGE_DAYS: COUNT\n"
                "Do not include headers, explanations, or any other text."
            ),
        }
        weekly_results = await asyncio.gather(
            *[_mcp(q) for q in weekly_queries.values()],
            return_exceptions=True,
        )
        for key, raw in zip(weekly_queries.keys(), weekly_results):
            weekly[key] = _fmt_kv(raw) if not isinstance(raw, Exception) else "Query failed."

    leads_today = (stats or {}).get("total_leads", 0)

    return {
        "date":            datetime.now().strftime("%d %b %Y"),
        "is_monday":       is_monday,
        "pipeline":        sections.get("pipeline", ""),
        "action_required": sections.get("action_required", ""),
        "closing_soon":    sections.get("closing_soon", ""),
        "aws_stage":       sections.get("aws_stage", ""),
        "cosell":          sections.get("cosell", ""),
        "funding":         sections.get("funding", ""),
        "aws_actions":     sections.get("aws_actions", ""),
        "rep_activity":    sections.get("rep_activity", ""),
        "weekly":          weekly,
        "leads_today":     leads_today,
    }


# ── Teams card formatter ──────────────────────────────────────────────────────

async def post_briefing_to_teams(data: dict) -> bool:
    """Build an Adaptive Card using ace_cards primitives and post to CEO channel.

    Uses bold TextBlock headers on the default card background.
    No coloured Container backgrounds anywhere.
    """
    date        = data.get("date", datetime.now().strftime("%d %b %Y"))
    is_monday   = data.get("is_monday", False)
    weekly      = data.get("weekly", {})
    leads_today = data.get("leads_today", 0)

    ar      = data.get("action_required", "")
    aa      = data.get("aws_actions", "")
    cs      = data.get("closing_soon", "")
    pipeline = data.get("pipeline", "")
    aws     = data.get("aws_stage", "")
    funding = data.get("funding", "")
    cosell  = data.get("cosell", "")
    rep     = data.get("rep_activity", "")

    color = _card_color(data)
    body: list[dict] = []

    # ── Header ────────────────────────────────────────────────────────────────
    body.append(_header_tb(f"● CLOUDIQS CEO BRIEFING — {date}", color=color))

    # ── Metrics strip ─────────────────────────────────────────────────────────
    body.append(_sep())
    body.append(_factset([
        {"title": "Leads today", "value": str(leads_today)},
        {"title": "Q2 Target",   "value": "£400,000"},
    ]))

    # ── TODAY'S FOCUS (action required + aws actions + closing soon) ──────────
    focus_lines: list[str] = []
    if _has_content(ar):
        focus_lines.append("**Action Required:**")
        focus_lines.extend(ar.split("\n")[:5])
    if _has_content(aa):
        focus_lines.append("**AWS Actions:**")
        focus_lines.extend(aa.split("\n")[:3])
    if _has_content(cs):
        focus_lines.append("**Closing this month:**")
        focus_lines.extend(cs.split("\n")[:3])

    if focus_lines:
        body.append(_sep())
        body.append(_heading("TODAY'S FOCUS"))
        for line in focus_lines:
            body.append(_tb(line, spacing="none"))

    # ── PIPELINE BY STAGE ─────────────────────────────────────────────────────
    if _has_content(pipeline):
        facts = extract_facts(pipeline)
        body.append(_sep())
        body.append(_heading("PIPELINE BY STAGE"))
        if facts:
            body.append(_factset(facts))
        else:
            body.append(_tb(pipeline))

    # ── AWS STAGE ALIGNMENT ───────────────────────────────────────────────────
    if _has_content(aws):
        body.append(_sep())
        body.append(_heading("AWS STAGE ALIGNMENT"))
        facts = extract_facts(aws)
        if facts:
            body.append(_factset(facts))
        else:
            body.append(_tb(aws))

    # ── CLOSE DATE RISK ───────────────────────────────────────────────────────
    if _has_content(cs):
        body.append(_sep())
        body.append(_heading("CLOSE DATE RISK — 30 DAYS"))
        for row in cs.split("\n")[:5]:
            if row.strip():
                body.append(_tb(row, spacing="none"))

    # ── FUNDING ELIGIBLE ──────────────────────────────────────────────────────
    if _has_content(funding):
        body.append(_sep())
        body.append(_heading("FUNDING ELIGIBLE"))
        for row in funding.split("\n")[:5]:
            if row.strip():
                body.append(_tb(row, spacing="none"))

    # ── CO-SELL INTELLIGENCE ──────────────────────────────────────────────────
    if _has_content(cosell):
        body.append(_sep())
        body.append(_heading("CO-SELL INTELLIGENCE"))
        for row in cosell.split("\n")[:5]:
            if row.strip():
                body.append(_tb(row, spacing="none"))

    # ── REP ACTIVITY ─────────────────────────────────────────────────────────
    if _has_content(rep):
        body.append(_sep())
        body.append(_heading("REP ACTIVITY"))
        facts = extract_facts(rep)
        if facts:
            body.append(_factset(facts[:5]))
        else:
            body.append(_tb(rep))

    # ── WEEKLY FOCUS (Monday only) ────────────────────────────────────────────
    if is_monday and weekly:
        weekly_parts: list[str] = []
        for label, key in [
            ("Close date cleanup", "close_date_cleanup"),
            ("Closed lost",        "closed_lost_analysis"),
            ("Pipeline velocity",  "pipeline_velocity"),
        ]:
            val = weekly.get(key, "")
            if val and _has_content(val):
                weekly_parts.append(f"**{label}:** {val}")
        if weekly_parts:
            body.append(_sep())
            body.append(_heading("WEEKLY FOCUS"))
            for part in weekly_parts:
                body.append(_tb(part, spacing="none"))

    card = _wrap_card(body)
    webhook_key = teams._resolve_webhook("teams/ceo-webhook-url")
    ok = await teams._post_raw(card, webhook_key)
    if not ok:
        title  = f"CLOUDIQS CEO BRIEFING — {date}"
        simple = teams._build_simple(title, "CEO briefing ready — check Teams")
        ok = await teams._post_raw(simple, webhook_key)
    return ok
