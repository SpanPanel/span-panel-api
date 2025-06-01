"""Tests for the PySpan SPAN Panel API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelTimeoutError,
)


class TestSpanPanelClient:
    """Test suite for SpanPanelClient."""

    def test_client_initialization(self):
        """Test that the client can be initialized."""
        client = SpanPanelClient("192.168.1.100")
        assert client is not None
        assert client._host == "192.168.1.100"
        assert client._port == 80
        assert client._timeout == 30.0
        assert client._use_ssl is False
        assert client._base_url == "http://192.168.1.100:80"

    def test_client_initialization_with_ssl(self):
        """Test client initialization with SSL."""
        client = SpanPanelClient("192.168.1.100", port=443, use_ssl=True)
        assert client._port == 443
        assert client._use_ssl is True
        assert client._base_url == "https://192.168.1.100:443"

    def test_client_initialization_with_timeout(self):
        """Test client initialization with custom timeout."""
        client = SpanPanelClient("192.168.1.100", timeout=60)
        assert client._timeout == 60

    @pytest.mark.asyncio
    async def test_authenticate_success(self):
        """Test successful authentication."""
        client = SpanPanelClient("192.168.1.100")

        with patch.object(client, "_get_api") as mock_get_api:
            mock_api = AsyncMock()
            mock_get_api.return_value.__aenter__.return_value = mock_api

            # Mock successful auth response
            auth_response = MagicMock()
            auth_response.access_token = "test-token"
            auth_response.token_type = "Bearer"
            mock_api.generate_jwt_api_v1_auth_register_post = AsyncMock(
                return_value=auth_response
            )

            result = await client.authenticate("test-app", "Test Application")

            assert result == auth_response
            assert client._access_token == "test-token"
            mock_api.generate_jwt_api_v1_auth_register_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_authenticate_failure(self):
        """Test authentication failure."""
        client = SpanPanelClient("192.168.1.100")

        # Mock _get_api to raise SpanPanelAuthError directly
        with patch.object(client, "_get_api") as mock_get_api:
            mock_get_api.side_effect = SpanPanelAuthError("Authentication failed")

            with pytest.raises(SpanPanelAuthError):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_get_status_success(self):
        """Test successful status retrieval."""
        client = SpanPanelClient("192.168.1.100")

        with patch.object(client, "_get_api") as mock_get_api:
            mock_api = AsyncMock()
            mock_get_api.return_value.__aenter__.return_value = mock_api

            # Mock status response
            status_response = MagicMock()
            status_response.system = MagicMock(manufacturer="SPAN")
            mock_api.system_status_api_v1_status_get = AsyncMock(
                return_value=status_response
            )

            result = await client.get_status()

            assert result == status_response
            mock_api.system_status_api_v1_status_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_circuits_success(self):
        """Test successful circuits retrieval."""
        client = SpanPanelClient("192.168.1.100")

        with patch.object(client, "_get_api") as mock_get_api:
            mock_api = AsyncMock()
            mock_get_api.return_value.__aenter__.return_value = mock_api

            # Mock circuits response
            circuits_response = MagicMock()
            circuits_response.circuits = {
                "1": MagicMock(name="Main", instant_power_w=1500)
            }
            mock_api.get_circuits_api_v1_circuits_get = AsyncMock(
                return_value=circuits_response
            )

            result = await client.get_circuits()

            assert result == circuits_response
            mock_api.get_circuits_api_v1_circuits_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_error_handling(self):
        """Test connection error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch.object(client, "_get_api") as mock_get_api:
            # Mock connection error
            mock_get_api.side_effect = SpanPanelConnectionError("Connection failed")

            with pytest.raises(SpanPanelConnectionError):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_timeout_error_handling(self):
        """Test timeout error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch.object(client, "_get_api") as mock_get_api:
            # Mock timeout error
            mock_get_api.side_effect = SpanPanelTimeoutError("Request timed out")

            with pytest.raises(SpanPanelTimeoutError):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """Test API error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch.object(client, "_get_api") as mock_get_api:
            # Mock API error
            mock_get_api.side_effect = SpanPanelAPIError(
                "Server Error", status_code=500
            )

            with pytest.raises(SpanPanelAPIError):
                await client.get_status()

    def test_imports(self):
        """Test that all necessary components can be imported."""
        assert SpanPanelClient is not None
        assert SpanPanelAPIError is not None
        assert SpanPanelAuthError is not None
        assert SpanPanelConnectionError is not None
        assert SpanPanelTimeoutError is not None


def test_client_with_custom_port():
    """Test client initialization with custom port."""
    client = SpanPanelClient("192.168.1.100", port=8080)
    assert client._port == 8080


def test_client_with_ssl():
    """Test client initialization with SSL."""
    client = SpanPanelClient("192.168.1.100", use_ssl=True)
    assert client._use_ssl is True


def test_exceptions_inheritance():
    """Test that our custom exceptions inherit properly."""
    from span_panel_api.exceptions import (
        SpanPanelAPIError,
        SpanPanelAuthError,
        SpanPanelConnectionError,
        SpanPanelError,
        SpanPanelTimeoutError,
    )

    # Test inheritance
    assert issubclass(SpanPanelConnectionError, SpanPanelError)
    assert issubclass(SpanPanelAuthError, SpanPanelError)
    assert issubclass(SpanPanelTimeoutError, SpanPanelError)
    assert issubclass(SpanPanelAPIError, SpanPanelError)


def test_api_error_with_status_code():
    """Test SpanPanelAPIError with status code."""
    from span_panel_api.exceptions import SpanPanelAPIError

    error = SpanPanelAPIError("Test error", status_code=404)
    assert str(error) == "Test error"
    assert error.status_code == 404


def test_imports_work():
    """Test that imports work correctly."""
    # Test wrapper imports
    from span_panel_api import SpanPanelClient
    from span_panel_api.exceptions import SpanPanelError

    # Verify types
    assert SpanPanelClient is not None
    assert SpanPanelError is not None
