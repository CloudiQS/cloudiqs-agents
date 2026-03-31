"""
Configuration and secrets management.
Reads all secrets from AWS Secrets Manager. Falls back to env vars for local dev.
"""

import os
import re
import logging
import functools

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("bridge")

REGION = os.environ.get("AWS_DEFAULT_REGION", "eu-west-1")
STACK = os.environ.get("STACK_NAME", "cloudiqs-engine")
SECRET_PREFIX = f"cloudiqs/{STACK}/"

_secrets_client = None
_cache = {}


def _get_client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager", region_name=REGION)
    return _secrets_client


def get_secret(short_key: str) -> str:
    """Retrieve a secret by short key. E.g. get_secret('hubspot/api-key')."""
    full_key = SECRET_PREFIX + short_key
    if full_key in _cache:
        return _cache[full_key]

    # Try Secrets Manager
    try:
        resp = _get_client().get_secret_value(SecretId=full_key)
        val = resp["SecretString"]
        _cache[full_key] = val
        return val
    except Exception:
        # Catches ClientError (key not found), credential errors (SSO expiry,
        # no instance profile locally), and any connectivity failures.
        pass

    # Fallback to env var: hubspot/api-key -> HUBSPOT_API_KEY
    env_key = short_key.replace("/", "_").replace("-", "_").upper()
    val = os.environ.get(env_key, "DUMMY")
    _cache[full_key] = val
    return val


def is_dummy(val: str) -> bool:
    """Check if a secret value is a placeholder."""
    if not val:
        return True
    return val.strip().upper() in ("DUMMY", "YOUR_KEY", "CHANGEME", "")


def is_valid_uuid(val: str) -> bool:
    """Check if a string looks like a UUID."""
    pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    return bool(pattern.match(val.strip())) if val else False
