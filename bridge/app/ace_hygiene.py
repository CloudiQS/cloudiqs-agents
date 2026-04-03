"""
ACE Hygiene — weekly pipeline health check for Partner Central.

Runs every Monday at 06:00 via POST /ace/hygiene (called by ace-hygiene agent).
Makes 6 Partner Central MCP queries in PARALLEL (asyncio.gather) covering
action required, stale launched deals, funding eligibility, AWS stage truth,
past close dates, and active co-sell reps.

MCP queries use structured pipe-delimited format so the response is parseable
data, not conversational AI text. Any commentary lines are stripped.

Produces:
  - A health score (0-10) based on pipeline signals
  - A prioritised action plan (ordered by urgency)
  - A Teams card posted to the ACE channel (bold text headers, no coloured
    Container backgrounds)

GET /ace/hygiene returns the same structured data as JSON without posting.

Exported:
  run_hygiene()              -> dict with all sections + health_score + action_plan
  post_hygiene_to_teams(data) -> bool
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from app import teams
from app.mcp_parser import parse_mcp_response, parse_pipe_rows

logger = logging.getLogger("bridge")

# ── Section schemas ───────────────────────────────────────────────────────────
# Each entry defines:
#   fields   - number of pipe-delimited fields expected per row
#   fmt      - format string for display (uses {0}, {1}, ... positional refs)
#   label    - section heading shown in Teams card

_SECTION_SCHEMA: dict[str, dict] = {
    "action_required": {
        "fields": 4,
        "fmt":    "{0} | {1} | {2} | {3}d left",
        "label":  "ACTION REQUIRED",
    },
    "stale_launched": {
        "fields": 4,
        "fmt":    "{0} | {1} | {2} | {3} days stale",
        "label":  "STALE LAUNCHED DEALS",
    },
    "funding_eligible": {
        "fields": 3,
        "fmt":    "{0} | {1} | {2}",
        "label":  "FUNDING ELIGIBLE",
    },
    "aws_stage": {
        "fields": 4,
        "fmt":    "{0} | {1} | AWS: {2} → Partner: {3}",
        "label":  "AWS STAGE ALIGNMENT",
    },
    "past_close_dates": {
        "fields": 3,
        "fmt":    "{0} | {1} | {2}",
        "label":  "PAST CLOSE DATES",
    },
    "cosell": {
        "fields": 4,
        "fmt":    "{0} | {1} | Rep: {2} | {3}",
        "label":  "CO-SELL ACTIVE",
    },
    "pipeline": {
        "fields": 3,
        "fmt":    "{0}: {1} deals (£{2})",
        "label":  "PIPELINE BY STAGE",
    },
}

# ── MCP query strings ─────────────────────────────────────────────────────────
# Each query ends with an explicit instruction to return ONLY pipe-delimited
# rows in the stated format — no commentary, no greetings, no explanations.

_QUERIES: dict[str, str] = {
    "action_required": (
        "List all opportunities with Action Required status. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | ISSUE | DAYS_REMAINING\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "stale_launched": (
        "List Launched opportunities not updated in 30 or more days. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | REVENUE | DAYS_STALE\n"
        "Maximum 10 results, sorted by days stale descending. "
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "funding_eligible": (
        "Which opportunities at Business Validation, Technical Validation, or Committed stage "
        "are eligible for MAP, POC credits, or CEI funding? "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | PROGRAM\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "aws_stage": (
        "List open opportunities where the AWS stage and my partner stage do not match. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | AWS_STAGE | PARTNER_STAGE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "past_close_dates": (
        "List open opportunities whose target close date is in the past. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | CLOSE_DATE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "cosell": (
        "List open opportunities with an active AWS Sales Rep engaged in co-sell. "
        "Respond with ONLY this format, one opportunity per line:\n"
        "OPP_ID | COMPANY | REP_NAME | REVENUE\n"
        "Maximum 5 results, sorted by revenue descending. "
        "Do not include headers, explanations, greetings, or any other text."
    ),
    "pipeline": (
        "List all open opportunities by stage with count and total expected revenue. "
        "Respond with ONLY this format, one stage per line:\n"
        "STAGE_NAME | COUNT | TOTAL_REVENUE\n"
        "Do not include headers, explanations, greetings, or any other text."
    ),
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_EMPTY_SENTINELS = ("None found", "Query failed", "No data available")


def _has_content(text: str) -> bool:
    """True if section has real pipe-row data (not a failure/empty placeholder)."""
    if not text:
        return False
    return not any(text.startswith(p) for p in _EMPTY_SENTINELS)


def _count_items(text: str) -> int:
    """Count pipe-delimited data rows in a section string.

    Returns 0 for empty/sentinel strings, otherwise counts lines
    that contain at least one pipe character (i.e. look like data rows).
    """
    if not text or any(text.startswith(p) for p in _EMPTY_SENTINELS):
        return 0
    return sum(1 for line in text.split("\n") if "|" in line and line.strip())


def _parse_section(raw: str, section_key: str) -> str:
    """Parse pipe-delimited MCP response for one section.

    Calls parse_pipe_rows with the expected field count for this section,
    formats each valid row with the section's format string, and joins
    them with newlines.

    Returns:
        Formatted multi-line string of data rows, or "No data available."
        if zero valid rows were found.  Pass-through for error sentinels.
    """
    if not raw:
        return "No data available."
    if raw.startswith("Query failed"):
        return raw

    schema   = _SECTION_SCHEMA[section_key]
    rows     = parse_pipe_rows(raw, schema["fields"])
    if not rows:
        return "No data available."

    fmt = schema["fmt"]
    return "\n".join(fmt.format(*r) for r in rows[:10])


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

    if _count_items(sections.get("action_required", "")) > 0:
        actions.append("URGENT: Resolve Action Required items in Partner Central immediately.")

    if _count_items(sections.get("past_close_dates", "")) > 0:
        actions.append("HIGH: Update past close dates — deals with stale dates risk being auto-closed by AWS.")

    if _count_items(sections.get("stale_launched", "")) > 0:
        actions.append("HIGH: Review stale Launched deals — mark as Won, Lost, or update activity.")

    if _count_items(sections.get("aws_stage", "")) > 0:
        actions.append("MEDIUM: Align mismatched AWS stages to avoid co-sell scoring penalties.")

    if _count_items(sections.get("funding_eligible", "")) > 0:
        actions.append("MEDIUM: Submit funding applications for eligible opportunities (MAP / POC / CEI).")

    if _count_items(sections.get("cosell", "")) > 0:
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
        return text if text else "No data available."
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
    Each response is parsed for pipe-delimited rows — chatbot commentary
    and narrative lines are discarded. Zero valid rows → "No data available."

    Returns sections, health score (0-10), and prioritised action plan.
    """
    raw_results = await asyncio.gather(
        *[_mcp(q) for q in _QUERIES.values()],
        return_exceptions=True,
    )

    sections: dict = {}
    for key, raw in zip(_QUERIES.keys(), raw_results):
        if isinstance(raw, Exception):
            logger.warning("ace_hygiene_gather_exception", extra={"section": key, "error": str(raw)})
            sections[key] = "Query failed — check MCP connection."
        else:
            sections[key] = _parse_section(raw, key)

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
        "pipeline":         sections.get("pipeline", ""),
    }


# ── Teams card ────────────────────────────────────────────────────────────────

def _build_hygiene_card(data: dict) -> dict:
    """Build the ACE hygiene Adaptive Card.

    Layout:
      ACE HYGIENE — [date]  |  [score]/10 [label]
      PIPELINE HEALTH (factset)
      PIPELINE BY STAGE (stage rows)
      DO THIS TODAY (numbered action items from action_required data)
      ACTION REQUIRED (detail rows)
      STALE DEALS
      FUNDING ELIGIBLE
      CO-SELL ACTIVE
      [Open Partner Central] button

    Bold TextBlock headers. No coloured Container backgrounds.
    """
    from app.ace_cards import _tb, _heading, _sep, _factset, _wrap_card, _header_tb, _action

    date         = data.get("date", datetime.now().strftime("%d %b %Y"))
    health_score = int(data.get("health_score") or 0)
    health_label = data.get("health_label", "POOR")
    action_plan  = data.get("action_plan") or []

    color = "good" if health_score >= 8 else ("warning" if health_score >= 5 else "attention")

    body: list[dict] = []

    # ── Header ────────────────────────────────────────────────────────────────
    body.append(_header_tb(
        f"● ACE HYGIENE — {date}  |  {health_score}/10 {health_label}",
        color=color,
    ))

    # ── Pipeline health score ─────────────────────────────────────────────────
    body.append(_sep())
    body.append(_factset([
        {"title": "Pipeline Health", "value": f"{health_score}/10 ({health_label})"},
        {"title": "Run Date",        "value": date},
    ]))

    # ── Pipeline by stage ─────────────────────────────────────────────────────
    pipeline = data.get("pipeline", "")
    if _has_content(pipeline):
        body.append(_sep())
        body.append(_heading("PIPELINE BY STAGE"))
        for row in [r for r in pipeline.split("\n") if r.strip()][:8]:
            body.append(_tb(row, spacing="none"))

    # ── DO THIS TODAY (top action_required items as numbered list) ────────────
    ar_text = data.get("action_required", "")
    ar_rows = [r for r in ar_text.split("\n") if r.strip() and "|" in r]
    if ar_rows or action_plan:
        body.append(_sep())
        body.append(_heading("DO THIS TODAY"))
        if ar_rows:
            # ar_rows are already formatted as "OPP_ID | COMPANY | ISSUE | Xd left"
            for i, row in enumerate(ar_rows[:5], 1):
                body.append(_tb(f"{i}. {row}", spacing="none" if i > 1 else "small"))
        else:
            # Fall back to generic action plan items
            for i, action in enumerate(action_plan[:5], 1):
                body.append(_tb(f"{i}. {action}", spacing="none" if i > 1 else "small"))

    # ── Detail sections ───────────────────────────────────────────────────────
    section_order = [
        ("action_required",  "ACTION REQUIRED"),
        ("stale_launched",   "STALE DEALS"),
        ("funding_eligible", "FUNDING ELIGIBLE"),
        ("past_close_dates", "PAST CLOSE DATES"),
        ("cosell",           "CO-SELL ACTIVE"),
        ("aws_stage",        "AWS STAGE MISALIGNED"),
    ]
    for section_key, label in section_order:
        text = data.get(section_key, "")
        if not _has_content(text):
            continue
        rows = [r for r in text.split("\n") if r.strip()]
        count = len(rows)
        body.append(_sep())
        body.append(_heading(f"{label} ({count})" if count > 1 else label))
        for row in rows[:5]:
            body.append(_tb(row, spacing="none"))

    # ── Open Partner Central button ───────────────────────────────────────────
    actions = [_action("Open Partner Central",
                       "https://partnercentral.awspartner.com/opportunities")]

    return _wrap_card(body, actions)


async def post_hygiene_to_teams(data: dict) -> bool:
    """Build the hygiene card and post to the ACE channel."""
    card = _build_hygiene_card(data)
    webhook_key = teams._resolve_webhook("teams/ace-webhook-url")
    ok = await teams._post_raw(card, webhook_key)
    if not ok:
        # Fallback: plain text summary that every Teams webhook variant accepts
        date         = data.get("date", "")
        health_score = data.get("health_score", 0)
        health_label = data.get("health_label", "POOR")
        action_plan  = data.get("action_plan") or []
        title        = f"ACE HYGIENE — {date}  |  {health_score}/10 {health_label}"
        body_text    = "\n".join(f"- {a}" for a in action_plan)
        simple = teams._build_simple(title, body_text)
        ok = await teams._post_raw(simple, webhook_key)
    return ok
