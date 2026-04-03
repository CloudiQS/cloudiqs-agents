"""Unit tests for app.knowledge — DynamoDB + S3 knowledge base client."""
import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError


# ── slugify ───────────────────────────────────────────────────────────────────

def test_slugify_basic():
    from app.knowledge import slugify
    assert slugify("UK Tote Group") == "uk-tote-group"


def test_slugify_ampersand():
    from app.knowledge import slugify
    assert slugify("Acme & Co. Ltd") == "acme-and-co-ltd"


def test_slugify_strips_punctuation():
    from app.knowledge import slugify
    assert slugify("Test (UK) Ltd.") == "test-uk-ltd"


def test_slugify_collapses_spaces():
    from app.knowledge import slugify
    assert slugify("  Extra   Spaces  ") == "extra-spaces"


def test_slugify_empty_returns_unknown():
    from app.knowledge import slugify
    assert slugify("") == "unknown"
    assert slugify("!!!") == "unknown"


def test_slugify_alphanumeric():
    from app.knowledge import slugify
    assert slugify("Acme123") == "acme123"


# ── log_event ─────────────────────────────────────────────────────────────────

@patch("app.knowledge._get_table")
def test_log_event_success(mock_get_table):
    from app.knowledge import log_event
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.put_item.return_value = {}
    tbl.update_item.return_value = {}

    result = log_event(
        "uk-tote-group", "lead_created", "sdr-vmware",
        "New lead found", campaign="switcher",
    )

    assert result is True
    tbl.put_item.assert_called_once()
    item = tbl.put_item.call_args[1]["Item"]
    assert item["company_slug"] == "uk-tote-group"
    assert item["event_type"] == "lead_created"
    assert item["agent"] == "sdr-vmware"
    assert item["campaign"] == "switcher"
    assert item["sort_key"].startswith("EVENT#lead_created#")


@patch("app.knowledge._get_table")
def test_log_event_outreach_sets_contacted(mock_get_table):
    from app.knowledge import log_event
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.put_item.return_value = {}
    tbl.update_item.return_value = {}

    log_event("acme", "outreach_sent", "sdr-msp", "Email sent")

    update_call = tbl.update_item.call_args
    expr = update_call[1]["UpdateExpression"]
    vals = update_call[1]["ExpressionAttributeValues"]
    assert "contacted" in expr
    assert vals.get(":ct") is True


@patch("app.knowledge._get_table")
def test_log_event_includes_detail_json(mock_get_table):
    from app.knowledge import log_event
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.put_item.return_value = {}
    tbl.update_item.return_value = {}

    log_event("acme", "lead_created", "test", "summary", detail={"icp": 9})

    item = tbl.put_item.call_args[1]["Item"]
    assert "detail" in item
    parsed = json.loads(item["detail"])
    assert parsed["icp"] == 9


@patch("app.knowledge._get_table")
def test_log_event_returns_false_on_error(mock_get_table):
    from app.knowledge import log_event
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.put_item.side_effect = Exception("DynamoDB unreachable")

    result = log_event("acme", "lead_created", "test", "test")
    assert result is False


# ── get_events ────────────────────────────────────────────────────────────────

@patch("app.knowledge._get_table")
def test_get_events_returns_items(mock_get_table):
    from app.knowledge import get_events
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.query.return_value = {
        "Items": [
            {"company_slug": "acme", "event_type": "lead_created", "summary": "Test"},
        ]
    }
    events = get_events("acme")
    assert len(events) == 1
    assert events[0]["event_type"] == "lead_created"


@patch("app.knowledge._get_table")
def test_get_events_empty_on_error(mock_get_table):
    from app.knowledge import get_events
    mock_get_table.side_effect = Exception("DynamoDB down")
    assert get_events("acme") == []


# ── get_last_event ────────────────────────────────────────────────────────────

@patch("app.knowledge._get_table")
def test_get_last_event_found(mock_get_table):
    from app.knowledge import get_last_event
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.query.return_value = {
        "Items": [{"company_slug": "acme", "event_type": "lead_created"}]
    }
    evt = get_last_event("acme", "lead_created")
    assert evt is not None
    assert evt["event_type"] == "lead_created"


@patch("app.knowledge._get_table")
def test_get_last_event_not_found_returns_none(mock_get_table):
    from app.knowledge import get_last_event
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.query.return_value = {"Items": []}
    assert get_last_event("acme", "lead_created") is None


@patch("app.knowledge._get_table")
def test_get_last_event_error_returns_none(mock_get_table):
    from app.knowledge import get_last_event
    mock_get_table.side_effect = Exception("fail")
    assert get_last_event("acme", "lead_created") is None


# ── get_events_by_type ────────────────────────────────────────────────────────

@patch("app.knowledge._get_table")
def test_get_events_by_type(mock_get_table):
    from app.knowledge import get_events_by_type
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.query.return_value = {
        "Items": [
            {"event_type": "outreach_sent", "company_slug": "a"},
            {"event_type": "outreach_sent", "company_slug": "b"},
        ]
    }
    events = get_events_by_type("outreach_sent")
    assert len(events) == 2
    assert tbl.query.call_args[1]["IndexName"] == "type-time-index"


@patch("app.knowledge._get_table")
def test_get_events_by_type_error_returns_empty(mock_get_table):
    from app.knowledge import get_events_by_type
    mock_get_table.side_effect = Exception("fail")
    assert get_events_by_type("lead_created") == []


# ── save_profile ──────────────────────────────────────────────────────────────

@patch("app.knowledge._get_s3")
def test_save_profile_success(mock_get_s3):
    from app.knowledge import save_profile
    s3 = MagicMock()
    mock_get_s3.return_value = s3
    s3.put_object.return_value = {}

    result = save_profile("uk-tote-group", {"company": "UK Tote Group", "icp_score": 10})

    assert result is True
    s3.put_object.assert_called_once()
    kw = s3.put_object.call_args[1]
    assert kw["Key"] == "profiles/uk-tote-group.json"
    assert kw["ContentType"] == "application/json"
    stored = json.loads(kw["Body"].decode())
    assert stored["company"] == "UK Tote Group"
    assert "saved_at" in stored


@patch("app.knowledge._get_s3")
def test_save_profile_returns_false_on_error(mock_get_s3):
    from app.knowledge import save_profile
    s3 = MagicMock()
    mock_get_s3.return_value = s3
    s3.put_object.side_effect = Exception("S3 error")

    assert save_profile("acme", {"company": "Acme"}) is False


# ── get_profile ───────────────────────────────────────────────────────────────

@patch("app.knowledge._get_s3")
def test_get_profile_returns_dict(mock_get_s3):
    from app.knowledge import get_profile
    s3 = MagicMock()
    mock_get_s3.return_value = s3
    body = json.dumps({"company": "UK Tote Group", "icp_score": 10}).encode()
    s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}

    profile = get_profile("uk-tote-group")
    assert profile is not None
    assert profile["company"] == "UK Tote Group"


@patch("app.knowledge._get_s3")
def test_get_profile_not_found_returns_none(mock_get_s3):
    from app.knowledge import get_profile
    s3 = MagicMock()
    mock_get_s3.return_value = s3
    err = {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}
    s3.get_object.side_effect = ClientError(err, "GetObject")

    assert get_profile("nonexistent") is None


@patch("app.knowledge._get_s3")
def test_get_profile_other_s3_error_returns_none(mock_get_s3):
    from app.knowledge import get_profile
    s3 = MagicMock()
    mock_get_s3.return_value = s3
    s3.get_object.side_effect = Exception("network error")

    assert get_profile("acme") is None


# ── has_been_contacted ────────────────────────────────────────────────────────

@patch("app.knowledge._get_table")
def test_has_been_contacted_true_via_profile(mock_get_table):
    from app.knowledge import has_been_contacted
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.get_item.return_value = {
        "Item": {"company_slug": "acme", "sort_key": "PROFILE", "contacted": True}
    }
    assert has_been_contacted("acme") is True


@patch("app.knowledge._get_table")
def test_has_been_contacted_false_no_profile(mock_get_table):
    from app.knowledge import has_been_contacted
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    # Profile exists but contacted=False
    tbl.get_item.return_value = {
        "Item": {"company_slug": "acme", "sort_key": "PROFILE", "contacted": False}
    }
    # No outreach events either
    tbl.query.return_value = {"Items": []}
    assert has_been_contacted("acme") is False


@patch("app.knowledge._get_table")
def test_has_been_contacted_true_via_event_fallback(mock_get_table):
    from app.knowledge import has_been_contacted
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    # Profile has no contacted flag
    tbl.get_item.return_value = {"Item": {"company_slug": "acme", "sort_key": "PROFILE"}}
    # But there is an outreach_sent event
    tbl.query.return_value = {
        "Items": [{"event_type": "outreach_sent", "company_slug": "acme"}]
    }
    assert has_been_contacted("acme") is True


@patch("app.knowledge._get_table")
def test_has_been_contacted_returns_false_on_error(mock_get_table):
    from app.knowledge import has_been_contacted
    mock_get_table.side_effect = Exception("DynamoDB down")
    # Falls back to event queries which also fail → returns False
    assert has_been_contacted("acme") is False


# ── get_never_contacted ───────────────────────────────────────────────────────

@patch("app.knowledge._get_table")
def test_get_never_contacted(mock_get_table):
    from app.knowledge import get_never_contacted
    tbl = MagicMock()
    mock_get_table.return_value = tbl
    tbl.query.return_value = {
        "Items": [
            {"company_slug": "alpha", "campaign": "msp", "contacted": False},
            {"company_slug": "beta",  "campaign": "msp"},   # no contacted attr
        ]
    }
    result = get_never_contacted("msp")
    assert "alpha" in result
    assert "beta" in result
    assert tbl.query.call_args[1]["IndexName"] == "campaign-index"


@patch("app.knowledge._get_table")
def test_get_never_contacted_error_returns_empty(mock_get_table):
    from app.knowledge import get_never_contacted
    mock_get_table.side_effect = Exception("fail")
    assert get_never_contacted("msp") == []
