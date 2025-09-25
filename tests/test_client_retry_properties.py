"""Tests for client retry properties and configuration."""

import pytest
from src.span_panel_api.client import SpanPanelClient


class TestClientRetryProperties:
    """Tests for client retry configuration properties."""

    def test_property_getters(self):
        """Test property getters."""
        client = SpanPanelClient(
            host="test",
            retries=3,
            retry_timeout=1.5,
            retry_backoff_multiplier=2.0,
        )

        assert client.retries == 3
        assert client.retry_timeout == 1.5
        assert client.retry_backoff_multiplier == 2.0

    def test_retry_property_setters_validation(self):
        """Test retry property setters validation."""
        client = SpanPanelClient(host="test")

        # Test retries setter validation
        with pytest.raises(ValueError, match="retries must be non-negative"):
            client.retries = -1

        # Test retry_timeout setter validation
        with pytest.raises(ValueError, match="retry_timeout must be non-negative"):
            client.retry_timeout = -1.0

        # Test retry_backoff_multiplier setter validation
        with pytest.raises(ValueError, match="retry_backoff_multiplier must be at least 1"):
            client.retry_backoff_multiplier = 0.5

        # Test valid setters
        client.retries = 5
        client.retry_timeout = 2.0
        client.retry_backoff_multiplier = 2.0

        assert client.retries == 5
        assert client.retry_timeout == 2.0
        assert client.retry_backoff_multiplier == 2.0

    def test_retry_configuration_validation_at_init(self):
        """Test retry configuration validation during initialization."""
        # Test invalid retries
        with pytest.raises(ValueError, match="retries must be non-negative"):
            SpanPanelClient(host="test", retries=-1)

        # Test invalid retry_timeout
        with pytest.raises(ValueError, match="retry_timeout must be non-negative"):
            SpanPanelClient(host="test", retry_timeout=-1.0)

        # Test invalid retry_backoff_multiplier
        with pytest.raises(ValueError, match="retry_backoff_multiplier must be at least 1"):
            SpanPanelClient(host="test", retry_backoff_multiplier=0.5)
