"""
AWS Partner Central ACE opportunity management.
Cross-account role assumption: engine account -> partner account (349440382087).

ALL enum values verified against official AWS documentation:
- CreateOpportunity: docs.aws.amazon.com/partner-central/latest/APIReference/API_CreateOpportunity.html
- Project type: docs.aws.amazon.com/partner-central/latest/APIReference/API_Project.html
- Marketing type: docs.aws.amazon.com/partner-central/latest/APIReference/API_Marketing.html

Design decisions:
1. ACE opportunities are created ONLY when a deal reaches Qualified in HubSpot.
   The ace-create agent detects the stage change and calls POST /ace/create.
2. Raw API is used for creates (not MCP) because MCP requires human-in-the-loop
   approval which would block autonomous agent execution. The human already
   approved by moving the deal to Qualified stage.
3. MCP is used for intelligence (profiles, funding, pipeline insights) via
   the bridge /mcp/* endpoints and mcp_client.py.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List

import boto3
from botocore.exceptions import ClientError

from app.config import REGION, get_secret, is_dummy

logger = logging.getLogger("bridge")


# ══════════════════════════════════════════════════════════════════════
# VERIFIED ENUM VALUES — copied from AWS API documentation
# Do not change these without checking the docs first.
# ══════════════════════════════════════════════════════════════════════

# Project.SalesActivities — API_Project.html
VALID_SALES_ACTIVITIES = [
    "Initialized discussions with customer",
    "Customer has shown interest in solution",
    "Conducted POC / Demo",
    "In evaluation / planning stage",
    "Agreed on solution to Business Problem",
    "Completed Action Plan",
    "Finalized Deployment Need",
    "SOW Signed",
]

# Project.DeliveryModels — API_Project.html
VALID_DELIVERY_MODELS = [
    "SaaS or PaaS",
    "BYOL or AMI",
    "Managed Services",
    "Professional Services",
    "Resell",
    "Other",
]

# Project.CustomerUseCase — API_Project.html
VALID_USE_CASES = [
    "AI Machine Learning and Analytics",
    "Archiving",
    "Big Data: Data Warehouse/Data Integration/ETL/Data Lake/BI",
    "Blockchain",
    "Business Applications: Mainframe Modernization",
    "Business Applications & Contact Center",
    "Business Applications & SAP Production",
    "Centralized Operations Management",
    "Cloud Management Tools",
    "Cloud Management Tools & DevOps with Continuous Integration & Continuous Delivery (CICD)",
    "Configuration, Compliance & Auditing",
    "Connected Services",
    "Containers & Serverless",
    "Content Delivery & Edge Services",
    "Database",
    "Edge Computing/End User Computing",
    "Energy",
    "Enterprise Governance & Controls",
    "Enterprise Resource Planning",
    "Financial Services",
    "Healthcare and Life Sciences",
    "High Performance Computing",
    "Hybrid Application Platform",
    "Industrial Software",
    "IOT",
    "Manufacturing, Supply Chain and Operations",
    "Media & High performance computing (HPC)",
    "Migration/Database Migration",
    "Monitoring, logging and performance",
    "Monitoring & Observability",
    "Networking",
    "Outpost",
    "SAP",
    "Security & Compliance",
    "Storage & Backup",
    "Training",
    "VMC",
    "VMWare",
    "Web development & DevOps",
]

# Marketing.Source — API_Marketing.html
VALID_MARKETING_SOURCES = ["Marketing Activity", "None"]

# Marketing.AwsFundingUsed — API_Marketing.html
VALID_FUNDING_USED = ["Yes", "No"]

# OpportunityType — API_CreateOpportunity.html
VALID_OPPORTUNITY_TYPES = ["Net New Business", "Flat Renewal", "Expansion"]

# Origin — API_CreateOpportunity.html
VALID_ORIGINS = ["AWS Referral", "Partner Referral"]

# PrimaryNeedsFromAws — API_CreateOpportunity.html (array)
VALID_PRIMARY_NEEDS = [
    "Co-Sell - Architectural Validation",
    "Co-Sell - Business Presentation",
    "Co-Sell - Competitive Information",
    "Co-Sell - Pricing Assistance",
    "Co-Sell - Technical Consultation",
    "Co-Sell - Total Cost of Ownership Evaluation",
    "Co-Sell - Deal Support",
    "Co-Sell - Support for Public Tender / RFx",
]

# LifeCycle.Stage — API_CreateOpportunity.html
VALID_STAGES = [
    "Prospect",
    "Qualified",
    "Technical Validation",
    "Business Validation",
    "Committed",
    "Launched",
    "Closed Lost",
]

# Project.CompetitorName — API_Project.html
VALID_COMPETITORS = [
    "Oracle Cloud", "On-Prem", "Co-location", "Akamai", "AliCloud",
    "Google Cloud Platform", "IBM Softlayer", "Microsoft Azure",
    "Other- Cost Optimization", "No Competition", "*Other",
]

# Project.ApnPrograms — API_Project.html
VALID_APN_PROGRAMS = [
    "APN Immersion Days", "APN Solution Space", "ATO (Authority to Operate)",
    "AWS Marketplace Campaign", "IS Immersion Day SFID Program",
    "ISV Workload Migration", "Migration Acceleration Program", "P3",
    "Partner Launch Initiative", "Partner Opportunity Acceleration Funded",
    "The Next Smart", "VMware Cloud on AWS", "Well-Architected",
    "Windows", "Workspaces/AppStream Accelerator Program", "WWPS NDPP",
]

# NationalSecurity — API_CreateOpportunity.html
VALID_NATIONAL_SECURITY = ["Yes", "No"]


# ══════════════════════════════════════════════════════════════════════
# CAMPAIGN MAPPINGS — map SDR campaign names to verified enum values
# Solution IDs from Partner Central export (March 2026)
# ══════════════════════════════════════════════════════════════════════

CAMPAIGN_USE_CASE = {
    "vmware":       "VMWare",
    "switcher":     "Migration/Database Migration",
    "storage":      "Storage & Backup",
    "agentbakery":  "AI Machine Learning and Analytics",
    "security":     "Security & Compliance",
    "greenfield":   "Cloud Management Tools",
    "msp":          "Monitoring, logging and performance",
    "education":    "Cloud Management Tools",
    "startup":      "Cloud Management Tools",
    "smb":          "Cloud Management Tools",
    "awsfunding":   "Cloud Management Tools",
}

CAMPAIGN_DELIVERY_MODEL = {
    "msp":          "Managed Services",
    "agentbakery":  "SaaS or PaaS",
    "security":     "Managed Services",
}

CAMPAIGN_APN_PROGRAM = {
    "vmware":       "VMware Cloud on AWS",
    "switcher":     "Migration Acceleration Program",
    "greenfield":   "Migration Acceleration Program",
    "storage":      "Migration Acceleration Program",
}

CAMPAIGN_COMPETITOR = {
    "vmware":   "Other- Cost Optimization",
    "switcher": "Other- Cost Optimization",
    "security": "Microsoft Azure",
}

CAMPAIGN_SOLUTION_ID = {
    "vmware":       "S-0058978",
    "msp":          "S-0058018",
    "greenfield":   "S-0058951",
    "startup":      "S-0058018",
    "storage":      "S-0052523",
    "smb":          "S-0058018",
    "education":    "S-0058018",
    "agentbakery":  "S-0081913",
    "switcher":     "S-0042108",
    "awsfunding":   "S-0058018",
    "security":     "S-0058951",
}

# Map HubSpot deal stages to ACE lifecycle stages
HUBSPOT_TO_ACE_STAGE = {
    "appointmentscheduled": "Prospect",       # New Lead
    "qualifiedtobuy":       "Qualified",      # Qualified
    "presentationscheduled": "Technical Validation",  # Meeting Booked
    "decisionmakerboughtin": "Business Validation",   # Proposal Sent
    "contractsent":          "Committed",     # Committed
    "closedwon":             "Launched",      # Closed Won
    "closedlost":            "Closed Lost",   # Closed Lost
}

# Map ACE stages to appropriate SalesActivities
STAGE_TO_SALES_ACTIVITY = {
    "Prospect":             "Initialized discussions with customer",
    "Qualified":            "Customer has shown interest in solution",
    "Technical Validation": "Conducted POC / Demo",
    "Business Validation":  "In evaluation / planning stage",
    "Committed":            "Agreed on solution to Business Problem",
    "Launched":             "Finalized Deployment Need",
}


# ══════════════════════════════════════════════════════════════════════
# CROSS-ACCOUNT ROLE
# ══════════════════════════════════════════════════════════════════════

def _get_partner_central_client():
    """Assume cross-account role and return a Partner Central Selling API client.

    The engine runs in one AWS account. Partner Central is registered in another.
    We assume a role in the partner account to make API calls.
    """
    role_arn = get_secret("partner-central/role-arn")
    if is_dummy(role_arn):
        logger.error("Partner Central role ARN not configured in Secrets Manager")
        return None

    try:
        sts = boto3.client("sts", region_name=REGION)
        assumed = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName=f"bridge-ace-{int(datetime.now().timestamp())}",
            DurationSeconds=900,
        )
        creds = assumed["Credentials"]
        return boto3.client(
            "partnercentral-selling",
            region_name="us-east-1",  # Partner Central API only in us-east-1
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
    except ClientError as e:
        logger.error(f"Failed to assume Partner Central role: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════════

def _validate_enum(value: str, valid_values: List[str], field_name: str) -> Optional[str]:
    """Validate a value against a list of valid enum values.
    Returns the value if valid, None if not. Logs a warning on invalid.
    """
    if not value:
        return None
    if value in valid_values:
        return value
    # Try case-insensitive match
    for v in valid_values:
        if v.lower() == value.lower():
            return v
    logger.warning(f"Invalid {field_name} value: '{value}'. Valid: {valid_values[:3]}...")
    return None


# ══════════════════════════════════════════════════════════════════════
# CREATE OPPORTUNITY
# ══════════════════════════════════════════════════════════════════════

async def create_opportunity(lead_data: dict) -> Optional[str]:
    """Create an ACE opportunity with all required fields.

    Called by ace-create agent via POST /ace/create when a deal reaches
    Qualified stage in HubSpot. The agent reads all deal data from HubSpot
    and passes it here.

    Args:
        lead_data: dict with fields from HubSpot deal. Required: company.
            Optional but recommended: email, contact, campaign, pain, signal,
            website, postal_code, location, revenue, job_title, phone,
            deal_name, companies_house_number.

    Returns:
        ACE Opportunity ID (e.g. "O1234567") or None on failure.
    """
    company = lead_data.get("company", "")
    if not company:
        logger.error("ACE create: company name is required")
        return None

    pc = _get_partner_central_client()
    if pc is None:
        return None

    campaign = lead_data.get("campaign", "msp")
    catalog = get_secret("partner-central/catalog") or "AWS"

    # Generate unique client token
    token = f"cqs-{company[:20].replace(' ', '-').lower()}-{int(datetime.now().timestamp())}"
    token = token[:64]

    # Close date: 90 days from now
    close_date = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")

    # CustomerBusinessProblem: API requires 20-2000 characters
    pain = lead_data.get("pain", "") or lead_data.get("signal", "") or ""
    if len(pain) < 20:
        pain = (
            f"{company} requires CloudiQS AWS professional services. "
            f"Signal: {lead_data.get('signal', 'inbound interest')}. "
            f"CloudiQS to conduct discovery and propose solution."
        )
    pain = pain[:2000]

    # Project title: use agent-generated deal name or construct one
    title = lead_data.get("deal_name", "")
    if not title:
        title = f"{company} - CloudiQS {campaign.title()}"
    title = title[:255]

    # Resolve campaign to verified enum values
    use_case = _validate_enum(
        CAMPAIGN_USE_CASE.get(campaign, "Cloud Management Tools"),
        VALID_USE_CASES,
        "CustomerUseCase",
    ) or "Cloud Management Tools"

    delivery_model = _validate_enum(
        CAMPAIGN_DELIVERY_MODEL.get(campaign, "Professional Services"),
        VALID_DELIVERY_MODELS,
        "DeliveryModels",
    ) or "Professional Services"

    sales_activity = _validate_enum(
        STAGE_TO_SALES_ACTIVITY.get("Prospect"),
        VALID_SALES_ACTIVITIES,
        "SalesActivities",
    ) or "Initialized discussions with customer"

    # ── Build the request ──────────────────────────────────────────

    params = {
        "Catalog": catalog,
        "ClientToken": token,
        "Origin": "Partner Referral",
        "PartnerOpportunityIdentifier": token,
        "OpportunityType": "Net New Business",
        "PrimaryNeedsFromAws": ["Co-Sell - Deal Support"],
        "LifeCycle": {
            "Stage": "Prospect",
            "TargetCloseDate": close_date,
            "NextSteps": "CloudiQS to schedule discovery call",
            "NextStepsHistory": [{
                "Time": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "Value": "Opportunity created by CloudiQS Engine",
            }],
        },
        "Customer": {
            "Account": {
                "CompanyName": company[:120],
                "Address": {
                    "CountryCode": (lead_data.get("location", "GB") or "GB")[:2].upper(),
                },
            },
        },
        "Project": {
            "Title": title,
            "CustomerBusinessProblem": pain,
            "CustomerUseCase": use_case,
            "DeliveryModels": [delivery_model],
            "SalesActivities": [sales_activity],
        },
        "Marketing": {
            "Source": "None",
            "AwsFundingUsed": "No",
        },
    }

    # ── Optional fields: only add if we have real data ─────────────

    # Postal code (required for submission, but CreateOpportunity accepts without it)
    postal_code = lead_data.get("postal_code", "")
    if postal_code:
        params["Customer"]["Account"]["Address"]["PostalCode"] = postal_code

    # Website
    website = lead_data.get("website", "")
    if website:
        params["Customer"]["Account"]["WebsiteUrl"] = website

    # Contact: only add if we have a real name and email
    contact_name = lead_data.get("contact", "")
    contact_email = lead_data.get("email", "")
    if contact_name and contact_email and "@" in contact_email:
        parts = contact_name.strip().split(" ", 1)
        contact_obj = {
            "Email": contact_email,
            "FirstName": parts[0],
            "LastName": parts[1] if len(parts) > 1 else parts[0],
        }
        # BusinessTitle is the correct field name (not "Title")
        job_title = lead_data.get("job_title", "")
        if job_title:
            contact_obj["BusinessTitle"] = job_title
        phone = lead_data.get("phone", "")
        if phone:
            contact_obj["Phone"] = phone

        params["Customer"]["Contacts"] = [contact_obj]

    # Expected spend
    revenue = lead_data.get("revenue", "")
    if revenue:
        params["Project"]["ExpectedCustomerSpend"] = [{
            "Amount": str(revenue),
            "CurrencyCode": "GBP",
            "Frequency": "Monthly",
            "TargetCompany": company[:80],
        }]

    # APN programs (campaign-specific)
    apn_program = CAMPAIGN_APN_PROGRAM.get(campaign)
    if apn_program:
        validated = _validate_enum(apn_program, VALID_APN_PROGRAMS, "ApnPrograms")
        if validated:
            params["Project"]["ApnPrograms"] = [validated]

    # Competitor
    competitor = CAMPAIGN_COMPETITOR.get(campaign)
    if competitor:
        validated = _validate_enum(competitor, VALID_COMPETITORS, "CompetitorName")
        if validated:
            params["Project"]["CompetitorName"] = validated

    # ── Make the API call ──────────────────────────────────────────

    try:
        resp = pc.create_opportunity(**params)
        opp_id = resp.get("Id", "")

        if not opp_id:
            logger.error(f"ACE create returned no ID for {company}")
            return None

        logger.info(f"ACE opportunity created: {company} -> {opp_id}")

        # Associate solution (required step before submission)
        solution_id = CAMPAIGN_SOLUTION_ID.get(campaign, CAMPAIGN_SOLUTION_ID["msp"])
        try:
            pc.associate_opportunity(
                Catalog=catalog,
                OpportunityIdentifier=opp_id,
                RelatedEntityType="Solutions",
                RelatedEntityIdentifier=solution_id,
            )
            logger.info(f"ACE solution associated: {opp_id} -> {solution_id}")
        except ClientError as e:
            # Non-fatal: opportunity exists but solution not linked
            logger.warning(f"ACE solution association failed (non-fatal): {e}")

        return opp_id

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", "No message")
        logger.error(f"ACE create failed for {company}: [{error_code}] {error_msg}")

        # Log the exact params that failed for debugging
        if error_code == "ValidationException":
            logger.error(f"ACE validation failure. Check enum values. Params sent: {params}")

        return None

    except Exception as e:
        logger.error(f"ACE unexpected error for {company}: {type(e).__name__}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# GET OPPORTUNITY STAGE
# ══════════════════════════════════════════════════════════════════════

async def get_opportunity_stage(opp_id: str) -> Optional[str]:
    """Return the current LifeCycle.Stage for an ACE opportunity.

    Args:
        opp_id: ACE opportunity ID (e.g. "O14608392")

    Returns:
        Stage string (e.g. "Qualified", "Committed") or None on failure.
    """
    pc = _get_partner_central_client()
    if pc is None:
        return None

    catalog = get_secret("partner-central/catalog") or "AWS"

    try:
        resp = pc.get_opportunity(Catalog=catalog, Identifier=opp_id)
        stage = (
            resp.get("LifeCycle", {}).get("Stage")
            or resp.get("Opportunity", {}).get("LifeCycle", {}).get("Stage")
        )
        return stage
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.warning(f"ACE get_opportunity_stage failed for {opp_id}: [{error_code}]")
        return None
    except Exception as e:
        logger.warning(f"ACE get_opportunity_stage unexpected error for {opp_id}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# UPDATE OPPORTUNITY STAGE
# ══════════════════════════════════════════════════════════════════════

async def update_opportunity_stage(opp_id: str, stage: str) -> bool:
    """Update an existing ACE opportunity stage.

    Args:
        opp_id: ACE opportunity ID (e.g. "O1234567")
        stage: HubSpot deal stage name or ACE stage name.
            Accepts both formats:
            - HubSpot: "qualifiedtobuy", "contractsent", "closedwon"
            - ACE: "Qualified", "Committed", "Launched"

    Returns:
        True if update succeeded, False otherwise.
    """
    # Resolve stage name
    ace_stage = None

    # Check if it is already a valid ACE stage
    if stage in VALID_STAGES:
        ace_stage = stage
    else:
        # Try HubSpot stage mapping
        ace_stage = HUBSPOT_TO_ACE_STAGE.get(stage)

    if not ace_stage:
        logger.warning(f"Cannot map stage '{stage}' to ACE. Valid HubSpot stages: {list(HUBSPOT_TO_ACE_STAGE.keys())}")
        return False

    pc = _get_partner_central_client()
    if pc is None:
        return False

    catalog = get_secret("partner-central/catalog") or "AWS"

    # Build update params
    update_params = {
        "Catalog": catalog,
        "Identifier": opp_id,
        "LifeCycle": {
            "Stage": ace_stage,
        },
    }

    # Update SalesActivities to match the new stage
    activity = STAGE_TO_SALES_ACTIVITY.get(ace_stage)
    if activity:
        update_params["Project"] = {"SalesActivities": [activity]}

    try:
        pc.update_opportunity(**update_params)
        logger.info(f"ACE stage updated: {opp_id} -> {ace_stage}")
        return True

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", "No message")
        logger.error(f"ACE stage update failed for {opp_id}: [{error_code}] {error_msg}")
        return False

    except Exception as e:
        logger.error(f"ACE stage update unexpected error for {opp_id}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
# UPDATE OPPORTUNITY FIELDS
# ══════════════════════════════════════════════════════════════════════

async def update_opportunity_fields(opp_id: str, fields: dict) -> bool:
    """Update free-form fields on an existing ACE opportunity.

    Supported fields (all optional):
        customer_business_problem (str) — CustomerBusinessProblem, 20-2000 chars
        website (str)                   — Customer.Account.WebsiteUrl

    Args:
        opp_id: ACE opportunity ID (e.g. "O14608392")
        fields: dict of field names to new values

    Returns:
        True if update succeeded, False otherwise.
    """
    pc = _get_partner_central_client()
    if pc is None:
        return False

    catalog = get_secret("partner-central/catalog") or "AWS"
    update_params: dict = {"Catalog": catalog, "Identifier": opp_id}

    customer_business_problem = fields.get("customer_business_problem", "")
    website = fields.get("website", "")

    if customer_business_problem:
        # API requires 20-2000 characters
        if len(customer_business_problem) < 20:
            customer_business_problem = customer_business_problem.ljust(20)
        update_params["Project"] = {
            "CustomerBusinessProblem": customer_business_problem[:2000]
        }

    if website:
        update_params["Customer"] = {
            "Account": {"WebsiteUrl": website}
        }

    if len(update_params) <= 2:
        logger.warning(f"ACE update: no recognised fields provided for {opp_id}")
        return False

    try:
        pc.update_opportunity(**update_params)
        logger.info(f"ACE fields updated: {opp_id} -> {list(fields.keys())}")
        return True

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        error_msg = e.response.get("Error", {}).get("Message", "No message")
        logger.error(f"ACE field update failed for {opp_id}: [{error_code}] {error_msg}")
        return False

    except Exception as e:
        logger.error(f"ACE field update unexpected error for {opp_id}: {e}")
        return False
