"""
ACE Hygiene — weekly pipeline health check for Partner Central.

Runs every Monday at 06:00 via POST /ace/hygiene (called by ace-hygiene agent).
Makes 6 Partner Central MCP queries in PARALLEL (asyncio.gather) covering
action required, stale launched deals, funding eligibility, AWS stage truth,
past close dates, and active co-sell reps.

Produces:
  - A health score (0-10) based on pipeline signals
  - A prioritised action plan (ordered by urgency)
  - A Teams card posted to the ACE channel

GET /ace/hygiene returns the same structured data as JSON without posting.

Exported:
  run_hygiene()              -> dict with all sections + health_score + action_plan
  post_hygiene_to_teams(data) -> bool
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from app import teams
from app.mcp_parser import parse_mcp_response, strip_narrative, truncate

logger = logging.getLogger("bridge")

# Per-section character budgets
_BUDGET = {
    "action_required":  400,
    "stale_launched":   300,
    "funding_eligible": 300,
    "aws_stage":        250,
    "past_close_dates": 250,
    "cosell":           200,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean(text: str, budget: int) -> str:
    cleaned = strip_narrative(text) if text else ""
    if not cleaned:
        return "None found."
    return truncate(cleaned, budget)


def _has_content(text: str) -> bool:
    """True if section has real data (not a failure/empty placeholder)."""
    if not text:
        return False
    return not any(text.startswith(p) for p in ("None found", "Query failed", "No data"))


def _count_items(text: str) -> int:
    """Rough count of bullet/numbered items or lines with content."""
    if not text or text.startswith("None found") or text.startswith("Query failed"):
        return 0
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    # Count lines that look like list items or data rows
    return sum(
        1 for l in lines
        if l and not l.lower().startswith(("here", "the following", "i found", "no "))
    )


def _compute_health_score(sections: dict) -> int:
    """Compute pipeline health score 0-10.

    Deductions:
      -2 per Action Required item (capped at -4)
      -2 if stale Launched deals exist
      -1 per misaligned AWS stage item (capped at -2)
      -1 per past close date item (capped at -2)

    Bonuses:
      +1 if active co-sell reps found
    """
    score = 10

    ar_count  = _count_items(sections.get("action_required", ""))
    stale     = _count_items(sections.get("stale_launched", ""))
    stage_mis = _count_items(sections.get("aws_stage", ""))
    past_cd   = _count_items(sections.get("past_close_dates", ""))
    cosell    = _count_items(sections.get("cosell", ""))

    score -= min(ar_count * 2, 4)   # max -4
    if stale > 0:
        score -= 2
    score -= min(stage_mis, 2)       # max -2
    score -= min(past_cd, 2)         # max -2

    if cosell > 0:
        score += 1

    return max(0, min(10, score))


def _score_label(score: int) -> str:
    if score >= 8:
        return "GOOD"
    if score >= 5:
        return "FAIR"
    return "POOR"


def _build_action_plan(sections: dict) -> list[str]:
    """Return prioritised list of actions, most urgent first."""
    actions: list[str] = []

    ar = sections.get("action_required", "")
    if _count_items(ar) > 0:
        actions.append("URGENT: Resolve Action Required items in Partner Central immediately.")

    past = sections.get("past_close_dates", "")
    if _count_items(past) > 0:
        actions.append("HIGH: Update past close dates — deals with stale dates risk being auto-closed by AWS.")

    stale = sections.get("stale_launched", "")
    if _count_items(stale) > 0:
        actions.append("HIGH: Review stale Launched deals — mark as Won, Lost, or update activity.")

    aws = sections.get("aws_stage", "")
    if _count_items(aws) > 0:
        actions.append("MEDIUM: Align mismatched AWS stages to avoid co-sell scoring penalties.")

    funding = sections.get("funding_eligible", "")
    if _count_items(funding) > 0:
        actions.append("MEDIUM: Submit funding applications for eligible opportunities (MAP / POC / CEI).")

    cosell = sections.get("cosell", "")
    if _count_items(cosell) > 0:
        actions.append("INFO: Follow up with active AWS co-sell reps to advance joint opportunities.")

    if not actions:
        actions.append("Pipeline is clean. No immediate actions required.")

    return actions


async def _mcp(query: str) -> str:
    """Run one Partner Central MCP query. Returns fallback string on failure."""
    from app import mcp_client
    try:
        result = await mcp_client.send_message(query, catalog="AWS")
        text = parse_mcp_response(result)
        return text if text else "None found."
    except Exception as exc:
        logger.warning(
            "ace_hygiene_mcp_query_failed",
            extra={"error": str(exc), "query": query[:60]},
        )
        return "Query failed — check MCP connection."


# ── Main hygiene runner ───────────────────────────────────────────────────────

async def run_hygiene() -> dict:
    """Run all 6 ACE hygiene queries in PARALLEL and return structured data.

    6 queries run simultaneously via asyncio.gather.
    Returns sections, health score (0-10), and prioritised action plan.
    """
    queries = {
        "action_required": (
            "List all opportunities with Action Required status. "
            "For each show opportunity ID, company name, and what action AWS needs. Be brief."
        ),
        "stale_launched": (
            "List all Launched opportunities that have not been updated in the last 30 days. "
            "Show opportunity ID, company name, and last update date."
        ),
        "funding_eligible": (
            "Which of my opportunities at Business Validation, Technical Validation, or Committed stage "
            "are eligible for MAP, POC credits, or CEI funding? "
            "Show opportunity ID, company name, and program name."
        ),
        "aws_stage": (
            "For my open opportunities, how many have a mismatch between the AWS stage and my partner stage? "
            "List the mismatched ones with opportunity ID, company name, AWS stage, and my stage."
        ),
        "past_close_dates": (
            "Which of my open opportunities have target close dates that are in the past? "
            "List opportunity ID, company name, and close date."
        ),
        "cosell": (
            "Which of my open opportunities have an active AWS Sales Rep engaged in co-sell? "
            "Show top 5 by expected revenue with rep name. Be brief."
        ),
    }

    raw_results = await asyncio.gather(
        *[_mcp(q) for q in queries.values()],
        return_exceptions=True,
    )

    sections: dict = {}
    for key, raw in zip(queries.keys(), raw_results):
        if isinstance(raw, Exception):
            logger.warning("ace_hygiene_gather_exception", extra={"section": key, "error": str(raw)})
            sections[key] = "Query failed — check MCP connection."
        else:
            sections[key] = raw

    health_score = _compute_health_score(sections)
    action_plan  = _build_action_plan(sections)

    return {
        "date":             datetime.now().strftime("%d %b %Y"),
        "health_score":     health_score,
        "health_label":     _score_label(health_score),
        "action_plan":      action_plan,
        "action_required":  sections.get("action_required", ""),
        "stale_launched":   sections.get("stale_launched", ""),
        "funding_eligible": sections.get("funding_eligible", ""),
        "aws_stage":        sections.get("aws_stage", ""),
        "past_close_dates": sections.get("past_close_dates", ""),
        "cosell":           sections.get("cosell", ""),
    }


# ── Teams card formatter ──────────────────────────────────────────────────────

async def post_hygiene_to_teams(data: dict) -> bool:
    """Format hygiene data and post to ACE channel via teams.post_to_ace."""
    date         = data.get("date", datetime.now().strftime("%d %b %Y"))
    health_score = data.get("health_score", 0)
    health_label = data.get("health_label", "POOR")
    action_plan  = data.get("action_plan", [])

    title = f"ACE HYGIENE — {date} | {health_score}/10 {health_label}"

    facts = [
        {"title": "Health score", "value": f"{health_score}/10 ({health_label})"},
        {"title": "Date",         "value": date},
    ]

    body_parts = []

    action_plan_text = "\n".join(f"- {a}" for a in action_plan) if action_plan else "No actions needed."
    body_parts.append(f"ACTION PLAN:\n{action_plan_text}")

    ar_text = _clean(data.get("action_required", ""), _BUDGET["action_required"])
    if _has_content(ar_text):
        body_parts.append(f"ACTION REQUIRED:\n{ar_text}")

    stale_text = _clean(data.get("stale_launched", ""), _BUDGET["stale_launched"])
    if _has_content(stale_text):
        body_parts.append(f"STALE LAUNCHED DEALS:\n{stale_text}")

    funding_text = _clean(data.get("funding_eligible", ""), _BUDGET["funding_eligible"])
    if _has_content(funding_text):
        body_parts.append(f"FUNDING ELIGIBLE:\n{funding_text}")

    aws_text = _clean(data.get("aws_stage", ""), _BUDGET["aws_stage"])
    if _has_content(aws_text):
        body_parts.append(f"AWS STAGE ALIGNMENT:\n{aws_text}")

    past_text = _clean(data.get("past_close_dates", ""), _BUDGET["past_close_dates"])
    if _has_content(past_text):
        body_parts.append(f"PAST CLOSE DATES:\n{past_text}")

    cosell_text = _clean(data.get("cosell", ""), _BUDGET["cosell"])
    if _has_content(cosell_text):
        body_parts.append(f"CO-SELL ACTIVE:\n{cosell_text}")

    body_text = "\n\n".join(body_parts)

    return await teams.post_to_ace(title, body_text, facts=facts)
