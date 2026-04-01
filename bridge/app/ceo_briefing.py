"""
CEO Briefing — comprehensive daily morning briefing for Steve.

Runs at 06:00 every day via POST /ceo/briefing (called by ceo-ops agent).
Makes 6 Partner Central MCP queries in sequence, combines them with
today's HubSpot lead stats, and posts a single structured Teams message.

On Mondays, adds a weekly pipeline hygiene section (3 extra MCP queries).

GET /ceo/briefing returns the same structured data as JSON without posting.

Exported:
  run_briefing(stats)           -> dict with all briefing sections
  post_briefing_to_teams(data)  -> bool
"""

import json
import logging
from datetime import datetime
from typing import Optional

from app import teams

logger = logging.getLogger("bridge")

_MAX_SECTION_CHARS = 800


# ── MCP response parser ───────────────────────────────────────────────────────

def _extract_assistant_text(result) -> str:
    """Extract readable text from a send_message response.

    result['text'] is a JSON string whose structure is:
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


def _trunc(text: str, limit: int = _MAX_SECTION_CHARS) -> str:
    """Truncate text to limit chars, appending ellipsis if cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


async def _mcp(query: str) -> str:
    """Run one Partner Central MCP query. Returns fallback string on failure."""
    from app import mcp_client
    try:
        result = await mcp_client.send_message(query, catalog="AWS")
        text = _extract_assistant_text(result)
        return _trunc(text) if text else "No data returned."
    except Exception as exc:
        logger.warning(
            "ceo_briefing_mcp_query_failed",
            extra={"error": str(exc), "query": query[:60]},
        )
        return "Query failed — check MCP connection."


# ── Main briefing runner ──────────────────────────────────────────────────────

async def run_briefing(stats: Optional[dict] = None) -> dict:
    """Run all CEO briefing queries in sequence and return structured data.

    Args:
        stats: Today's lead stats from bridge _stats dict.

    Returns dict with keys for each Teams card section.
    """
    is_monday = datetime.now().weekday() == 0

    pipeline = await _mcp(
        "Give me a breakdown of all my opportunities by stage "
        "with count and revenue per stage"
    )
    action_required = await _mcp(
        "List all opportunities with Action Required status "
        "with company name and what AWS needs"
    )
    closing_soon = await _mcp(
        "Which opportunities have target close dates within 30 days? "
        "Show company, close date, stage, and what is blocking"
    )
    aws_stage = await _mcp(
        "For my Launched opportunities, how many have AWS stage Launched "
        "versus Closed Lost versus empty?"
    )
    cosell = await _mcp(
        "Which of my open opportunities have AWS Sales Reps actively engaged? "
        "Show top 10 by expected spend with rep name"
    )
    funding = await _mcp(
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

async def post_briefing_to_teams(data: dict) -> bool:
    """Format briefing data as a Teams MessageCard and post it.

    Posts to teams/ceo-webhook-url with fallback to teams/webhook-url.
    """
    date = data.get("date", datetime.now().strftime("%d %b %Y"))
    is_monday = data.get("is_monday", False)
    weekly = data.get("weekly", {})
    leads_today = data.get("leads_today", 0)

    sections = [
        {
            "activityTitle": "TODAY'S ACTIONS",
            "activityText": (
                f"**Action Required (AWS)**\n"
                f"{data.get('action_required', 'None')}\n\n"
                f"**Deals closing this month**\n"
                f"{data.get('closing_soon', 'None')}"
            ),
        },
        {
            "activityTitle": "PIPELINE SCORECARD",
            "activityText": data.get("pipeline", "No data."),
        },
        {
            "activityTitle": "AWS STAGE TRUTH",
            "activityText": data.get("aws_stage", "No data."),
        },
        {
            "activityTitle": "AT RISK THIS MONTH",
            "activityText": data.get("closing_soon", "No data."),
        },
        {
            "activityTitle": "FUNDING OPPORTUNITIES",
            "activityText": data.get("funding", "No data."),
        },
        {
            "activityTitle": "CO-SELL ACTIVE",
            "activityText": data.get("cosell", "No data."),
        },
        {
            "activityTitle": "ENGINE HEALTH",
            "activityText": (
                f"Leads found today: {leads_today}\n"
                f"Bridge: healthy"
            ),
        },
    ]

    if is_monday and weekly:
        sections.append(
            {
                "activityTitle": "WEEKLY FOCUS (Monday)",
                "activityText": (
                    f"**Close date cleanup**\n"
                    f"{weekly.get('close_date_cleanup', 'N/A')}\n\n"
                    f"**Closed lost analysis**\n"
                    f"{weekly.get('closed_lost_analysis', 'N/A')}\n\n"
                    f"**Pipeline velocity**\n"
                    f"{weekly.get('pipeline_velocity', 'N/A')}"
                ),
            }
        )

    card = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": "1F3D7A",
        "summary": f"CloudiQS CEO Briefing {date}",
        "title": f"CLOUDIQS CEO BRIEFING — {date}",
        "sections": sections,
    }

    from app.config import get_secret, is_dummy
    ceo_key = get_secret("teams/ceo-webhook-url")
    webhook_key = "teams/ceo-webhook-url" if not is_dummy(ceo_key) else "teams/webhook-url"
    return await teams._post(card, webhook_key=webhook_key)
