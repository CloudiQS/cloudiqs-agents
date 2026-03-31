"""
Unit tests for bridge config and secrets management.
"""
import pytest
from unittest.mock import patch, MagicMock


def test_is_dummy_returns_true_for_dummy():
    from app.config import is_dummy
    assert is_dummy("DUMMY") is True
    assert is_dummy("dummy") is True
    assert is_dummy("") is True
    assert is_dummy(None) is True


def test_is_dummy_returns_false_for_real_value():
    from app.config import is_dummy
    assert is_dummy("real-api-key-abc123") is False
    assert is_dummy("sk-somekey") is False


def test_get_secret_returns_dummy_when_no_credentials():
    """With no AWS credentials, get_secret should return DUMMY gracefully."""
    from app.config import get_secret
    # Override the stack name to avoid real Secrets Manager calls
    with patch("app.config._get_client") as mock_client:
        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = Exception("No credentials")
        mock_client.return_value = mock_sm
        # Clear cache first
        import app.config as cfg
        cfg._cache.clear()
        result = get_secret("test/some-key")
    assert result == "DUMMY"


def test_get_secret_caches_result():
    """Second call to get_secret for same key should use cache, not call Secrets Manager."""
    from app.config import get_secret
    import app.config as cfg

    with patch("app.config._get_client") as mock_client:
        mock_sm = MagicMock()
        mock_sm.get_secret_value.return_value = {"SecretString": "cached-value"}
        mock_client.return_value = mock_sm
        cfg._cache.clear()

        val1 = get_secret("hubspot/api-key")
        val2 = get_secret("hubspot/api-key")

    assert val1 == "cached-value"
    assert val2 == "cached-value"
    # Should only have called Secrets Manager once
    assert mock_sm.get_secret_value.call_count == 1


def test_get_secret_falls_back_to_env(monkeypatch):
    """If Secrets Manager fails, get_secret should check environment variables."""
    import app.config as cfg

    with patch("app.config._get_client") as mock_client:
        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = Exception("SM unavailable")
        mock_client.return_value = mock_sm
        cfg._cache.clear()

        monkeypatch.setenv("HUBSPOT_API_KEY", "env-fallback-key")
        result = get_secret("hubspot/api-key")

    # Should fall back to env var (HUBSPOT_API_KEY)
    assert result == "env-fallback-key"


def _get_secret_import():
    from app.config import get_secret
    return get_secret
