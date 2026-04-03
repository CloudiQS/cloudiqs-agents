"""
ACE Funding Checker — identify and report funding opportunities.

Runs on demand via POST /ace/funding-check (called by ace-funding agent).
Makes 3 Partner Central MCP queries in PARALLEL covering funding eligibility,
active funding applications, and available funding programs.

All queries use structured pipe-delimited or key:value format so responses
are parseable data, not conversational AI text.

Q2 2026 goal: claim the $60K quarterly funding wallet.

Exported:
  run_funding_check()           -> dict with eligible, active, programs, summary
  post_funding_to_teams(data)   -> bool
"""

import asyncio
import logging
from datetime import datetime

from app import teams
from app.mcp_parser import parse_mcp_response, parse_pipe_rows, parse_structured, strip_narrative, truncate

logger = logging.getLogger("bridge")

_BUDGET = {
    "eligible":  500,
    "active":    300,
    "programs":  300,
}

FUNDING_PROGRAMS = ["MAP", "POC", "CEI", "MPOPP", "Partner Launch Initiative"]

# Pipe section schemas
_PIPE_SECTIONS: dict[str, dict] = {
    "eligible": {"fields": 4, "fmt": "{0} | {1} | {2} | {3}"},
    "active":   {"fields": 4, "fmt": "{0} | {1} | {2} | Status: {3}"},
}


def _clean(text: str, budget: int) -> str:
    cleaned = strip_narrative(text) if text else ""
    if not cleaned:
        return "None found."
    return truncate(cleaned, budget)


def _count_eligible(text: str) -> int:
    if not text or text.startswith("None found") or text.startswith("Query failed") or text.startswith("No data"):
        return 0
    return sum(1 for line in text.split("\n") if line.strip() and not line.lower().startswith(("here", "no ", "none")))


def _fmt_pipe(raw: str, key: str) -> str:
    """Parse pipe-delimited MCP response for one funding section."""
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
    return "\n".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in parsed.items())


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

    All queries use structured formats — pipe-delimited for opportunity lists,
    key:value for program details — so MCP cannot inject conversational text.

    Returns sections, eligible count, and action summary.
    """
    queries = {
        "eligible": (
            "List all open opportunities at Business Validation, Technical Validation, "
            "or Committed stage eligible for MAP, POC credits, CEI, or MPOPP funding. "
            "Respond with ONLY this format, one opportunity per line:\n"
            "OPP_ID | COMPANY | PROGRAM | AMOUNT\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "active": (
            "List all currently active or pending funding applications. "
            "Respond with ONLY this format, one application per line:\n"
            "OPP_ID | COMPANY | PROGRAM | STATUS\n"
            "Do not include headers, explanations, greetings, or any other text."
        ),
        "programs": (
            "List available MAP, POC, CEI, and MPOPP funding programs. "
            "Respond with ONLY this format, one program per line:\n"
            "PROGRAM_NAME: MAXIMUM_AMOUNT\n"
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
            logger.warning("ace_funding_gather_exception", extra={"section": key, "error": str(raw)})
            sections[key] = "Query failed — check MCP connection."
        elif key in _PIPE_SECTIONS:
            sections[key] = _fmt_pipe(raw, key)
        else:
            sections[key] = _fmt_kv(raw)

    eligible_count = _count_eligible(sections.get("eligible", ""))

    action_items: list[str] = []
    if eligible_count > 0:
        action_items.append(
            f"Submit funding applications for {eligible_count} eligible "
            "opportunities. Target: full $60K quarterly wallet."
        )
    active_text = sections.get("active", "")
    if active_text and not active_text.startswith("None") and not active_text.startswith("No data"):
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
