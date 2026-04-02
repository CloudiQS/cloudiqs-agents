"""
Q2 2026 Target Tracker — weekly progress towards £400K pipeline.

Working backwards from Q2 target:
  Pipeline target:     £400,000 by 30 June 2026
  Deal size assumption: £30,000 average
  Deals needed:        ~14 qualified deals
  Weeks remaining:     13 weeks (Q2: Apr-Jun 2026)
  Pipeline per week:   £30,000

  Lead funnel (SDR):
    Leads needed/week:  58
    Emails/week:        46
    Replies/week:       2
    Qualified/week:     1

GET /targets/weekly returns JSON with:
  - current week number in Q2
  - weeks remaining
  - pipeline added this week (from HubSpot Qualified+ deals)
  - cumulative pipeline added this Q2
  - on_track bool (cumulative >= expected by this week)
  - funnel stats: leads this week, emails sent, reply rate

Exported:
  get_weekly_targets()  -> dict
"""

import logging
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger("bridge")

# Q2 2026 constants
Q2_START        = date(2026, 4, 1)
Q2_END          = date(2026, 6, 30)
Q2_TOTAL_WEEKS  = 13
Q2_TARGET_GBP   = 400_000
WEEKLY_TARGET   = Q2_TARGET_GBP // Q2_TOTAL_WEEKS  # £30,769
AVG_DEAL_SIZE   = 30_000

# Weekly funnel targets
LEADS_PER_WEEK  = 58
EMAILS_PER_WEEK = 46
REPLIES_PER_WEEK = 2
QUALIFIED_PER_WEEK = 1


def _week_of_q2(today: Optional[date] = None) -> int:
    """Return the current week number within Q2 (1-13). 0 if before Q2, 14+ if after."""
    today = today or date.today()
    if today < Q2_START:
        return 0
    if today > Q2_END:
        return Q2_TOTAL_WEEKS
    delta = (today - Q2_START).days
    return (delta // 7) + 1


def _weeks_remaining(today: Optional[date] = None) -> int:
    today = today or date.today()
    if today >= Q2_END:
        return 0
    delta = (Q2_END - today).days
    return max(0, (delta // 7) + 1)


def _expected_pipeline_by_week(week: int) -> int:
    """Expected cumulative pipeline value by end of the given Q2 week."""
    return min(week, Q2_TOTAL_WEEKS) * WEEKLY_TARGET


async def get_weekly_targets(stats: Optional[dict] = None) -> dict:
    """Compute Q2 weekly target metrics.

    Args:
        stats: Optional stats dict from /stats endpoint
               (keys: total_leads, by_campaign, week_leads, week_pipeline_gbp)

    Returns:
        Dict with progress metrics, funnel targets, and on_track flag.
    """
    today      = date.today()
    week_num   = _week_of_q2(today)
    weeks_left = _weeks_remaining(today)
    expected   = _expected_pipeline_by_week(week_num)

    # Try to get pipeline data from HubSpot if not provided
    cumulative_pipeline = 0
    week_leads = 0
    week_pipeline = 0

    if stats:
        week_leads    = stats.get("week_leads", stats.get("total_leads", 0))
        week_pipeline = stats.get("week_pipeline_gbp", 0)
        cumulative_pipeline = stats.get("q2_pipeline_gbp", week_pipeline)
    else:
        # Fallback: try to compute from HubSpot
        try:
            from app import hubspot
            pipeline_data = await hubspot.get_pipeline_counts()
            # Pipeline counts are deal counts, not values — use as proxy
            qualified_count = sum(
                pipeline_data.get(s, 0)
                for s in ("qualifiedtobuy", "presentationscheduled",
                          "decisionmakerboughtin", "contractsent", "closedwon")
            )
            cumulative_pipeline = qualified_count * AVG_DEAL_SIZE
        except Exception as exc:
            logger.warning("targets_pipeline_fetch_failed", extra={"error": str(exc)})

    on_track   = cumulative_pipeline >= expected
    gap_to_target = max(0, Q2_TARGET_GBP - cumulative_pipeline)
    pipeline_needed_per_remaining_week = (
        (gap_to_target // weeks_left) if weeks_left > 0 else 0
    )

    return {
        "date":                         today.isoformat(),
        "q2_week":                      week_num,
        "q2_total_weeks":               Q2_TOTAL_WEEKS,
        "weeks_remaining":              weeks_left,
        "q2_target_gbp":                Q2_TARGET_GBP,
        "weekly_target_gbp":            WEEKLY_TARGET,
        "cumulative_pipeline_gbp":      cumulative_pipeline,
        "expected_pipeline_gbp":        expected,
        "on_track":                     on_track,
        "gap_to_target_gbp":            gap_to_target,
        "pipeline_needed_per_week_gbp": pipeline_needed_per_remaining_week,
        "funnel_targets": {
            "leads_per_week":     LEADS_PER_WEEK,
            "emails_per_week":    EMAILS_PER_WEEK,
            "replies_per_week":   REPLIES_PER_WEEK,
            "qualified_per_week": QUALIFIED_PER_WEEK,
        },
        "week_leads": week_leads,
    }
