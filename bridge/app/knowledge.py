"""
Knowledge base — DynamoDB event log + S3 profile store.

Tracks every agent action and stores company research profiles.
Used to prevent duplicate outreach, share research across agents,
and build a queryable history of all pipeline activity.

DynamoDB table: {STACK_NAME}-agent-log  (env: KNOWLEDGE_TABLE)
  PK: company_slug  (e.g. "uk-tote-group")
  SK: sort_key

  Item types:
    Events:  SK = "EVENT#{event_type}#{created_at_iso}"
             Attrs: event_type, created_at, event_id, agent, summary,
                    detail (JSON string), campaign

    Profile: SK = "PROFILE"
             Attrs: campaign, contacted (bool), first_seen, last_updated

  Required GSIs (create once via Console or CloudFormation):
    type-time-index
      PK: event_type (STRING)
      SK: created_at  (STRING)
      Used by: get_events_by_type()

    campaign-index  (sparse — only PROFILE items carry campaign)
      PK: campaign  (STRING)
      SK: first_seen (STRING)
      Used by: get_never_contacted()

S3 bucket: {STACK_NAME}-knowledge  (env: KNOWLEDGE_BUCKET)
  profiles/{company_slug}.json  — full lead / research profile

IAM: bridge runs on EC2 with cloudiqs-engine-role.
     Instance profile handles auth — no access keys in code.

Default resource names use STACK_NAME env var (default: cloudiqs-engine):
  table  → cloudiqs-engine-agent-log   (live: cloudiqs-agent-log*)
  bucket → cloudiqs-engine-knowledge

*Set KNOWLEDGE_TABLE=cloudiqs-agent-log to match the live table name.
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

from app.config import REGION, STACK

logger = logging.getLogger("bridge")

# Resource names: override via env vars if the live names differ from STACK convention
_TABLE_NAME  = os.environ.get("KNOWLEDGE_TABLE",  f"{STACK}-agent-log")
_BUCKET_NAME = os.environ.get("KNOWLEDGE_BUCKET", f"{STACK}-knowledge")

# Lazy-initialised boto3 handles — shared across requests
_table: Optional[object] = None
_s3:    Optional[object] = None


def _get_table():
    global _table
    if _table is None:
        resource = boto3.resource("dynamodb", region_name=REGION)
        _table = resource.Table(_TABLE_NAME)
    return _table


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


# ── Slug ─────────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """Convert a company name to a lowercase URL-safe slug.

    Examples:
        'UK Tote Group'  → 'uk-tote-group'
        'Acme & Co. Ltd' → 'acme-and-co-ltd'
        '  Spaces  '     → 'spaces'
    """
    s = name.lower().strip()
    s = re.sub(r"[&+]", "and", s)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s)
    return s.strip("-") or "unknown"


# ── Events ────────────────────────────────────────────────────────────────────

def log_event(
    company_slug: str,
    event_type: str,
    agent: str,
    summary: str,
    detail: Optional[dict] = None,
    campaign: str = "",
) -> bool:
    """Write an event record to DynamoDB and update the PROFILE metadata item.

    Args:
        company_slug: Slugified company name — use slugify() to generate.
        event_type:   One of: lead_created, outreach_sent, email_opened,
                      reply_received, qualified, ace_created, research_done.
        agent:        Agent or process that generated the event.
        summary:      Single-line description (max 500 chars).
        detail:       Optional dict of additional data (stored as JSON string).
        campaign:     Campaign vertical (e.g. "msp", "vmware").

    Returns:
        True on success. False on any error — never raises.
    """
    now = datetime.now(timezone.utc).isoformat()
    sort_key = f"EVENT#{event_type}#{now}"

    event_item: dict = {
        "company_slug": company_slug,
        "sort_key":     sort_key,
        "event_type":   event_type,
        "created_at":   now,
        "event_id":     str(uuid.uuid4()),
        "agent":        agent,
        "summary":      (summary or "")[:500],
    }
    if detail:
        event_item["detail"] = json.dumps(detail, default=str)
    if campaign:
        event_item["campaign"] = campaign

    try:
        table = _get_table()

        # Write the event record
        table.put_item(Item=event_item)

        # Build the PROFILE update expression
        update_parts = [
            "SET last_updated = :lu",
            "first_seen = if_not_exists(first_seen, :lu)",
        ]
        expr_values: dict = {":lu": now}

        if campaign:
            update_parts.append("campaign = if_not_exists(campaign, :c)")
            expr_values[":c"] = campaign

        _OUTREACH_TYPES = {"outreach_sent", "email_sent", "email_opened"}
        if event_type in _OUTREACH_TYPES:
            update_parts.append("contacted = :ct")
            expr_values[":ct"] = True

        table.update_item(
            Key={"company_slug": company_slug, "sort_key": "PROFILE"},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeValues=expr_values,
        )

        logger.info(
            "knowledge_event_logged",
            extra={"company": company_slug, "type": event_type, "agent": agent},
        )
        return True

    except Exception as exc:
        logger.warning(
            "knowledge_log_event_failed",
            extra={"company": company_slug, "type": event_type, "error": str(exc)},
        )
        return False


def get_events(company_slug: str) -> list:
    """Return all events for a company, newest first.

    Returns empty list on any error.
    """
    try:
        table = _get_table()
        resp = table.query(
            KeyConditionExpression=(
                Key("company_slug").eq(company_slug)
                & Key("sort_key").begins_with("EVENT#")
            ),
            ScanIndexForward=False,
        )
        return resp.get("Items", [])
    except Exception as exc:
        logger.warning(
            "knowledge_get_events_failed",
            extra={"company": company_slug, "error": str(exc)},
        )
        return []


def get_last_event(company_slug: str, event_type: str) -> Optional[dict]:
    """Return the most recent event of a given type for a company.

    Returns None if not found or on any error.
    """
    try:
        table = _get_table()
        resp = table.query(
            KeyConditionExpression=(
                Key("company_slug").eq(company_slug)
                & Key("sort_key").begins_with(f"EVENT#{event_type}#")
            ),
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None
    except Exception as exc:
        logger.warning(
            "knowledge_get_last_event_failed",
            extra={"company": company_slug, "type": event_type, "error": str(exc)},
        )
        return None


def get_events_by_type(event_type: str, since: Optional[str] = None) -> list:
    """Return all events of a given type across all companies.

    Args:
        event_type: Event type filter (e.g. "lead_created").
        since:      ISO8601 timestamp — return only events at or after this time.

    Uses GSI: type-time-index (PK: event_type, SK: created_at).
    Returns empty list on any error.
    """
    try:
        table = _get_table()
        kce = Key("event_type").eq(event_type)
        if since:
            kce = kce & Key("created_at").gte(since)
        resp = table.query(
            IndexName="type-time-index",
            KeyConditionExpression=kce,
            ScanIndexForward=False,
        )
        return resp.get("Items", [])
    except Exception as exc:
        logger.warning(
            "knowledge_get_events_by_type_failed",
            extra={"type": event_type, "since": since, "error": str(exc)},
        )
        return []


# ── S3 profiles ───────────────────────────────────────────────────────────────

def save_profile(company_slug: str, profile: dict) -> bool:
    """Save a company profile to S3 as JSON.

    Key: profiles/{company_slug}.json
    Overwrites any existing profile for the same slug.
    Adds a 'saved_at' timestamp to the stored object.

    Returns True on success. False on any error — never raises.
    """
    key = f"profiles/{company_slug}.json"
    try:
        data = dict(profile)
        data["saved_at"] = datetime.now(timezone.utc).isoformat()
        body = json.dumps(data, default=str, indent=2).encode()
        _get_s3().put_object(
            Bucket=_BUCKET_NAME,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        logger.info(
            "knowledge_profile_saved",
            extra={"company": company_slug, "key": key, "bytes": len(body)},
        )
        return True
    except Exception as exc:
        logger.warning(
            "knowledge_save_profile_failed",
            extra={"company": company_slug, "key": key, "error": str(exc)},
        )
        return False


def get_profile(company_slug: str) -> Optional[dict]:
    """Load a company profile from S3.

    Returns the profile dict (including 'saved_at' timestamp),
    or None if the profile does not exist or on any error.
    """
    key = f"profiles/{company_slug}.json"
    try:
        resp = _get_s3().get_object(Bucket=_BUCKET_NAME, Key=key)
        return json.loads(resp["Body"].read().decode())
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return None
        logger.warning(
            "knowledge_get_profile_failed",
            extra={"company": company_slug, "key": key, "error": str(exc)},
        )
        return None
    except Exception as exc:
        logger.warning(
            "knowledge_get_profile_failed",
            extra={"company": company_slug, "key": key, "error": str(exc)},
        )
        return None


# ── Contact tracking ──────────────────────────────────────────────────────────

def has_been_contacted(company_slug: str) -> bool:
    """Return True if an outreach event has been logged for this company.

    Fast path: checks the PROFILE metadata item's 'contacted' flag.
    Fallback:  queries the event stream for outreach/email events.

    Returns False if not contacted or on any error.
    """
    # Fast path — single item lookup
    try:
        table = _get_table()
        resp = table.get_item(
            Key={"company_slug": company_slug, "sort_key": "PROFILE"},
        )
        item = resp.get("Item")
        if item and item.get("contacted"):
            return True
    except Exception as exc:
        logger.warning(
            "knowledge_has_been_contacted_check_failed",
            extra={"company": company_slug, "error": str(exc)},
        )

    # Fallback — check event stream
    for etype in ("outreach_sent", "email_sent"):
        if get_last_event(company_slug, etype):
            return True
    return False


def get_never_contacted(campaign: str) -> list:
    """Return slugs of companies in a campaign that have not yet been contacted.

    Queries the campaign-index GSI (PK: campaign) which is sparse —
    only PROFILE items carry the campaign attribute.
    Filters where contacted attribute is not True.

    Returns empty list on any error.
    """
    try:
        table = _get_table()
        resp = table.query(
            IndexName="campaign-index",
            KeyConditionExpression=Key("campaign").eq(campaign),
            FilterExpression=Attr("contacted").ne(True),
        )
        return [item["company_slug"] for item in resp.get("Items", [])]
    except Exception as exc:
        logger.warning(
            "knowledge_get_never_contacted_failed",
            extra={"campaign": campaign, "error": str(exc)},
        )
        return []
