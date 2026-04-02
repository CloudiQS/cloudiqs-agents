"""
Event Bus — agent-to-agent event routing with S3 audit trail.

Agents POST events after significant actions (lead created, deal qualified,
reply received, ACE opportunity created). Events are stored in memory
(capped at 500), persisted to the local data volume, and audited to S3.

Event schema:
  event_type: str   (e.g. "lead.created", "deal.qualified", "reply.received")
  agent:      str   (source agent name, e.g. "sdr-vmware")
  payload:    dict  (event-specific data)
  timestamp:  str   (ISO 8601)
  id:         str   (uuid4)

Exported:
  publish(event_type, agent, payload)  -> dict  (the saved event)
  get_recent(event_type, limit)        -> list
  replay(event_id)                     -> dict | None
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bridge")

# In-memory ring buffer
_MAX_EVENTS = 500
_events: list[dict] = []
_events_lock = asyncio.Lock()

# Local persistence
_EVENTS_FILE = Path("/data/bus_events.json")

# Valid event types
VALID_EVENTS = {
    "lead.created",
    "lead.duplicate",
    "reply.received",
    "reply.classified",
    "deal.qualified",
    "deal.stage_changed",
    "ace.created",
    "ace.updated",
    "ace.funding_submitted",
    "hygiene.complete",
    "briefing.complete",
    "sync.complete",
}


# ── Persistence helpers ───────────────────────────────────────────────────────

def _load_events_from_disk() -> list:
    try:
        if _EVENTS_FILE.exists():
            data = json.loads(_EVENTS_FILE.read_text())
            if isinstance(data, list):
                return data[-_MAX_EVENTS:]
    except Exception as exc:
        logger.warning("events_load_failed", extra={"error": str(exc)})
    return []


def _save_events_to_disk(events: list) -> None:
    try:
        _EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _EVENTS_FILE.write_text(json.dumps(events[-_MAX_EVENTS:]))
    except Exception as exc:
        logger.warning("events_save_failed", extra={"error": str(exc)})


async def _audit_to_s3(event: dict) -> None:
    """Write event to S3 for long-term audit trail. Fire-and-forget."""
    try:
        import boto3
        from app.config import get_secret, STACK_NAME

        bucket = f"{STACK_NAME}-uploads-736956442878"
        key = (
            f"events/{event['event_type'].replace('.', '/')}/"
            f"{event['timestamp'][:10]}/{event['id']}.json"
        )
        s3 = boto3.client("s3", region_name="eu-west-1")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(event),
            ContentType="application/json",
        )
        logger.info("event_audited_s3", extra={"event_id": event["id"], "key": key})
    except Exception as exc:
        logger.warning("event_s3_audit_failed", extra={"error": str(exc)})


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def _bootstrap() -> None:
    """Load persisted events into memory on first import."""
    global _events
    _events = _load_events_from_disk()


_bootstrap()


# ── Public API ────────────────────────────────────────────────────────────────

async def publish(event_type: str, agent: str, payload: Optional[dict] = None) -> dict:
    """Publish an event to the bus.

    Args:
        event_type: One of VALID_EVENTS (or any dot-separated string)
        agent:      Source agent name
        payload:    Event-specific data dict

    Returns:
        The saved event dict.
    """
    event = {
        "id":         str(uuid.uuid4()),
        "event_type": event_type,
        "agent":      agent,
        "payload":    payload or {},
        "timestamp":  datetime.utcnow().isoformat() + "Z",
    }

    async with _events_lock:
        _events.append(event)
        while len(_events) > _MAX_EVENTS:
            _events.pop(0)
        _save_events_to_disk(_events)

    # S3 audit is fire-and-forget
    asyncio.create_task(_audit_to_s3(event))

    logger.info(
        "event_published",
        extra={"event_type": event_type, "agent": agent, "event_id": event["id"]},
    )
    return event


async def get_recent(
    event_type: Optional[str] = None,
    agent: Optional[str] = None,
    limit: int = 50,
) -> list:
    """Return recent events, newest first.

    Args:
        event_type: Filter by event type (exact match)
        agent:      Filter by source agent
        limit:      Max events to return (capped at 200)
    """
    async with _events_lock:
        events = list(_events)

    if event_type:
        events = [e for e in events if e["event_type"] == event_type]
    if agent:
        events = [e for e in events if e["agent"] == agent]

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:min(limit, 200)]


async def replay(event_id: str) -> Optional[dict]:
    """Look up a specific event by ID."""
    async with _events_lock:
        for e in reversed(_events):
            if e.get("id") == event_id:
                return e
    return None
