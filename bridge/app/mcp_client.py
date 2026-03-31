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
Auth: SigV4 with service name 'partnercentral-agents-mcp', region 'us-east-1'
Protocol: JSON-RPC 2.0 over HTTPS

KNOWN RISKS (verify in Claude Code before production):
1. SigV4 signing: headers are signed then passed to httpx. If httpx modifies
   headers (content-length, host), the signature becomes invalid. May need
   to use botocore's own HTTP client or requests with aws4auth instead.
2. SSE streaming: AWS docs say the server supports Server-Sent Events.
   This code assumes a simple JSON response. If sendMessage returns SSE,
   this will fail. May need httpx streaming or an SSE client library.
3. Session expiry: sessions last 48h. Code caches sessionId but does not
   handle expiry. Add a timestamp check or catch session-expired errors.

Reference: https://docs.aws.amazon.com/partner-central/latest/APIReference/mcp-getting-started.html
"""

import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError
import httpx

from app.config import REGION, get_secret, is_dummy

logger = logging.getLogger("bridge")

MCP_ENDPOINT = "https://partnercentral-agents-mcp.us-east-1.api.aws/mcp"
MCP_SERVICE = "partnercentral-agents-mcp"
MCP_REGION = "us-east-1"

# Cache sessions (expire after 48h per AWS docs)
_sessions: Dict[str, str] = {}


def _get_credentials():
    """Get AWS credentials for SigV4 signing via cross-account role."""
    role_arn = get_secret("partner-central/role-arn")
    if is_dummy(role_arn):
        role_arn = "arn:aws:iam::349440382087:role/CloudiQS-PartnerCentral-MCP"

    sts = boto3.client("sts", region_name=REGION)
    assumed = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"mcp-{int(datetime.now().timestamp())}",
        DurationSeconds=900,
    )

    session = boto3.Session(
        aws_access_key_id=assumed["Credentials"]["AccessKeyId"],
        aws_secret_access_key=assumed["Credentials"]["SecretAccessKey"],
        aws_session_token=assumed["Credentials"]["SessionToken"],
        region_name=MCP_REGION,
    )
    return session.get_credentials().get_frozen_credentials()


def _sign_request(method: str, url: str, body: str) -> dict:
    """Sign a request with SigV4 for the MCP service."""
    credentials = _get_credentials()
    request = AWSRequest(
        method=method,
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    SigV4Auth(credentials, MCP_SERVICE, MCP_REGION).add_auth(request)
    return dict(request.headers)


async def _send_jsonrpc(method: str, params: dict, request_id: int = 1) -> Optional[dict]:
    """Send a JSON-RPC 2.0 request to the MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    body = json.dumps(payload)

    try:
        headers = _sign_request("POST", MCP_ENDPOINT, body)
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(MCP_ENDPOINT, content=body, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"MCP request failed {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        logger.error(f"MCP connection error: {e}")
    return None


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
        session_id: Reuse existing session for multi-turn conversations

    Returns:
        Dict with 'text' (agent response), 'sessionId', 'status'
    """
    args = {
        "content": [{"type": "text", "text": message}],
        "catalog": catalog,
    }
    if session_id:
        args["sessionId"] = session_id

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

    if response["sessionId"]:
        _sessions[catalog] = response["sessionId"]

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
