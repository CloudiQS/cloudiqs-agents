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
    linkedin_url: str = ""

    # Company data
    website: str = ""
    location: str = "GB"
    postal_code: str = ""
    employees: Optional[int] = None
    revenue: str = ""
    companies_house_number: str = ""
    sic_code: str = ""
    company_news: str = ""

    # SDR enrichment
    campaign: str = "msp"
    signal: str = ""
    pain: str = ""
    play: str = ""
    hook: str = ""
    icp_score: int = 0
    tech_stack: str = ""
    aws_services: str = ""

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
