"""Unit tests for app.events event bus."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


# ── publish ───────────────────────────────────────────────────────────────────

@patch("app.events.asyncio.create_task")
async def test_publish_returns_event(mock_task):
    from app import events
    events._events.clear()
    event = await events.publish("lead.created", "sdr-vmware", {"company": "Acme"})
    assert event["event_type"] == "lead.created"
    assert event["agent"] == "sdr-vmware"
    assert event["payload"]["company"] == "Acme"
    assert "id" in event
    assert "timestamp" in event


@patch("app.events.asyncio.create_task")
async def test_publish_stores_event(mock_task):
    from app import events
    events._events.clear()
    await events.publish("reply.received", "sdr-reply-handler", {})
    recent = await events.get_recent()
    assert any(e["event_type"] == "reply.received" for e in recent)


@patch("app.events.asyncio.create_task")
async def test_publish_caps_at_max(mock_task):
    from app import events
    events._events.clear()
    original_max = events._MAX_EVENTS
    events._MAX_EVENTS = 5
    for i in range(10):
        await events.publish("lead.created", "sdr-test", {"i": i})
    assert len(events._events) <= 5
    events._MAX_EVENTS = original_max


# ── get_recent ────────────────────────────────────────────────────────────────

@patch("app.events.asyncio.create_task")
async def test_get_recent_filter_by_type(mock_task):
    from app import events
    events._events.clear()
    await events.publish("lead.created", "sdr-vmware", {})
    await events.publish("reply.received", "sdr-reply-handler", {})
    leads = await events.get_recent(event_type="lead.created")
    assert all(e["event_type"] == "lead.created" for e in leads)


@patch("app.events.asyncio.create_task")
async def test_get_recent_filter_by_agent(mock_task):
    from app import events
    events._events.clear()
    await events.publish("lead.created", "sdr-vmware", {})
    await events.publish("lead.created", "sdr-msp", {})
    vmware_events = await events.get_recent(agent="sdr-vmware")
    assert all(e["agent"] == "sdr-vmware" for e in vmware_events)


@patch("app.events.asyncio.create_task")
async def test_get_recent_limit(mock_task):
    from app import events
    events._events.clear()
    for i in range(10):
        await events.publish("lead.created", "sdr-test", {})
    results = await events.get_recent(limit=3)
    assert len(results) <= 3


@patch("app.events.asyncio.create_task")
async def test_get_recent_newest_first(mock_task):
    from app import events
    events._events.clear()
    await events.publish("lead.created", "sdr-a", {"order": 1})
    await events.publish("lead.created", "sdr-b", {"order": 2})
    results = await events.get_recent(limit=2)
    assert results[0]["payload"]["order"] == 2


# ── replay ────────────────────────────────────────────────────────────────────

@patch("app.events.asyncio.create_task")
async def test_replay_finds_event(mock_task):
    from app import events
    events._events.clear()
    event = await events.publish("ace.created", "ace-create", {"opp_id": "O111"})
    found = await events.replay(event["id"])
    assert found is not None
    assert found["id"] == event["id"]


@patch("app.events.asyncio.create_task")
async def test_replay_returns_none_for_unknown(mock_task):
    from app import events
    result = await events.replay("nonexistent-uuid")
    assert result is None


# ── POST /event endpoint ──────────────────────────────────────────────────────

@patch("app.events.asyncio.create_task")
async def test_post_event_endpoint(mock_task):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/event", json={
            "event_type": "lead.created",
            "agent": "sdr-vmware",
            "payload": {"company": "Acme Corp"},
        })
    assert r.status_code == 200
    assert r.json()["status"] == "published"
    assert r.json()["event"]["event_type"] == "lead.created"


async def test_post_event_missing_type():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/event", json={"agent": "sdr-vmware"})
    assert r.status_code == 400


# ── GET /events/recent endpoint ───────────────────────────────────────────────

@patch("app.events.asyncio.create_task")
async def test_get_events_recent_endpoint(mock_task):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Publish one first
        await c.post("/event", json={"event_type": "lead.created", "agent": "sdr-test", "payload": {}})
        r = await c.get("/events/recent?event_type=lead.created&limit=5")
    assert r.status_code == 200
    assert "events" in r.json()
    assert "total" in r.json()


# ── POST /event/replay endpoint ───────────────────────────────────────────────

@patch("app.events.asyncio.create_task")
async def test_event_replay_endpoint(mock_task):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        pub = await c.post("/event", json={
            "event_type": "ace.created", "agent": "ace-create",
            "payload": {"opp_id": "O999"},
        })
        event_id = pub.json()["event"]["id"]
        r = await c.post("/event/replay", json={"event_id": event_id})
    assert r.status_code == 200
    assert r.json()["id"] == event_id


async def test_event_replay_not_found():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/event/replay", json={"event_id": "does-not-exist"})
    assert r.status_code == 404


async def test_event_replay_missing_id():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/event/replay", json={})
    assert r.status_code == 400
