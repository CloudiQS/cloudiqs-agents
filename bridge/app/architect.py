"""
AWS Architecture Generator.
Uses Bedrock Claude Sonnet to generate architecture recommendations for SOW documents.
"""

import asyncio
import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("bridge")

# Cross-region inference — Claude Sonnet in us-east-1
BEDROCK_REGION = "us-east-1"
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6-20251101"

SYSTEM_PROMPT = """You are a Senior AWS Solutions Architect at CloudiQS, an AWS Advanced Consulting \
Partner based in Harpenden, UK. You design cloud architectures for UK mid-market companies \
(50-500 employees).

When given customer requirements, produce a concise architecture recommendation in exactly \
this format:

## Architecture Overview
[3-5 sentences. Business outcomes first, technology second. No jargon.]

## Architecture Diagram
```
[ASCII diagram showing key AWS services, data flow, and eu-west-1 region boundary]
```

## Key AWS Services
| Service | Purpose |
|---------|---------|
[one row per service]

## Key Architectural Decisions
1. [Decision and rationale — why this, not an alternative]
2. [Decision and rationale]
3. [Decision and rationale]

## Security and Compliance Notes
[UK-specific: GDPR data residency (eu-west-1), FCA/SOC2 as applicable, IAM least privilege, \
encryption at rest and in transit.]

## AWS Funding Opportunities
[MAP, Well-Architected Review, POC credits if applicable. One sentence per programme.]

Rules:
- Default region: eu-west-1 (London)
- Follow AWS Well-Architected Framework (all 6 pillars)
- Size for UK SMB (50-500 employees) — no over-engineering
- Mark anything needing customer-specific detail as [TBC]
- Mark sections for Sita (Solutions Architect) to validate with *[Sita to validate]*
- Keep response under 800 words"""

SERVICE_TYPE_CONTEXT = {
    "migration": "The customer is migrating workloads from on-premises (VMware, bare metal, or \
legacy hosting) to AWS. Focus on MGN for rehost, ECS for containerisation, RDS for database \
migration, Direct Connect or VPN for hybrid connectivity, and a phased cutover approach.",
    "vmware": "The customer is exiting VMware following Broadcom acquisition price increases. \
Urgency is high. Focus on AWS MGN for lift-and-shift, compute right-sizing, and a fast \
8-12 week migration timeline.",
    "greenfield": "The customer is building a new cloud-native application on AWS. Focus on \
serverless where appropriate (Lambda, Fargate), managed databases (Aurora), CI/CD pipelines, \
and modern DevOps practices.",
    "storage": "The customer is migrating on-premises file storage (NAS, Windows File Server, \
NetApp) to AWS. Focus on FSx for Windows File Server, DataSync for migration, AWS Managed \
Active Directory, and DFS Namespace for transparent cutover.",
    "msp": "The customer needs ongoing managed cloud operations. Focus on CloudWatch dashboards \
and alarms, Security Hub, GuardDuty, Config, cost management (Budgets, Cost Explorer), patch \
management via SSM, and cross-account IAM for CloudiQS access.",
    "agentbakery": "The customer wants to build AI agent systems. Focus on Amazon Bedrock \
(Nova Lite for SDR, Claude Sonnet for complex reasoning), AWS AgentCore, Strands framework, \
S3 for data sources, Lambda or ECS for agent execution, and EventBridge for orchestration.",
    "security": "The customer needs a security posture review and remediation. Focus on \
Security Hub, GuardDuty, Config rules, IAM Access Analyzer, Macie for PII detection, \
CloudTrail, VPC Flow Logs, and a prioritised remediation roadmap.",
    "education": "The customer is in the education sector (school, MAT, university). Focus \
on AWS-managed services to reduce IT overhead, compliance with UK education data standards, \
and cost-effective compute for research or student workloads.",
    "startup": "The customer is a funded startup scaling infrastructure. Focus on serverless \
(Lambda, Fargate), managed databases, auto-scaling, cost optimisation from day one, and \
CI/CD pipelines for rapid deployment.",
    "smb": "The customer is a UK SMB moving to AWS. Focus on simplicity, cost control, \
managed services to reduce operational burden, and a migration from on-premises or a \
legacy hosting provider.",
}


def _get_service_context(service_type: str) -> str:
    """Return service-type-specific context for the architecture prompt."""
    return SERVICE_TYPE_CONTEXT.get(
        service_type.lower(),
        "The customer needs an AWS architecture appropriate for a UK mid-market company.",
    )


def _call_bedrock(requirements: str, service_type: str, company: str) -> Optional[str]:
    """Synchronous Bedrock converse call — run via asyncio.to_thread."""
    try:
        client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        service_context = _get_service_context(service_type)

        user_message = (
            f"Customer: {company}\n"
            f"Service type: {service_type}\n\n"
            f"Context: {service_context}\n\n"
            f"Customer requirements: {requirements}\n\n"
            "Please design an AWS architecture for this customer."
        )

        response = client.converse(
            modelId=BEDROCK_MODEL,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 1500, "temperature": 0.2},
        )

        output = response.get("output", {}).get("message", {}).get("content", [])
        if output and output[0].get("type") == "text":
            return output[0]["text"]

        logger.error(f"Bedrock returned unexpected structure: {response}")
        return None

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        logger.error(f"Bedrock ClientError ({error_code}): {e}")
        return None
    except Exception as e:
        logger.error(f"Bedrock architecture generation failed: {e}")
        return None


async def generate_architecture(
    requirements: str,
    service_type: str,
    company: str,
) -> Optional[str]:
    """Generate an AWS architecture recommendation for SOW inclusion.

    Args:
        requirements: Customer pain points and technical requirements (free text)
        service_type: migration | vmware | msp | agentbakery | security | startup | smb | etc.
        company: Customer company name

    Returns:
        Markdown string with architecture overview, ASCII diagram, service list,
        key decisions, security notes, and funding opportunities.
        Returns None if Bedrock is unavailable.
    """
    logger.info(f"Generating architecture: {company} | {service_type}")
    result = await asyncio.to_thread(_call_bedrock, requirements, service_type, company)
    if result:
        logger.info(f"Architecture generated for {company} ({len(result)} chars)")
    else:
        logger.warning(f"Architecture generation failed for {company} — Bedrock unavailable")
    return result
