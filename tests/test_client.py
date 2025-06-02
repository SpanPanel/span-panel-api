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

        # Mock the API function directly
        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            # Mock successful auth response
            auth_response = MagicMock()
            auth_response.access_token = "test-token"
            auth_response.token_type = "Bearer"
            mock_auth.asyncio = AsyncMock(return_value=auth_response)

            result = await client.authenticate("test-app", "Test Application")

            assert result == auth_response
            assert client._access_token == "test-token"
            mock_auth.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_authenticate_failure(self):
        """Test authentication failure."""
        client = SpanPanelClient("192.168.1.100")

        # Mock the API function to raise an exception
        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            import httpx

            mock_auth.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "401 Unauthorized", request=MagicMock(), response=MagicMock()
                )
            )

            with pytest.raises(
                SpanPanelAuthError
            ):  # Our wrapper converts 401 errors to SpanPanelAuthError
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_get_status_success(self):
        """Test successful status retrieval."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            # Mock status response
            status_response = MagicMock()
            status_response.system = MagicMock(manufacturer="SPAN")
            mock_status.asyncio = AsyncMock(return_value=status_response)

            result = await client.get_status()

            assert result == status_response
            mock_status.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_panel_state_success(self):
        """Test successful panel state retrieval."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Mock panel state response
            panel_response = MagicMock()
            panel_response.main_relay_state = "CLOSED"
            panel_response.instant_grid_power_w = 5000
            mock_panel.asyncio = AsyncMock(return_value=panel_response)

            result = await client.get_panel_state()

            assert result == panel_response
            mock_panel.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_storage_soe_success(self):
        """Test successful storage SOE retrieval."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.get_storage_soe_api_v1_storage_soe_get"
        ) as mock_storage:
            # Mock storage SOE response
            storage_response = MagicMock()
            storage_response.soe = 0.85
            storage_response.max_energy_kwh = 13.5
            mock_storage.asyncio = AsyncMock(return_value=storage_response)

            result = await client.get_storage_soe()

            assert result == storage_response
            mock_storage.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_circuit_priority_success(self):
        """Test successful circuit priority setting."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_set_circuit:
            # Mock circuit update response
            circuit_response = MagicMock()
            circuit_response.priority = "MUST_HAVE"
            mock_set_circuit.asyncio = AsyncMock(return_value=circuit_response)

            result = await client.set_circuit_priority("circuit-1", "MUST_HAVE")

            assert result == circuit_response
            mock_set_circuit.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_circuits_success(self):
        """Test successful circuits retrieval."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.get_circuits_api_v1_circuits_get"
        ) as mock_circuits:
            # Mock circuits response
            circuits_response = MagicMock()
            circuits_response.circuits = {
                "1": MagicMock(name="Main", instant_power_w=1500)
            }
            mock_circuits.asyncio = AsyncMock(return_value=circuits_response)

            result = await client.get_circuits()

            assert result == circuits_response
            mock_circuits.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_circuit_relay_success(self):
        """Test successful circuit relay setting."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_set_circuit:
            # Mock circuit update response
            circuit_response = MagicMock()
            circuit_response.relay_state = "OPEN"
            mock_set_circuit.asyncio = AsyncMock(return_value=circuit_response)

            result = await client.set_circuit_relay("circuit-1", "OPEN")

            assert result == circuit_response
            mock_set_circuit.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_panel_state_auth_error(self):
        """Test panel state retrieval with auth error."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Mock 401 error
            mock_panel.asyncio = AsyncMock(side_effect=Exception("401 Unauthorized"))

            with pytest.raises(SpanPanelAuthError):
                await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_get_storage_soe_connection_error(self):
        """Test storage SOE retrieval with connection error."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.get_storage_soe_api_v1_storage_soe_get"
        ) as mock_storage:
            import httpx

            # Mock connection error
            mock_storage.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Connection failed")
            )

            with pytest.raises(SpanPanelConnectionError):
                await client.get_storage_soe()

    @pytest.mark.asyncio
    async def test_set_circuit_priority_invalid_priority(self):
        """Test circuit priority setting with invalid priority."""
        client = SpanPanelClient("192.168.1.100")

        # Test invalid priority enum value - should be wrapped in SpanPanelAPIError
        with pytest.raises(SpanPanelAPIError, match="API error.*not a valid Priority"):
            await client.set_circuit_priority("circuit-1", "INVALID_PRIORITY")

    @pytest.mark.asyncio
    async def test_connection_error_handling(self):
        """Test connection error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            import httpx

            # Mock connection error
            mock_status.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Connection failed")
            )

            with pytest.raises(SpanPanelConnectionError):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_timeout_error_handling(self):
        """Test timeout error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            import httpx

            # Mock timeout error
            mock_status.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Request timed out")
            )

            with pytest.raises(SpanPanelTimeoutError):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """Test API error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            # Mock API error
            mock_status.asyncio = AsyncMock(side_effect=Exception("Server Error"))

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
