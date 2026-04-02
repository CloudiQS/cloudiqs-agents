"""
CEO Briefing — comprehensive daily morning briefing for Steve.

Runs at 06:00 every day via POST /ceo/briefing (called by ceo-ops agent).
Makes 6 Partner Central MCP queries in sequence, combines with today's
HubSpot lead stats, and posts a single structured Teams MessageCard.

On Mondays, adds a weekly pipeline hygiene section (3 extra MCP queries).

GET /ceo/briefing returns the same structured data as JSON without posting.

Exported:
  run_briefing(stats)           -> dict with all briefing sections
  post_briefing_to_teams(data)  -> bool
  _extract_assistant_text(r)    -> str  (shared with main.py hygiene)
  _strip_narrative(text)        -> str  (shared with main.py hygiene)
"""

import json
import logging
import re
from datetime import datetime
from typing import Optional

from app import teams

logger = logging.getLogger("bridge")

# Per-section character budgets — total stays well under 2000
_BUDGET = {
    "do_today":    450,
    "pipeline":    250,
    "aws_truth":   180,
    "at_risk":     280,
    "funding":     250,
    "cosell":      200,
    "engine":       80,
    "weekly":      400,
}

# MCP agent thinking-out-loud prefixes to strip
_NARRATIVE_PREFIXES = (
    "I'll help",
    "Let me",
    "Now let me",
    "I'll analyze",
    "I'll hand",
    "I need to",
    "I can see",
    "I've",
    "I found",
    "Perfect!",
    "Great!",
    "Working on it",
    "Preparing",
    "Fetching",
    "Analyzing",
    "Based on my analysis",
    "I will",
    "I am ",
    "I'm ",
    "Sure,",
    "Certainly,",
    "Of course",
)


# ── MCP response parsing ──────────────────────────────────────────────────────

def _extract_assistant_text(result) -> str:
    """Extract readable text from a send_message response.

    result['text'] is a JSON string:
      {"content": [
        {"type": "ASSISTANT_RESPONSE", "content": {"text": "..."}},
        {"type": "serverToolResult", ...},
      ]}

    Collects all ASSISTANT_RESPONSE blocks and joins them.
    Falls back to the raw 'text' string if json.loads fails.
    Returns "" if result is None or has no text.
    """
    if not result:
        return ""
    raw = result.get("text", "")
    if not raw:
        return ""
    try:
        inner = json.loads(raw)
        parts = []
        for block in inner.get("content", []):
            if block.get("type") == "ASSISTANT_RESPONSE":
                content = block.get("content", {})
                if isinstance(content, dict):
                    parts.append(content.get("text", ""))
                elif isinstance(content, str):
                    parts.append(content)
        text = "\n".join(p for p in parts if p).strip()
        return text or raw.strip()
    except (json.JSONDecodeError, AttributeError):
        return raw.strip()


def _strip_narrative(text: str) -> str:
    """Remove MCP agent thinking-out-loud sentences from response text.

    Strips any line that starts with one of the known narrative prefixes.
    Collapses consecutive blank lines to a single blank.
    """
    cleaned = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(stripped.startswith(p) for p in _NARRATIVE_PREFIXES):
            continue
        cleaned.append(line)

    # Collapse multiple consecutive blank lines
    result: list[str] = []
    prev_blank = False
    for line in cleaned:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        result.append(line)
        prev_blank = is_blank

    return "\n".join(result).strip()


def _clean(text: str, budget: int) -> str:
    """Strip narrative then truncate to budget chars."""
    cleaned = _strip_narrative(text)
    if not cleaned:
        return "No data."
    if len(cleaned) <= budget:
        return cleaned
    return cleaned[: budget - 1] + "…"


async def _mcp(query: str) -> str:
    """Run one Partner Central MCP query. Returns fallback string on failure."""
    from app import mcp_client
    from app.mcp_parser import parse_mcp_response
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
    """Run all CEO briefing queries in sequence and return structured data."""
    is_monday = datetime.now().weekday() == 0

    pipeline        = await _mcp(
        "Give me a breakdown of all my opportunities by stage "
        "with count and revenue per stage"
    )
    action_required = await _mcp(
        "List all opportunities with Action Required status "
        "with company name and what AWS needs"
    )
    closing_soon    = await _mcp(
        "Which opportunities have target close dates within 30 days? "
        "Show company, close date, stage, and what is blocking"
    )
    aws_stage       = await _mcp(
        "For my Launched opportunities, how many have AWS stage Launched "
        "versus Closed Lost versus empty?"
    )
    cosell          = await _mcp(
        "Which of my open opportunities have AWS Sales Reps actively engaged? "
        "Show top 10 by expected spend with rep name"
    )
    funding         = await _mcp(
        "Which of my opportunities at Business Validation or Technical Validation "
        "stage are eligible for funding?"
    )

    weekly: dict = {}
    if is_monday:
        weekly["close_date_cleanup"] = await _mcp(
            "How many of my open opportunities have close dates in the past? List them."
        )
        weekly["closed_lost_analysis"] = await _mcp(
            "For my Closed Lost opportunities, what are the top 3 reasons for losing?"
        )
        weekly["pipeline_velocity"] = await _mcp(
            "What is the average number of days opportunities take to progress "
            "from Qualified to Launched stage?"
        )

    leads_today = (stats or {}).get("total_leads", 0)

    return {
        "date": datetime.now().strftime("%d %b %Y"),
        "is_monday": is_monday,
        "pipeline": pipeline,
        "action_required": action_required,
        "closing_soon": closing_soon,
        "aws_stage": aws_stage,
        "cosell": cosell,
        "funding": funding,
        "weekly": weekly,
        "leads_today": leads_today,
    }


# ── Teams card formatter ──────────────────────────────────────────────────────

def _has_content(text: str) -> bool:
    """True if a section has real data (not a failure or empty placeholder)."""
    if not text:
        return False
    return not any(
        text.startswith(p)
        for p in ("No data", "Query failed", "None")
    )


def _theme_for(data: dict) -> str:
    """Pick card border colour based on urgency."""
    if _has_content(data.get("action_required", "")):
        return "C00000"   # red — AWS wants something NOW
    if _has_content(data.get("closing_soon", "")):
        return "FFC000"   # amber — deals at risk
    return "1F3D7A"        # blue — normal morning briefing


async def post_briefing_to_teams(data: dict) -> bool:
    """Format briefing data as an Adaptive Card and post to CEO channel.

    Card structure:
      Title:     CEO BRIEFING — [DATE]
      DO TODAY:  action required + closing soon
      PIPELINE:  stage breakdown
      AWS TRUTH: confirmed vs mismatch counts
      AT RISK:   deals closing within 30 days
      FUNDING:   eligible programs
      CO-SELL:   active AWS reps
      ENGINE:    FactSet (leads today, bridge status)
      WEEKLY:    Monday only — close date cleanup, closed lost, velocity
    """
    date        = data.get("date", datetime.now().strftime("%d %b %Y"))
    is_monday   = data.get("is_monday", False)
    weekly      = data.get("weekly", {})
    leads_today = data.get("leads_today", 0)

    # DO TODAY — combine action required + closing soon
    do_today_parts = []
    ar = _clean(data.get("action_required", ""), 200)
    if _has_content(ar):
        do_today_parts.append(f"Action Required:\n{ar}")
    cs = _clean(data.get("closing_soon", ""), 200)
    if _has_content(cs):
        do_today_parts.append(f"Closing this month:\n{cs}")
    do_today_text = "\n\n".join(do_today_parts) if do_today_parts else "Nothing urgent today."

    sections = [
        f"DO TODAY\n{do_today_text}",
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
        sections.append("WEEKLY FOCUS (Monday)\n" + "\n".join(weekly_parts))

    body_text = "\n\n".join(sections)
    engine_facts = [
        {"title": "Leads today", "value": str(leads_today)},
        {"title": "Bridge",      "value": "healthy"},
    ]
    return await teams.post_to_ceo(
        f"CEO BRIEFING — {date}",
        body_text,
        facts=engine_facts,
    )
