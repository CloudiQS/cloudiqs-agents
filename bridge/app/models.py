"""
Data models for the Bridge API.
All fields are optional except email and company - agents provide what they can.
"""

from typing import Optional
from pydantic import BaseModel, Field


class LeadPayload(BaseModel):
    """Full lead payload from SDR agents."""
    # Required
    email: str
    company: str

    # Contact
    contact: str = ""
    job_title: str = ""
    phone: str = ""
    company_phone: str = ""
    general_phone: str = ""                      # General enquiry line (separate from switchboard)
    linkedin: str = ""                           # Primary DM LinkedIn URL
    linkedin_url: str = ""                       # Alias for backwards compatibility

    # Company data
    website: str = ""
    location: str = "GB"
    postal_code: str = ""
    employees: Optional[int] = None
    revenue: str = ""
    companies_house_number: str = ""
    sic_codes: str = ""                          # From Companies House API
    sic_code: str = ""                           # Alias for backwards compatibility
    company_news: str = ""
    company_description: str = ""
    founded_year: Optional[str] = None           # Incorporation year (string to accept "2018")

    # SDR enrichment
    campaign: str = "msp"
    signal: str = ""
    pain: str = ""
    play: str = ""
    hook: str = ""
    icp_score: int = 0
    tech_stack: str = ""

    # AWS intelligence (from POST /ace/customer-lookup)
    aws_customer: Optional[bool] = None          # True if confirmed AWS customer
    aws_services: str = ""                        # Known AWS services in use
    aws_region: str = ""                          # Primary deployment region
    aws_spend: str = ""                           # Estimated monthly AWS spend
    aws_account_owner: str = ""                   # AWS account manager name
    aws_existing_opps: str = ""                   # Existing ACE opportunities (raw)
    ace_opportunities: str = ""                   # Formatted ACE pipeline summary

    # Deep research intelligence
    recent_news: Optional[list] = None          # [str, ...]
    talk_track: str = ""
    linkedin_activity: str = ""
    decision_maker_background: str = ""
    other_contacts: Optional[list] = None       # [{name, title, email, phone, linkedin, background}]

    # Email content
    email_1_body: str = ""

    # Secondary contact (multi-threading)
    secondary_contact: str = ""
    secondary_title: str = ""
    secondary_linkedin: str = ""

    # Deal naming (agent-generated)
    deal_name: str = ""


class IngestPayload(BaseModel):
    """Simplified payload for S3 upload / bulk ingestion."""
    company: str
    campaign: str = "triage"
    website: str = ""
    contact: str = ""
    email: str = ""
    job_title: str = ""
    notes: str = ""
    source: str = "s3-upload"


class WebhookPayload(BaseModel):
    """Instantly webhook event."""
    event_type: str = ""
    email: str = ""
    campaign_id: str = ""
    timestamp: str = ""
    reply_text: str = ""
