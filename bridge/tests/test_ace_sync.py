"""Unit tests for app.ace_sync — ACE → HubSpot stage sync."""
from unittest.mock import AsyncMock, patch


# ── _stage_rank ───────────────────────────────────────────────────────────────

def test_stage_rank_ordering():
    from app.ace_sync import _stage_rank
    assert _stage_rank("appointmentscheduled") < _stage_rank("qualifiedtobuy")
    assert _stage_rank("qualifiedtobuy") < _stage_rank("contractsent")
    assert _stage_rank("contractsent") < _stage_rank("closedwon")


def test_stage_rank_unknown():
    from app.ace_sync import _stage_rank
    assert _stage_rank("nonexistent_stage") == -1


# ── ACE_TO_HUBSPOT mapping ────────────────────────────────────────────────────

def test_ace_to_hubspot_mapping_complete():
    from app.ace_sync import ACE_TO_HUBSPOT
    for ace_stage in ("Prospect", "Qualified", "Technical Validation",
                      "Business Validation", "Committed", "Launched", "Closed Lost"):
        assert ace_stage in ACE_TO_HUBSPOT


# ── _sync_one ─────────────────────────────────────────────────────────────────

@patch("app.ace.get_opportunity_stage", new_callable=AsyncMock, return_value=None)
async def test_sync_one_fails_when_ace_stage_unavailable(mock_stage):
    from app.ace_sync import _sync_one
    deal = {"deal_id": "1", "ace_opportunity_id": "O111", "dealstage": "qualifiedtobuy", "dealname": "Acme"}
    result = await _sync_one(deal)
    assert result["status"] == "failed"


@patch("app.ace.get_opportunity_stage", new_callable=AsyncMock, return_value="Qualified")
async def test_sync_one_aligned_no_update(mock_stage):
    from app.ace_sync import _sync_one
    deal = {"deal_id": "1", "ace_opportunity_id": "O111", "dealstage": "qualifiedtobuy", "dealname": "Acme"}
    result = await _sync_one(deal)
    assert result["status"] == "aligned"


@patch("app.hubspot.update_deal_property", new_callable=AsyncMock, return_value=True)
@patch("app.ace.get_opportunity_stage", new_callable=AsyncMock, return_value="Committed")
async def test_sync_one_updates_hubspot_when_ace_ahead(mock_stage, mock_update):
    from app.ace_sync import _sync_one
    deal = {"deal_id": "1", "ace_opportunity_id": "O111", "dealstage": "qualifiedtobuy", "dealname": "Acme"}
    result = await _sync_one(deal)
    assert result["status"] == "updated"
    assert result["new_hs"] == "contractsent"
    mock_update.assert_called_once_with("1", "dealstage", "contractsent")


@patch("app.ace.get_opportunity_stage", new_callable=AsyncMock, return_value="Qualified")
async def test_sync_one_skips_backtrack(mock_stage):
    """ACE shows Qualified but HubSpot is already at Committed — do not backtrack."""
    from app.ace_sync import _sync_one
    deal = {"deal_id": "1", "ace_opportunity_id": "O111", "dealstage": "contractsent", "dealname": "Acme"}
    result = await _sync_one(deal)
    assert result["status"] == "skipped"
    assert "backtrack" in result["reason"]


async def test_sync_one_skips_no_ace_id():
    from app.ace_sync import _sync_one
    deal = {"deal_id": "1", "ace_opportunity_id": "", "dealstage": "qualifiedtobuy"}
    result = await _sync_one(deal)
    assert result["status"] == "skipped"


@patch("app.ace.get_opportunity_stage", new_callable=AsyncMock, return_value="UnknownStage")
async def test_sync_one_skips_unknown_ace_stage(mock_stage):
    from app.ace_sync import _sync_one
    deal = {"deal_id": "1", "ace_opportunity_id": "O111", "dealstage": "qualifiedtobuy", "dealname": "Acme"}
    result = await _sync_one(deal)
    assert result["status"] == "skipped"


# ── run_sync ──────────────────────────────────────────────────────────────────

@patch("app.hubspot.get_deals_with_ace_id", new_callable=AsyncMock, return_value=[])
async def test_run_sync_no_deals(mock_deals):
    from app.ace_sync import run_sync
    result = await run_sync()
    assert result["total"] == 0
    assert result["synced"] == 0
    assert "date" in result


@patch("app.hubspot.update_deal_property", new_callable=AsyncMock, return_value=True)
@patch("app.ace.get_opportunity_stage", new_callable=AsyncMock, return_value="Committed")
@patch("app.hubspot.get_deals_with_ace_id", new_callable=AsyncMock, return_value=[
    {"deal_id": "1", "ace_opportunity_id": "O111", "dealstage": "qualifiedtobuy", "dealname": "Acme"},
    {"deal_id": "2", "ace_opportunity_id": "O222", "dealstage": "contractsent",    "dealname": "Beta"},
])
async def test_run_sync_updates_one_skips_one(mock_deals, mock_stage, mock_update):
    from app.ace_sync import run_sync
    result = await run_sync()
    assert result["total"] == 2
    assert result["synced"] == 1   # Acme: qualifiedtobuy → Committed → contractsent
    assert result["aligned"] == 1  # Beta already at contractsent, ACE says Committed = same


@patch("app.hubspot.get_deals_with_ace_id", new_callable=AsyncMock, return_value=[
    {"deal_id": "1", "ace_opportunity_id": "O111", "dealstage": "qualifiedtobuy", "dealname": "Acme"},
])
async def test_run_sync_returns_all_keys(mock_deals):
    from app.ace_sync import run_sync
    with patch("app.ace_sync._sync_one", new_callable=AsyncMock, return_value={"status": "aligned"}):
        result = await run_sync()
    for key in ("date", "total", "synced", "aligned", "skipped", "failed", "updates"):
        assert key in result


# ── post_sync_to_teams ────────────────────────────────────────────────────────

@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_post_sync_calls_post_to_ace(mock_post):
    from app.ace_sync import post_sync_to_teams
    data = {
        "date": "07 Apr 2026 06:00", "total": 5, "synced": 2,
        "aligned": 3, "skipped": 0, "failed": 0,
        "updates": [
            {"deal_name": "Acme", "opp_id": "O1", "ace_stage": "Committed",
             "old_hs": "qualifiedtobuy", "new_hs": "contractsent"},
        ],
    }
    result = await post_sync_to_teams(data)
    assert result is True
    mock_post.assert_called_once()


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_post_sync_title_has_date(mock_post):
    from app.ace_sync import post_sync_to_teams
    await post_sync_to_teams({"date": "07 Apr 2026 06:00", "total": 3,
                               "synced": 0, "aligned": 3, "skipped": 0, "failed": 0, "updates": []})
    kwargs = mock_post.call_args[1]
    title = kwargs.get("title") or mock_post.call_args[0][0]
    assert "ACE SYNC" in title
    assert "07 Apr 2026" in title


@patch("app.teams.post_to_ace", new_callable=AsyncMock, return_value=True)
async def test_post_sync_no_deals_message(mock_post):
    from app.ace_sync import post_sync_to_teams
    await post_sync_to_teams({"date": "07 Apr 2026", "total": 0,
                               "synced": 0, "aligned": 0, "skipped": 0, "failed": 0, "updates": []})
    kwargs = mock_post.call_args[1]
    body = kwargs.get("body_text") or mock_post.call_args[0][1]
    assert "No HubSpot deals" in body
