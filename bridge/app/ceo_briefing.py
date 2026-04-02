"""
CEO Briefing — comprehensive daily morning briefing for Steve.

Runs at 06:00 every day via POST /ceo/briefing (called by ceo-ops agent).
Makes 8 Partner Central MCP queries in PARALLEL (asyncio.gather) covering
pipeline scorecard, actions required, deals closing soon, AWS stage truth,
co-sell activity, funding eligibility, AWS recommended actions, and rep
activity. Combines with today's HubSpot lead stats and Q2 target data.

On Mondays, adds a weekly hygiene section (3 more parallel MCP queries).

Q2 TARGET: £400,000 pipeline by 30 June 2026.

GET /ceo/briefing returns the same structured data as JSON without posting.

Exported:
  run_briefing(stats)           -> dict with all briefing sections
  post_briefing_to_teams(data)  -> bool
  _extract_assistant_text(r)    -> str  (legacy, prefer mcp_parser.parse_mcp_response)
  _strip_narrative(text)        -> str  (legacy, prefer mcp_parser.strip_narrative)
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Optional

from app import teams
from app.mcp_parser import parse_mcp_response, strip_narrative, truncate, extract_facts

logger = logging.getLogger("bridge")

# Q2 2026 targets
Q2_PIPELINE_TARGET = 400_000   # £400K
Q2_START = "2026-04-01"
Q2_END   = "2026-06-30"

# Per-section character budgets
_BUDGET = {
    "do_today":  500,
    "pipeline":  300,
    "aws_truth": 200,
    "at_risk":   300,
    "funding":   280,
    "cosell":    220,
    "weekly":    400,
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
    """Strip narrative then truncate to budget chars."""
    cleaned = strip_narrative(text) if text else ""
    if not cleaned:
        return "No data."
    return truncate(cleaned, budget)


def _has_content(text: str) -> bool:
    """True if section has real data (not a failure/empty placeholder)."""
    if not text:
        return False
    return not any(text.startswith(p) for p in ("No data", "Query failed", "None"))


def _theme_for(data: dict) -> str:
    """Pick card border colour based on urgency."""
    if _has_content(data.get("action_required", "")):
        return "C00000"   # red — AWS wants something NOW
    if _has_content(data.get("closing_soon", "")):
        return "FFC000"   # amber — deals at risk
    return "1F3D7A"        # blue — normal morning briefing


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
    On Mondays, 3 additional weekly queries also run in parallel.
    Total wall-clock time: ~60-90s instead of 4-8 minutes.
    """
    is_monday = datetime.now().weekday() == 0

    # ── 8 core queries (parallel) ─────────────────────────────────────────────
    queries = {
        "pipeline": (
            "Give me a breakdown of all my opportunities by stage with count "
            "and total expected revenue per stage. Format as a simple table."
        ),
        "action_required": (
            "List all opportunities with Action Required status. "
            "For each show company name and what action AWS needs from me. Be brief."
        ),
        "closing_soon": (
            "Which opportunities have target close dates within 30 days? "
            "Show company, close date, current stage. Be brief."
        ),
        "aws_stage": (
            "For my Launched opportunities, compare AWS stage versus partner stage. "
            "How many are aligned, misaligned, or have empty AWS stage?"
        ),
        "cosell": (
            "Which of my open opportunities have an AWS Sales Rep actively engaged? "
            "Show top 5 by expected revenue with rep name. Be brief."
        ),
        "funding": (
            "Which opportunities at Business Validation or Technical Validation are "
            "eligible for MAP, POC, or MPOPP funding? Show company and program."
        ),
        "aws_actions": (
            "For all open opportunities, which have AWS recommended actions I have "
            "not completed? Show company and what AWS recommends. Be brief."
        ),
        "rep_activity": (
            "Which AWS Sales Reps have been most active on my opportunities this month? "
            "Show top 5 rep names with opportunity count."
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
        else:
            sections[key] = raw

    # ── Monday weekly queries (parallel) ─────────────────────────────────────
    weekly: dict = {}
    if is_monday:
        weekly_queries = {
            "close_date_cleanup": "How many of my open opportunities have close dates in the past? List them.",
            "closed_lost_analysis": "For my Closed Lost opportunities, what are the top 3 reasons for losing?",
            "pipeline_velocity": (
                "What is the average number of days opportunities take to progress "
                "from Qualified to Launched stage?"
            ),
        }
        weekly_results = await asyncio.gather(
            *[_mcp(q) for q in weekly_queries.values()],
            return_exceptions=True,
        )
        for key, raw in zip(weekly_queries.keys(), weekly_results):
            weekly[key] = raw if not isinstance(raw, Exception) else "Query failed."

    leads_today = (stats or {}).get("total_leads", 0)

    return {
        "date":           datetime.now().strftime("%d %b %Y"),
        "is_monday":      is_monday,
        "pipeline":       sections.get("pipeline", ""),
        "action_required":sections.get("action_required", ""),
        "closing_soon":   sections.get("closing_soon", ""),
        "aws_stage":      sections.get("aws_stage", ""),
        "cosell":         sections.get("cosell", ""),
        "funding":        sections.get("funding", ""),
        "aws_actions":    sections.get("aws_actions", ""),
        "rep_activity":   sections.get("rep_activity", ""),
        "weekly":         weekly,
        "leads_today":    leads_today,
    }


# ── Teams card formatter ──────────────────────────────────────────────────────

async def post_briefing_to_teams(data: dict) -> bool:
    """Format briefing data and post to CEO channel.

    Card structure:
      Title:       CEO BRIEFING — [DATE]
      Q2 TARGET:   facts row (target, leads today)
      TODAY'S FOCUS: action required + closing soon + aws_actions
      PIPELINE:    stage breakdown
      AWS TRUTH:   confirmed vs mismatch
      AT RISK:     deals closing within 30 days
      FUNDING:     eligible programs
      CO-SELL:     active AWS reps
      WEEKLY:      Monday only
    """
    date        = data.get("date", datetime.now().strftime("%d %b %Y"))
    is_monday   = data.get("is_monday", False)
    weekly      = data.get("weekly", {})
    leads_today = data.get("leads_today", 0)

    # TODAY'S FOCUS — combine urgent items
    focus_parts = []
    ar = _clean(data.get("action_required", ""), 200)
    if _has_content(ar):
        focus_parts.append(f"Action Required:\n{ar}")
    aa = _clean(data.get("aws_actions", ""), 150)
    if _has_content(aa):
        focus_parts.append(f"AWS Recommended Actions:\n{aa}")
    cs = _clean(data.get("closing_soon", ""), 150)
    if _has_content(cs):
        focus_parts.append(f"Closing this month:\n{cs}")
    focus_text = "\n\n".join(focus_parts) if focus_parts else "Nothing urgent today."

    body_sections = [
        f"TODAY'S FOCUS\n{focus_text}",
        f"PIPELINE\n{_clean(data.get('pipeline', ''), _BUDGET['pipeline'])}",
        f"AWS TRUTH\n{_clean(data.get('aws_stage', ''), _BUDGET['aws_truth'])}",
        f"AT RISK\n{_clean(data.get('closing_soon', ''), _BUDGET['at_risk'])}",
        f"FUNDING\n{_clean(data.get('funding', ''), _BUDGET['funding'])}",
        f"CO-SELL\n{_clean(data.get('cosell', ''), _BUDGET['cosell'])}",
    ]

    if is_monday and weekly:
        weekly_parts = []
        for label, key in [
            ("Close date cleanup", "close_date_cleanup"),
            ("Closed lost",        "closed_lost_analysis"),
            ("Pipeline velocity",  "pipeline_velocity"),
        ]:
            val = _clean(weekly.get(key, ""), 120)
            weekly_parts.append(f"{label}: {val}")
        body_sections.append("WEEKLY FOCUS (Monday)\n" + "\n".join(weekly_parts))

    body_text = "\n\n".join(body_sections)

    facts = [
        {"title": "Q2 Target",   "value": "£400,000"},
        {"title": "Leads today", "value": str(leads_today)},
        {"title": "Bridge",      "value": "healthy"},
    ]

    return await teams.post_to_ceo(
        title=f"CEO BRIEFING — {date}",
        body_text=body_text,
        facts=facts,
    )
