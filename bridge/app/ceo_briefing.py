"""
CEO Briefing — AWS Stage Alignment tracking.

Queries Partner Central MCP for the real AWS-confirmed stage of our
'Launched' opportunities and compares against what we self-report.

Exported functions:
  run_aws_stage_alignment()   -> dict with counts and text
  post_briefing_to_teams()    -> posts MessageCard to Teams (CEO channel)
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Optional

from app import teams

logger = logging.getLogger("bridge")

# RAG thresholds — adjust as pipeline matures
_GREEN_THRESHOLD = 0.80   # >= 80% alignment = GREEN
_AMBER_THRESHOLD = 0.60   # 60–79% = AMBER, < 60% = RED


def _colour(rate: Optional[float]) -> str:
    """Return GREEN / AMBER / RED indicator based on alignment rate."""
    if rate is None:
        return "UNKNOWN"
    if rate >= _GREEN_THRESHOLD:
        return "GREEN"
    if rate >= _AMBER_THRESHOLD:
        return "AMBER"
    return "RED"


def _theme_colour(label: str) -> str:
    """Map colour label to Office 365 MessageCard hex theme colour."""
    return {"GREEN": "00B050", "AMBER": "FFC000", "RED": "C00000"}.get(label, "888888")


def _try_parse_counts(text: str) -> dict:
    """Best-effort extraction of stage counts from MCP free-text response.

    Returns dict with keys: total, aws_launched, closed_lost, empty.
    All values default to None if not extractable.
    """
    def find_int(pattern: str) -> Optional[int]:
        m = re.search(pattern, text, re.IGNORECASE)
        return int(m.group(1)) if m else None

    return {
        "total": find_int(r"(\d+)\s+(?:total\s+)?launched"),
        "aws_launched": find_int(
            r"(\d+)\s+.*?(?:aws[- ]?launched|confirmed.*?launched|aws.*?stage.*?launched)"
        ),
        "closed_lost": find_int(r"(\d+)\s+.*?closed.?lost"),
        "empty": find_int(
            r"(\d+)\s+.*?(?:empty|not\s+set|missing|no\s+aws\s+stage)"
        ),
    }


def _extract_assistant_text(result) -> str:
    """Extract readable text from a send_message response.

    result['text'] is a JSON string whose structure is:
      {
        "content": [
          {"type": "ASSISTANT_RESPONSE", "content": {"text": "..."}},
          {"type": "serverToolResult", "content": {...}},
          ...
        ]
      }

    Collects all ASSISTANT_RESPONSE text blocks and joins them.
    Falls back to the raw 'text' value if json.loads fails.
    """
    if not result:
        return "No data returned."
    raw = result.get("text", "")
    if not raw:
        return "No data returned."
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
        return raw.strip() or "No data returned."


async def run_aws_stage_alignment() -> dict:
    """Query MCP for AWS stage breakdown of Launched opportunities.

    Returns:
        {
          "text":           full MCP response text,
          "total":          total Launched count (int or None),
          "aws_launched":   AWS-confirmed Launched (int or None),
          "closed_lost":    AWS Closed Lost (int or None),
          "empty":          no AWS stage set (int or None),
          "alignment_rate": float 0–1 (or None if counts unavailable),
          "colour":         "GREEN" | "AMBER" | "RED" | "UNKNOWN",
        }
    """
    from app import mcp_client

    query = (
        "For my Launched opportunities, show how many have AWS stage as Launched "
        "versus Closed Lost versus empty (no AWS stage set). "
        "Give me the exact counts and a percentage breakdown."
    )

    try:
        result = await mcp_client.send_message(query, catalog="AWS")
    except Exception as exc:
        logger.warning("ceo_briefing_mcp_failed", extra={"error": str(exc)})
        return {
            "text": f"MCP query failed: {exc}",
            "total": None,
            "aws_launched": None,
            "closed_lost": None,
            "empty": None,
            "alignment_rate": None,
            "colour": "UNKNOWN",
        }

    # result['text'] is a JSON string containing a content array.
    # Walk the array and collect text from ASSISTANT_RESPONSE blocks.
    text = _extract_assistant_text(result)

    counts = _try_parse_counts(text)
    alignment_rate: Optional[float] = None
    if counts["total"] and counts["aws_launched"] is not None:
        alignment_rate = counts["aws_launched"] / counts["total"]

    colour = _colour(alignment_rate)
    logger.info(
        "ceo_briefing_alignment",
        extra={
            "total": counts["total"],
            "aws_launched": counts["aws_launched"],
            "alignment_rate": alignment_rate,
            "colour": colour,
        },
    )

    return {
        "text": text,
        "total": counts["total"],
        "aws_launched": counts["aws_launched"],
        "closed_lost": counts["closed_lost"],
        "empty": counts["empty"],
        "alignment_rate": alignment_rate,
        "colour": colour,
    }


async def post_briefing_to_teams(alignment: dict) -> bool:
    """Post AWS Stage Alignment card to the Teams CEO briefing channel.

    Colour coding:
      GREEN  (>= 80% alignment) — dark green  #00B050
      AMBER  (60–79%)           — gold        #FFC000
      RED    (< 60%)            — dark red    #C00000
      UNKNOWN                   — grey        #888888
    """
    colour_label = alignment.get("colour", "UNKNOWN")
    theme = _theme_colour(colour_label)
    today = datetime.now().strftime("%d %b %Y")

    rate = alignment.get("alignment_rate")
    rate_str = f"{rate:.0%}" if rate is not None else "N/A"

    # Quarterly scorecard facts
    scorecard_facts: list[dict] = []
    if alignment.get("aws_launched") is not None:
        scorecard_facts.append(
            {"name": "AWS-confirmed Launched", "value": str(alignment["aws_launched"])}
        )
    if alignment.get("total") is not None and alignment.get("aws_launched") is not None:
        mismatch = alignment["total"] - alignment["aws_launched"]
        scorecard_facts.append({"name": "Stage mismatch", "value": str(mismatch)})
    scorecard_facts.append({"name": "True launch rate", "value": rate_str})

    colour_emoji = {"GREEN": "🟢", "AMBER": "🟡", "RED": "🔴"}.get(colour_label, "⚪")
    scorecard_facts.append({"name": "Status", "value": f"{colour_emoji} {colour_label}"})

    card = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": theme,
        "summary": f"CEO Briefing — AWS Stage Alignment {today}",
        "title": f"AWS STAGE ALIGNMENT — {today}",
        "sections": [
            {
                "activityTitle": "Launched Opportunity Stage Breakdown",
                "activityText": alignment.get("text", "No data."),
            },
            {
                "activityTitle": "Quarterly Scorecard",
                "facts": scorecard_facts,
            },
        ],
    }

    # Try dedicated CEO webhook first, fall back to generic channel
    from app.config import get_secret, is_dummy
    ceo_key = get_secret("teams/ceo-webhook-url")
    webhook_key = "teams/ceo-webhook-url" if not is_dummy(ceo_key) else "teams/webhook-url"
    return await teams._post(card, webhook_key=webhook_key)
