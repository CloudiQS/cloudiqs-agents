"""
Shared test fixtures for CloudiQS Bridge tests.
"""
import pytest
from fastapi.testclient import TestClient

TEST_API_KEY = "test-key-cloudiqs-12345"


@pytest.fixture(autouse=True)
def patch_auth(monkeypatch):
    """Patch bridge API key loading and enable auth for all tests.

    Patches _load_bridge_api_key (not _BRIDGE_API_KEY directly) so that when
    the FastAPI lifespan runs inside TestClient it receives TEST_API_KEY rather
    than a randomly-generated fallback key.  Directly patching the module-level
    variable would be overwritten by lifespan's global reassignment.
    """
    import app.main as main_module
    monkeypatch.setattr(main_module, "_BRIDGE_AUTH_ENABLED", True)
    monkeypatch.setattr(main_module, "_load_bridge_api_key", lambda: TEST_API_KEY)


@pytest.fixture
def client():
    """FastAPI test client with application lifespan skipped (no Secrets Manager needed)."""
    from app.main import app
    # Use TestClient which handles startup/shutdown events
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def auth(client):
    """Pre-configured auth headers for authenticated requests."""
    return {"X-API-Key": TEST_API_KEY}


@pytest.fixture
def valid_lead():
    """Minimal valid lead payload."""
    return {
        "email": "test@example.com",
        "company": "Test Ltd",
        "contact": "John Smith",
        "campaign": "msp",
        "signal": "hiring cloud engineer",
        "pain": "VMware end of life, need to migrate",
        "play": "Migration",
        "icp_score": 7,
    }
