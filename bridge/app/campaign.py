"""
Campaign mappings for ACE, Instantly, and HubSpot.
These map SDR campaign names to AWS Partner Central solution IDs,
use cases, delivery models, and Instantly campaign secret keys.

Solution IDs verified from Partner Central Export (March 2026).
"""

# Campaign -> Instantly campaign secret key in Secrets Manager
INSTANTLY_CAMPAIGN_MAP = {
    "vmware": "instantly/vmware-campaign-id",
    "msp": "instantly/msp-campaign-id",
    "greenfield": "instantly/greenfield-campaign-id",
    "startup": "instantly/startup-campaign-id",
    "storage": "instantly/storage-campaign-id",
    "smb": "instantly/smb-campaign-id",
    "education": "instantly/education-campaign-id",
    "agentbakery": "instantly/agentbakery-campaign-id",
    "switcher": "instantly/switcher-campaign-id",
    "awsfunding": "instantly/awsfunding-campaign-id",
    "security": "instantly/security-campaign-id",
}

# Campaign -> Partner Central Solution ID
# Source: Partner Central > Solutions > Export (March 2026)
SOLUTION_MAP = {
    "vmware": "S-0058978",       # Server Migration
    "msp": "S-0058018",          # SMB
    "greenfield": "S-0058951",   # Secure Landing Zone
    "startup": "S-0058018",      # SMB
    "storage": "S-0052523",      # Transfer Family
    "smb": "S-0058018",          # SMB
    "education": "S-0058018",    # SMB
    "agentbakery": "S-0081913",  # GenAI Consulting
    "switcher": "S-0042108",     # Migration Consulting
    "awsfunding": "S-0058018",   # SMB
    "security": "S-0058951",     # Secure Landing Zone
}

# Campaign -> ACE Use Case (enum values from Partner Central)
USECASE_MAP = {
    "vmware": "Migration",
    "switcher": "Migration",
    "storage": "Storage",
    "agentbakery": "AI Machine Learning and Analytics",
    "security": "Configuration, Compliance & Auditing",
}
DEFAULT_USECASE = "Cloud Management Tools"

# Campaign -> ACE Delivery Model
DELIVERY_MODEL_MAP = {
    "msp": "Managed Services",
    "agentbakery": "SaaS or PaaS",
}
DEFAULT_DELIVERY_MODEL = "Other"

# Campaign -> Industry Vertical (default, agent can override)
INDUSTRY_MAP = {
    "education": "Education",
    "security": "Financial Services",
    "agentbakery": "Software & Internet",
}
DEFAULT_INDUSTRY = "Other"

# AWS Segment codes for deal names
# GB - SEG - Company - Solution - Quarter - Revenue - Tags
SEGMENT_MAP = {
    "startup": "SUP",
    "smb": "SMB",
    "education": "EDU",
}
DEFAULT_SEGMENT = "ENT"


def get_solution_id(campaign: str) -> str:
    return SOLUTION_MAP.get(campaign, SOLUTION_MAP["msp"])


def get_use_case(campaign: str) -> str:
    return USECASE_MAP.get(campaign, DEFAULT_USECASE)


def get_delivery_model(campaign: str) -> str:
    return DELIVERY_MODEL_MAP.get(campaign, DEFAULT_DELIVERY_MODEL)


def get_industry(campaign: str) -> str:
    return INDUSTRY_MAP.get(campaign, DEFAULT_INDUSTRY)


def get_segment(campaign: str) -> str:
    return SEGMENT_MAP.get(campaign, DEFAULT_SEGMENT)
