"""
ACE Funding Checker — identify and report funding opportunities.

Runs on demand via POST /ace/funding-check (called by ace-funding agent).
Makes 3 Partner Central MCP queries in PARALLEL covering funding eligibility,
active funding applications, and available funding programs.

Q2 2026 goal: claim the $60K quarterly funding wallet.

Exported:
  run_funding_check()           -> dict with eligible, active, programs, summary
  post_funding_to_teams(data)   -> bool
"""

import asyncio
import logging
from datetime import datetime

from app import teams
from app.mcp_parser import parse_mcp_response, strip_narrative, truncate

logger = logging.getLogger("bridge")

_BUDGET = {
    "eligible":  500,
    "active":    300,
    "programs":  300,
}

FUNDING_PROGRAMS = ["MAP", "POC", "CEI", "MPOPP", "Partner Launch Initiative"]


def _clean(text: str, budget: int) -> str:
    cleaned = strip_narrative(text) if text else ""
    if not cleaned:
        return "None found."
    return truncate(cleaned, budget)


def _count_eligible(text: str) -> int:
    if not text or text.startswith("None found") or text.startswith("Query failed"):
        return 0
    return sum(1 for l in text.split("\n") if l.strip() and not l.lower().startswith(("here", "no ", "none")))


async def _mcp(query: str) -> str:
    from app import mcp_client
    try:
        result = await mcp_client.send_message(query, catalog="AWS")
        text = parse_mcp_response(result)
        return text if text else "None found."
    except Exception as exc:
        logger.warning("ace_funding_mcp_failed", extra={"error": str(exc), "query": query[:60]})
        return "Query failed — check MCP connection."


async def run_funding_check() -> dict:
    """Run all 3 funding MCP queries in PARALLEL.

    Returns sections, eligible count, and action summary.
    """
    queries = {
        "eligible": (
            "Which of my open opportunities at Business Validation, Technical Validation, "
            "or Committed stage are eligible for MAP, POC credits, CEI, or MPOPP funding? "
            "For each show opportunity ID, company name, program name, and estimated amount."
        ),
        "active": (
            "List all my currently active or pending funding applications. "
            "Show opportunity ID, company name, program, status, and amount. Be brief."
        ),
        "programs": (
            "What MAP, POC, CEI, and MPOPP funding programs are available to me as a partner? "
            "Show program name, eligibility criteria, and maximum funding amount. Be brief."
        ),
    }

    raw_results = await asyncio.gather(
        *[_mcp(q) for q in queries.values()],
        return_exceptions=True,
    )

    sections: dict = {}
    for key, raw in zip(queries.keys(), raw_results):
        if isinstance(raw, Exception):
            logger.warning("ace_funding_gather_exception", extra={"section": key, "error": str(raw)})
            sections[key] = "Query failed — check MCP connection."
        else:
            sections[key] = raw

    eligible_count = _count_eligible(sections.get("eligible", ""))

    action_items: list[str] = []
    if eligible_count > 0:
        action_items.append(
            f"Submit funding applications for {eligible_count} eligible "
            "opportunities. Target: full $60K quarterly wallet."
        )
    active_text = sections.get("active", "")
    if active_text and not active_text.startswith("None"):
        action_items.append("Follow up on active applications — ensure all required documents are uploaded.")
    if not action_items:
        action_items.append("No eligible opportunities found. Check stage advancement for pipeline deals.")

    return {
        "date":            datetime.now().strftime("%d %b %Y"),
        "eligible":        sections.get("eligible", ""),
        "active":          sections.get("active", ""),
        "programs":        sections.get("programs", ""),
        "eligible_count":  eligible_count,
        "action_items":    action_items,
    }


async def post_funding_to_teams(data: dict) -> bool:
    """Post funding check summary to ACE Teams channel."""
    date           = data.get("date", datetime.now().strftime("%d %b %Y"))
    eligible_count = data.get("eligible_count", 0)
    action_items   = data.get("action_items", [])

    action_text = "\n".join(f"- {a}" for a in action_items)

    body_sections = [
        f"ACTIONS\n{action_text}",
        f"ELIGIBLE OPPORTUNITIES\n{_clean(data.get('eligible', ''), _BUDGET['eligible'])}",
        f"ACTIVE APPLICATIONS\n{_clean(data.get('active', ''), _BUDGET['active'])}",
        f"AVAILABLE PROGRAMS\n{_clean(data.get('programs', ''), _BUDGET['programs'])}",
    ]
    body_text = "\n\n".join(body_sections)

    facts = [
        {"title": "Eligible opportunities", "value": str(eligible_count)},
        {"title": "Q2 funding target",      "value": "$60K quarterly wallet"},
        {"title": "Date",                   "value": date},
    ]

    return await teams.post_to_ace(
        title=f"ACE FUNDING — {date} | {eligible_count} eligible",
        body_text=body_text,
        facts=facts,
    )
