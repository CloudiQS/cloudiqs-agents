"""Unit tests for app.targets — Q2 2026 pipeline target tracking."""
from datetime import date
from unittest.mock import AsyncMock, patch


# ── _week_of_q2 ───────────────────────────────────────────────────────────────

def test_week_of_q2_start():
    from app.targets import _week_of_q2
    assert _week_of_q2(date(2026, 4, 1)) == 1


def test_week_of_q2_second_week():
    from app.targets import _week_of_q2
    assert _week_of_q2(date(2026, 4, 8)) == 2


def test_week_of_q2_before_q2():
    from app.targets import _week_of_q2
    assert _week_of_q2(date(2026, 3, 31)) == 0


def test_week_of_q2_last_week():
    from app.targets import _week_of_q2
    assert _week_of_q2(date(2026, 6, 30)) <= 13


# ── _weeks_remaining ──────────────────────────────────────────────────────────

def test_weeks_remaining_start_of_q2():
    from app.targets import _weeks_remaining
    assert _weeks_remaining(date(2026, 4, 1)) == 13


def test_weeks_remaining_end_of_q2():
    from app.targets import _weeks_remaining
    assert _weeks_remaining(date(2026, 6, 30)) == 0


def test_weeks_remaining_mid_q2():
    from app.targets import _weeks_remaining
    weeks = _weeks_remaining(date(2026, 5, 1))
    assert 1 <= weeks <= 12


# ── _expected_pipeline_by_week ────────────────────────────────────────────────

def test_expected_pipeline_week_1():
    from app.targets import _expected_pipeline_by_week, WEEKLY_TARGET
    assert _expected_pipeline_by_week(1) == WEEKLY_TARGET


def test_expected_pipeline_week_13():
    from app.targets import _expected_pipeline_by_week, Q2_TARGET_GBP, Q2_TOTAL_WEEKS
    # Week 13 expected is 13 * WEEKLY_TARGET which may differ slightly from Q2_TARGET_GBP due to integer division
    assert _expected_pipeline_by_week(13) <= Q2_TARGET_GBP + 1000


def test_expected_pipeline_capped_at_q2_weeks():
    from app.targets import _expected_pipeline_by_week
    # Week 20 (beyond Q2) is capped at week 13
    assert _expected_pipeline_by_week(20) == _expected_pipeline_by_week(13)


# ── get_weekly_targets ────────────────────────────────────────────────────────

async def test_get_weekly_targets_returns_all_keys():
    from app.targets import get_weekly_targets
    with patch("app.targets.date") as mock_date:
        mock_date.today.return_value = date(2026, 4, 7)
        result = await get_weekly_targets(stats={"total_leads": 5, "week_leads": 5})
    for key in ("date", "q2_week", "weeks_remaining", "q2_target_gbp", "weekly_target_gbp",
                "cumulative_pipeline_gbp", "expected_pipeline_gbp", "on_track",
                "gap_to_target_gbp", "funnel_targets", "week_leads"):
        assert key in result


async def test_get_weekly_targets_on_track_when_ahead():
    from app.targets import get_weekly_targets
    with patch("app.targets.date") as mock_date:
        mock_date.today.return_value = date(2026, 4, 7)
        result = await get_weekly_targets(stats={
            "total_leads": 60,
            "week_pipeline_gbp": 35_000,
            "q2_pipeline_gbp": 35_000,
        })
    assert result["on_track"] is True


async def test_get_weekly_targets_not_on_track_when_behind():
    from app.targets import get_weekly_targets
    with patch("app.targets.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 1)  # Week 5
        result = await get_weekly_targets(stats={
            "total_leads": 10,
            "q2_pipeline_gbp": 0,
        })
    assert result["on_track"] is False


async def test_get_weekly_targets_funnel_targets_present():
    from app.targets import get_weekly_targets, LEADS_PER_WEEK, EMAILS_PER_WEEK
    with patch("app.targets.date") as mock_date:
        mock_date.today.return_value = date(2026, 4, 7)
        result = await get_weekly_targets()
    funnel = result["funnel_targets"]
    assert funnel["leads_per_week"] == LEADS_PER_WEEK
    assert funnel["emails_per_week"] == EMAILS_PER_WEEK


@patch("app.hubspot.get_pipeline_counts", new_callable=AsyncMock, return_value={
    "qualifiedtobuy": 5, "contractsent": 2
})
async def test_get_weekly_targets_falls_back_to_hubspot(mock_counts):
    from app.targets import get_weekly_targets, AVG_DEAL_SIZE
    with patch("app.targets.date") as mock_date:
        mock_date.today.return_value = date(2026, 4, 7)
        result = await get_weekly_targets()  # no stats provided
    # 5+2 = 7 qualified deals * AVG_DEAL_SIZE
    assert result["cumulative_pipeline_gbp"] == 7 * AVG_DEAL_SIZE
