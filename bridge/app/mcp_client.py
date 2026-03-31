"""
AWS Partner Central MCP Client.

Connects to the Partner Central agents MCP Server for:
- Pipeline insights
- Customer profiles (AWS signal detection)
- Opportunity management
- Funding recommendations and applications
- Sales play generation
- Solution matching

Endpoint: https://partnercentral-agents-mcp.us-east-1.api.aws/mcp
Auth: SigV4 via requests-aws4auth (battle-tested, does not invalidate signatures)
Protocol: JSON-RPC 2.0 over HTTPS. sendMessage may return SSE — handled below.

Reference: https://docs.aws.amazon.com/partner-central/latest/APIReference/mcp-getting-started.html
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple

import boto3
import requests
from botocore.exceptions import ClientError
from requests_aws4auth import AWS4Auth

from app.config import REGION, get_secret, is_dummy

logger = logging.getLogger("bridge")

MCP_ENDPOINT = "https://partnercentral-agents-mcp.us-east-1.api.aws/mcp"
MCP_SERVICE = "partnercentral-agents-mcp"
MCP_REGION = "us-east-1"

# Cache sessions with timestamps: {catalog: (session_id, created_at)}
# Sessions expire after 48h per AWS docs.
_sessions: Dict[str, Tuple[str, datetime]] = {}
SESSION_TTL_HOURS = 47  # refresh slightly before the 48h AWS expiry


def _get_auth() -> AWS4Auth:
    """Get requests-aws4auth credentials via cross-account role assume."""
    role_arn = get_secret("partner-central/role-arn")
    if is_dummy(role_arn):
        role_arn = "arn:aws:iam::349440382087:role/CloudiQS-PartnerCentral-MCP"

    sts = boto3.client("sts", region_name=REGION)
    assumed = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"mcp-{int(datetime.now().timestamp())}",
        DurationSeconds=900,
    )
    creds = assumed["Credentials"]
    return AWS4Auth(
        creds["AccessKeyId"],
        creds["SecretAccessKey"],
        MCP_REGION,
        MCP_SERVICE,
        session_token=creds["SessionToken"],
    )


def _get_cached_session(catalog: str) -> Optional[str]:
    """Return cached session ID if still valid, else None."""
    entry = _sessions.get(catalog)
    if not entry:
        return None
    session_id, created_at = entry
    age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
    if age_hours >= SESSION_TTL_HOURS:
        del _sessions[catalog]
        return None
    return session_id


def _parse_sse_or_json(text: str) -> Optional[dict]:
    """Parse response body that may be plain JSON or SSE (data: {...} lines)."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    # SSE format: accumulate all data: lines into a single response
    combined: dict = {}
    for line in text.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                try:
                    obj = json.loads(chunk)
                    combined.update(obj)
                except json.JSONDecodeError:
                    pass
    return combined if combined else None


def _do_post(body: str) -> Optional[dict]:
    """Synchronous POST with SigV4 auth. Run via asyncio.to_thread."""
    try:
        auth = _get_auth()
        resp = requests.post(
            MCP_ENDPOINT,
            data=body,
            headers={"Content-Type": "application/json"},
            auth=auth,
            timeout=90,
        )
        if resp.status_code == 200:
            return _parse_sse_or_json(resp.text)
        logger.error(f"MCP request failed {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        logger.error(f"MCP connection error: {e}")
    return None


async def _send_jsonrpc(method: str, params: dict, request_id: int = 1) -> Optional[dict]:
    """Send a JSON-RPC 2.0 request to the MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    body = json.dumps(payload)
    return await asyncio.to_thread(_do_post, body)


async def initialize() -> bool:
    """Initialize MCP connection. Call once at startup."""
    result = await _send_jsonrpc("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {
            "name": "cloudiqs-engine",
            "version": "7.1.0",
        },
    })
    if result and "result" in result:
        logger.info("MCP initialized successfully")
        return True
    logger.error("MCP initialization failed")
    return False


async def send_message(
    message: str,
    catalog: str = "AWS",
    session_id: Optional[str] = None,
) -> Optional[dict]:
    """Send a natural language message to the Partner Central agent.

    Args:
        message: Natural language query (e.g. "Create a customer profile for Acme Ltd")
        catalog: "AWS" for production, "Sandbox" for testing
        session_id: Reuse existing session for multi-turn conversations (optional override)

    Returns:
        Dict with 'text' (agent response), 'sessionId', 'status'
    """
    # Use caller-supplied session, then cached session (if not expired), then none
    active_session = session_id or _get_cached_session(catalog)

    args = {
        "content": [{"type": "text", "text": message}],
        "catalog": catalog,
    }
    if active_session:
        args["sessionId"] = active_session

    result = await _send_jsonrpc("tools/call", {
        "name": "sendMessage",
        "arguments": args,
    })

    if not result:
        return None

    # Parse the response
    tool_result = result.get("result", {})
    content = tool_result.get("content", [])

    response = {
        "status": tool_result.get("status", "unknown"),
        "sessionId": tool_result.get("sessionId", ""),
        "text": "",
    }

    for block in content:
        if block.get("type") == "text":
            response["text"] += block.get("text", "")

    # Cache new session ID with creation timestamp
    if response["sessionId"]:
        _sessions[catalog] = (response["sessionId"], datetime.now(timezone.utc))

    return response


# ── Convenience functions for specific capabilities ───────────────────

async def get_customer_profile(company_name: str, catalog: str = "AWS") -> Optional[str]:
    """Generate AWS-enriched customer profile. Detects AWS customer signal."""
    result = await send_message(
        f"Create a customer profile for {company_name}",
        catalog=catalog,
    )
    return result.get("text") if result else None


async def check_funding_eligibility(opportunity_id: str, catalog: str = "AWS") -> Optional[str]:
    """Check which funding programs an opportunity qualifies for."""
    result = await send_message(
        f"Am I eligible for any funding programs on opportunity {opportunity_id}? "
        f"Check MAP, POC credits, ISV Workload Migration, and CEI.",
        catalog=catalog,
    )
    return result.get("text") if result else None


async def create_fund_request(opportunity_id: str, program: str = "MAP", catalog: str = "AWS") -> Optional[str]:
    """Create a pre-populated fund request for an opportunity."""
    result = await send_message(
        f"Create a {program} benefit application for opportunity {opportunity_id}",
        catalog=catalog,
    )
    return result.get("text") if result else None


async def get_pipeline_insights(query: str = "Which opportunities need my attention this week?", catalog: str = "AWS") -> Optional[str]:
    """Get pipeline intelligence from the MCP agent."""
    result = await send_message(query, catalog=catalog)
    return result.get("text") if result else None


async def get_sales_play(opportunity_id: str, catalog: str = "AWS") -> Optional[str]:
    """Generate a customised sales strategy for an opportunity."""
    result = await send_message(
        f"Generate a sales play for opportunity {opportunity_id}",
        catalog=catalog,
    )
    return result.get("text") if result else None


async def get_next_steps(opportunity_id: str, catalog: str = "AWS") -> Optional[str]:
    """Get prioritised next steps to advance an opportunity."""
    result = await send_message(
        f"What do I need to do next to advance opportunity {opportunity_id}? "
        f"Is it ready for submission? What fields are missing?",
        catalog=catalog,
    )
    return result.get("text") if result else None


async def match_solutions(opportunity_id: str, catalog: str = "AWS") -> Optional[str]:
    """Find which CloudiQS solutions best match an opportunity."""
    result = await send_message(
        f"Which of our solutions best match opportunity {opportunity_id}?",
        catalog=catalog,
    )
    return result.get("text") if result else None


async def progress_opportunity(opportunity_id: str, notes: str, catalog: str = "AWS") -> Optional[str]:
    """Upload call notes and auto-progress an opportunity."""
    result = await send_message(
        f"Here are my call notes for opportunity {opportunity_id}: {notes}. "
        f"Update the opportunity with the relevant details and tell me which "
        f"fields were satisfied and which still need data.",
        catalog=catalog,
    )
    return result.get("text") if result else None


async def get_closed_lost_analysis(catalog: str = "AWS") -> Optional[str]:
    """Analyse why opportunities were lost in the last 6 months."""
    result = await send_message(
        "What are the top reasons we have lost opportunities in the last 6 months?",
        catalog=catalog,
    )
    return result.get("text") if result else None
