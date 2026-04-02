"""
ACE-HubSpot Stage Sync — one-way ACE → HubSpot sync.

Runs every 2 hours via POST /ace/sync (called by ace-sync agent).
Reads the current stage from ACE Partner Central and updates HubSpot
when ACE is ahead of HubSpot. NEVER updates ACE from HubSpot.

Stage mapping (ACE → HubSpot):
  Prospect           -> appointmentscheduled
  Qualified          -> qualifiedtobuy
  Technical Validation -> presentationscheduled
  Business Validation -> decisionmakerboughtin
  Committed          -> contractsent
  Launched           -> closedwon
  Closed Lost        -> closedlost

GET /ace/sync returns the same structured data as JSON without posting.

Exported:
  run_sync()                 -> dict with synced, skipped, failed, updates list
  post_sync_to_teams(data)   -> bool
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from app import hubspot, teams

logger = logging.getLogger("bridge")

# ACE stage → HubSpot deal stage ID
ACE_TO_HUBSPOT: dict[str, str] = {
    "Prospect":             "appointmentscheduled",
    "Qualified":            "qualifiedtobuy",
    "Technical Validation": "presentationscheduled",
    "Business Validation":  "decisionmakerboughtin",
    "Committed":            "contractsent",
    "Launched":             "closedwon",
    "Closed Lost":          "closedlost",
}

# Stages we do NOT backtrack HubSpot to (ACE sometimes lags behind)
# If ACE shows an earlier stage than HubSpot, skip the update.
_STAGE_ORDER = [
    "appointmentscheduled",
    "qualifiedtobuy",
    "presentationscheduled",
    "decisionmakerboughtin",
    "contractsent",
    "closedwon",
    "closedlost",
]


def _stage_rank(hs_stage: str) -> int:
    try:
        return _STAGE_ORDER.index(hs_stage)
    except ValueError:
        return -1


async def _sync_one(deal: dict) -> dict:
    """Sync a single deal. Returns a result dict."""
    from app import ace as ace_module

    deal_id   = deal.get("deal_id", "")
    opp_id    = deal.get("ace_opportunity_id", "")
    hs_stage  = deal.get("dealstage", "")
    deal_name = deal.get("dealname", opp_id)

    if not opp_id:
        return {"deal_id": deal_id, "status": "skipped", "reason": "no ace_opportunity_id"}

    ace_stage = await ace_module.get_opportunity_stage(opp_id)
    if not ace_stage:
        return {
            "deal_id": deal_id,
            "opp_id": opp_id,
            "status": "failed",
            "reason": "could not read ACE stage",
        }

    target_hs_stage = ACE_TO_HUBSPOT.get(ace_stage)
    if not target_hs_stage:
        return {
            "deal_id": deal_id,
            "opp_id": opp_id,
            "status": "skipped",
            "reason": f"unknown ACE stage: {ace_stage}",
        }

    if target_hs_stage == hs_stage:
        return {
            "deal_id": deal_id,
            "opp_id": opp_id,
            "status": "aligned",
            "stage": ace_stage,
        }

    # Do not backtrack HubSpot if ACE is behind
    if _stage_rank(target_hs_stage) < _stage_rank(hs_stage):
        return {
            "deal_id": deal_id,
            "opp_id": opp_id,
            "status": "skipped",
            "reason": f"ACE stage '{ace_stage}' is behind HubSpot '{hs_stage}' — no backtrack",
        }

    success = await hubspot.update_deal_property(deal_id, "dealstage", target_hs_stage)
    if success:
        logger.info(
            "ace_sync_updated",
            extra={"deal_id": deal_id, "opp_id": opp_id,
                   "from_stage": hs_stage, "to_stage": target_hs_stage},
        )
        return {
            "deal_id":    deal_id,
            "opp_id":     opp_id,
            "deal_name":  deal_name,
            "status":     "updated",
            "ace_stage":  ace_stage,
            "old_hs":     hs_stage,
            "new_hs":     target_hs_stage,
        }
    return {
        "deal_id": deal_id,
        "opp_id":  opp_id,
        "status":  "failed",
        "reason":  "HubSpot update failed",
    }


async def run_sync() -> dict:
    """Sync all HubSpot deals with ACE IDs to their ACE stage.

    Fetches deals from HubSpot, reads ACE stage for each in parallel,
    updates HubSpot where ACE is ahead. Returns summary + per-deal results.
    """
    deals = await hubspot.get_deals_with_ace_id()
    if not deals:
        logger.info("ace_sync_no_deals")
        return {
            "date":    datetime.now().strftime("%d %b %Y %H:%M"),
            "total":   0,
            "synced":  0,
            "aligned": 0,
            "skipped": 0,
            "failed":  0,
            "updates": [],
        }

    results = await asyncio.gather(
        *[_sync_one(d) for d in deals],
        return_exceptions=True,
    )

    updates: list[dict] = []
    synced = aligned = skipped = failed = 0

    for r in results:
        if isinstance(r, Exception):
            failed += 1
            continue
        status = r.get("status", "")
        if status == "updated":
            synced += 1
            updates.append(r)
        elif status == "aligned":
            aligned += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1

    return {
        "date":    datetime.now().strftime("%d %b %Y %H:%M"),
        "total":   len(deals),
        "synced":  synced,
        "aligned": aligned,
        "skipped": skipped,
        "failed":  failed,
        "updates": updates,
    }


async def post_sync_to_teams(data: dict) -> bool:
    """Post sync summary to ACE Teams channel."""
    date    = data.get("date", "")
    synced  = data.get("synced", 0)
    aligned = data.get("aligned", 0)
    total   = data.get("total", 0)
    updates = data.get("updates", [])

    if synced == 0 and total > 0:
        body_text = f"All {aligned} deals already aligned. No updates needed."
    elif total == 0:
        body_text = "No HubSpot deals with ACE IDs found."
    else:
        lines = [f"Updated {synced} of {total} deals:"]
        for u in updates[:10]:
            lines.append(
                f"  {u.get('deal_name', u.get('opp_id', ''))} "
                f"→ {u.get('ace_stage', '')} ({u.get('old_hs', '')} → {u.get('new_hs', '')})"
            )
        body_text = "\n".join(lines)

    facts = [
        {"title": "Total deals",  "value": str(total)},
        {"title": "Updated",      "value": str(synced)},
        {"title": "Already aligned", "value": str(aligned)},
    ]

    return await teams.post_to_ace(
        title=f"ACE SYNC — {date}",
        body_text=body_text,
        facts=facts,
    )
