"""Tests for the PySpan SPAN Panel API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import (
    SpanPanelAPIError,
    SpanPanelAuthError,
    SpanPanelConnectionError,
    SpanPanelRetriableError,
    SpanPanelServerError,
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

            # Create a proper mock response with status_code
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_request = MagicMock()

            mock_auth.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "401 Unauthorized", request=mock_request, response=mock_response
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
        client.set_access_token("test-token")

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
        client.set_access_token("test-token")

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
        client.set_access_token("test-token")

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
        client.set_access_token("test-token")

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
        client.set_access_token("test-token")

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
        client.set_access_token("test-token")

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
        client.set_access_token("test-token")

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
    """Test that import works correctly."""
    # Test that we can import everything
    from span_panel_api.client import SpanPanelClient

    assert SpanPanelClient is not None


# Additional coverage tests for missing lines
class TestSpanPanelClientAdditionalCoverage:
    """Additional tests to improve coverage."""

    def test_import_error_handling(self):
        """Test import error handling in client module."""
        # We can't easily test import failures of the generated client
        # since it's a module-level import, but we can test that
        # the import paths are correct
        from span_panel_api.client import (
            AuthIn,
            AuthOut,
            BatteryStorage,
            CircuitsOut,
            PanelState,
            RelayStateIn,
            StatusOut,
        )

        # Verify all imports work
        assert AuthIn is not None
        assert AuthOut is not None
        assert BatteryStorage is not None
        assert CircuitsOut is not None
        assert PanelState is not None
        assert RelayStateIn is not None
        assert StatusOut is not None

    def test_set_access_token(self):
        """Test set_access_token method."""
        client = SpanPanelClient("192.168.1.100")

        # Initially no token
        assert client._access_token is None
        assert client._client is None

        # Set token
        client.set_access_token("test-token")
        assert client._access_token == "test-token"
        assert client._client is None  # Should reset client

    def test_get_client_with_authenticated_client_path(self):
        """Test _get_client with authenticated client creation."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        # First call should create authenticated client
        http_client = client._get_client()
        assert http_client is not None

        # Second call should reuse the same client
        http_client2 = client._get_client()
        assert http_client2 is http_client

    def test_get_unauthenticated_client(self):
        """Test _get_unauthenticated_client method."""
        client = SpanPanelClient("192.168.1.100")

        unauthenticated_client = client._get_unauthenticated_client()
        assert unauthenticated_client is not None

    @pytest.mark.asyncio
    async def test_context_manager_cleanup_with_client(self):
        """Test context manager cleanup when client exists."""
        client = SpanPanelClient("192.168.1.100")

        # Set up a mock client
        mock_client = AsyncMock()
        mock_client.__aexit__ = AsyncMock()
        client._client = mock_client

        # Test cleanup
        await client.close()
        mock_client.__aexit__.assert_called_once_with(None, None, None)
        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager_cleanup_with_exception(self):
        """Test context manager cleanup when client.__aexit__ raises an exception."""
        client = SpanPanelClient("192.168.1.100")

        # Set up a mock client that raises an exception during cleanup
        mock_client = AsyncMock()
        mock_client.__aexit__ = AsyncMock(side_effect=Exception("Cleanup failed"))
        client._client = mock_client

        # Test cleanup - should not raise exception
        await client.close()
        mock_client.__aexit__.assert_called_once_with(None, None, None)
        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager_entry_exit(self):
        """Test async context manager entry and exit."""
        async with SpanPanelClient("192.168.1.100") as client:
            assert client is not None
        # Exit should have been called without issues

    @pytest.mark.asyncio
    async def test_authenticate_none_response(self):
        """Test authentication with None response."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            mock_auth.asyncio = AsyncMock(return_value=None)

            with pytest.raises(
                SpanPanelAPIError, match="Authentication failed - no response"
            ):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_authenticate_validation_error_response(self):
        """Test authentication with HTTPValidationError response."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            from span_panel_api.client import HTTPValidationError

            validation_error = HTTPValidationError(detail=[])
            mock_auth.asyncio = AsyncMock(return_value=validation_error)

            with pytest.raises(
                SpanPanelAPIError, match="Validation error during authentication"
            ):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_authenticate_unexpected_response_type(self):
        """Test authentication with unexpected response type."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            # Return an unexpected type (string instead of AuthOut)
            mock_auth.asyncio = AsyncMock(return_value="unexpected")

            with pytest.raises(SpanPanelAPIError, match="Unexpected response type"):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_get_client_for_endpoint_requires_auth_no_token(self):
        """Test _get_client_for_endpoint when auth is required but no token."""
        client = SpanPanelClient("192.168.1.100")

        with pytest.raises(
            SpanPanelAuthError, match="This endpoint requires authentication"
        ):
            client._get_client_for_endpoint(requires_auth=True)

    @pytest.mark.asyncio
    async def test_get_client_for_endpoint_no_auth_required(self):
        """Test _get_client_for_endpoint when no auth is required."""
        client = SpanPanelClient("192.168.1.100")

        # Should work without token when auth not required
        http_client = client._get_client_for_endpoint(requires_auth=False)
        assert http_client is not None

    @pytest.mark.asyncio
    async def test_get_status_api_error(self):
        """Test get_status with API error."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio = AsyncMock(side_effect=Exception("API error"))

            with pytest.raises(SpanPanelAPIError, match="API error"):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_get_panel_state_api_error(self):
        """Test get_panel_state with API error."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            mock_panel.asyncio = AsyncMock(side_effect=Exception("API error"))

            with pytest.raises(SpanPanelAPIError, match="API error"):
                await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_get_circuits_api_error(self):
        """Test get_circuits with API error."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_circuits_api_v1_circuits_get"
        ) as mock_circuits:
            mock_circuits.asyncio = AsyncMock(side_effect=Exception("API error"))

            with pytest.raises(SpanPanelAPIError, match="API error"):
                await client.get_circuits()

    @pytest.mark.asyncio
    async def test_get_storage_soe_api_error(self):
        """Test get_storage_soe with API error."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_storage_soe_api_v1_storage_soe_get"
        ) as mock_storage:
            mock_storage.asyncio = AsyncMock(side_effect=Exception("API error"))

            with pytest.raises(SpanPanelAPIError, match="API error"):
                await client.get_storage_soe()

    @pytest.mark.asyncio
    async def test_set_circuit_relay_api_error(self):
        """Test set_circuit_relay with API error."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_set_circuit:
            mock_set_circuit.asyncio = AsyncMock(side_effect=Exception("API error"))

            with pytest.raises(SpanPanelAPIError, match="API error"):
                await client.set_circuit_relay("circuit-1", "OPEN")

    @pytest.mark.asyncio
    async def test_set_circuit_priority_api_error(self):
        """Test set_circuit_priority with API error."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_set_circuit:
            mock_set_circuit.asyncio = AsyncMock(side_effect=Exception("API error"))

            with pytest.raises(SpanPanelAPIError, match="API error"):
                await client.set_circuit_priority("circuit-1", "MUST_HAVE")

    # Note: We use mocking for timeout/connection error testing because:
    # 1. httpx handles timeouts internally at the transport level
    # 2. Mocking httpx.TimeoutException tests our error handling logic
    # 3. Real timeout tests would be slow and unreliable
    # 4. Our goal is to test error handling, not httpx's timeout implementation

    @pytest.mark.asyncio
    async def test_comprehensive_timeout_coverage(self):
        """Test timeout error handling for all API methods."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        # Test get_panel_state timeout
        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            mock_panel.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )
            with pytest.raises(SpanPanelTimeoutError):
                await client.get_panel_state()

        # Test get_circuits timeout
        with patch(
            "span_panel_api.client.get_circuits_api_v1_circuits_get"
        ) as mock_circuits:
            mock_circuits.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )
            with pytest.raises(SpanPanelTimeoutError):
                await client.get_circuits()

        # Test set_circuit_relay timeout
        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_set:
            mock_set.asyncio = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            with pytest.raises(SpanPanelTimeoutError):
                await client.set_circuit_relay("circuit-1", "OPEN")

        # Test set_circuit_priority timeout
        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_set:
            mock_set.asyncio = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            with pytest.raises(SpanPanelTimeoutError):
                await client.set_circuit_priority("circuit-1", "MUST_HAVE")

    @pytest.mark.asyncio
    async def test_comprehensive_connection_coverage(self):
        """Test connection error handling for all API methods."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        # Test get_panel_state connection error
        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            mock_panel.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Connection failed")
            )
            with pytest.raises(SpanPanelConnectionError):
                await client.get_panel_state()

        # Test get_circuits connection error
        with patch(
            "span_panel_api.client.get_circuits_api_v1_circuits_get"
        ) as mock_circuits:
            mock_circuits.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Connection failed")
            )
            with pytest.raises(SpanPanelConnectionError):
                await client.get_circuits()

        # Test set_circuit_relay connection error
        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_set:
            mock_set.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Connection failed")
            )
            with pytest.raises(SpanPanelConnectionError):
                await client.set_circuit_relay("circuit-1", "OPEN")

        # Test set_circuit_priority connection error
        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_set:
            mock_set.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Connection failed")
            )
            with pytest.raises(SpanPanelConnectionError):
                await client.set_circuit_priority("circuit-1", "MUST_HAVE")


class TestHTTPStatusErrorHandling:
    """Test HTTP status error handling and categorization."""

    @pytest.mark.asyncio
    async def test_401_unauthorized_error(self):
        """Test 401 Unauthorized error handling."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        # Set a token so we get past the auth check
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Create a mock response with 401 status
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "401 Unauthorized", request=mock_request, response=mock_response
            )
            mock_panel.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelAuthError, match="Authentication required"):
                await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_403_forbidden_error(self):
        """Test 403 Forbidden error handling."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        # Set a token so we get past the auth check
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Create a mock response with 403 status
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "403 Forbidden", request=mock_request, response=mock_response
            )
            mock_panel.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelAuthError, match="Authentication required"):
                await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_500_internal_server_error(self):
        """Test 500 Internal Server Error handling (non-retriable)."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        # Set a token so we get past the auth check
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_api:
            # Create a mock response with 500 status
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=mock_request,
                response=mock_response,
            )
            mock_api.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelServerError) as exc_info:
                await client.set_circuit_priority("circuit-1", "MUST_HAVE")

            # Verify the error contains the status code and is marked as non-retriable
            assert exc_info.value.status_code == 500
            assert "Server error 500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_502_bad_gateway_error(self):
        """Test 502 Bad Gateway error handling (retriable)."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        # Set a token so we get past the auth check
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Create a mock response with 502 status
            mock_response = MagicMock()
            mock_response.status_code = 502
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "502 Bad Gateway", request=mock_request, response=mock_response
            )
            mock_panel.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelRetriableError) as exc_info:
                await client.get_panel_state()

            # Verify the error contains the status code and is marked as retriable
            assert exc_info.value.status_code == 502
            assert "Retriable server error 502" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_503_service_unavailable_error(self):
        """Test 503 Service Unavailable error handling (retriable)."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        # Set a token so we get past the auth check
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_circuits_api_v1_circuits_get"
        ) as mock_circuits:
            # Create a mock response with 503 status
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "503 Service Unavailable", request=mock_request, response=mock_response
            )
            mock_circuits.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelRetriableError) as exc_info:
                await client.get_circuits()

            # Verify the error contains the status code and is marked as retriable
            assert exc_info.value.status_code == 503
            assert "Retriable server error 503" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_504_gateway_timeout_error(self):
        """Test 504 Gateway Timeout error handling (retriable)."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        # Set a token so we get past the auth check
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_storage_soe_api_v1_storage_soe_get"
        ) as mock_storage:
            # Create a mock response with 504 status
            mock_response = MagicMock()
            mock_response.status_code = 504
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "504 Gateway Timeout", request=mock_request, response=mock_response
            )
            mock_storage.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelRetriableError) as exc_info:
                await client.get_storage_soe()

            # Verify the error contains the status code and is marked as retriable
            assert exc_info.value.status_code == 504
            assert "Retriable server error 504" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_other_http_error(self):
        """Test other HTTP error handling (like 404, 400, etc.)."""
        import httpx

        client = SpanPanelClient("192.168.1.100")
        # Set a token so we get past the auth check
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Create a mock response with 404 status
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "404 Not Found", request=mock_request, response=mock_response
            )
            mock_panel.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelAPIError) as exc_info:
                await client.get_panel_state()

            # Verify the error contains the status code
            assert exc_info.value.status_code == 404
            assert "HTTP 404" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_authenticate_http_errors(self):
        """Test HTTP error handling in authenticate method."""
        import httpx

        client = SpanPanelClient("192.168.1.100")

        # Test 401 during authentication
        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "401 Unauthorized", request=mock_request, response=mock_response
            )
            mock_auth.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelAuthError, match="Authentication failed"):
                await client.authenticate("test-app", "Test Application")

        # Test 500 during authentication
        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=mock_request,
                response=mock_response,
            )
            mock_auth.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelServerError) as exc_info:
                await client.authenticate("test-app", "Test Application")

            assert exc_info.value.status_code == 500

        # Test 502 during authentication (retriable)
        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            mock_response = MagicMock()
            mock_response.status_code = 502
            mock_request = MagicMock()

            http_error = httpx.HTTPStatusError(
                "502 Bad Gateway", request=mock_request, response=mock_response
            )
            mock_auth.asyncio = AsyncMock(side_effect=http_error)

            with pytest.raises(SpanPanelRetriableError) as exc_info:
                await client.authenticate("test-app", "Test Application")

            assert exc_info.value.status_code == 502


class TestErrorCategorization:
    """Test that errors are properly categorized for retry logic."""

    def test_exception_inheritance(self):
        """Test that new exception types inherit properly."""
        from span_panel_api.exceptions import (
            SpanPanelAPIError,
            SpanPanelRetriableError,
            SpanPanelServerError,
        )

        # Test inheritance chain
        assert issubclass(SpanPanelRetriableError, SpanPanelAPIError)
        assert issubclass(SpanPanelServerError, SpanPanelAPIError)

        # Test that they can be instantiated with status codes
        retriable_error = SpanPanelRetriableError("Test retriable error", 502)
        assert retriable_error.status_code == 502

        server_error = SpanPanelServerError("Test server error", 500)
        assert server_error.status_code == 500

    def test_status_code_constants(self):
        """Test that status code constants are properly defined."""
        from span_panel_api.const import (
            AUTH_ERROR_CODES,
            HTTP_BAD_GATEWAY,
            HTTP_FORBIDDEN,
            HTTP_GATEWAY_TIMEOUT,
            HTTP_INTERNAL_SERVER_ERROR,
            HTTP_SERVICE_UNAVAILABLE,
            HTTP_UNAUTHORIZED,
            RETRIABLE_ERROR_CODES,
            SERVER_ERROR_CODES,
        )

        # Test individual constants
        assert HTTP_UNAUTHORIZED == 401
        assert HTTP_FORBIDDEN == 403
        assert HTTP_INTERNAL_SERVER_ERROR == 500
        assert HTTP_BAD_GATEWAY == 502
        assert HTTP_SERVICE_UNAVAILABLE == 503
        assert HTTP_GATEWAY_TIMEOUT == 504

        # Test categorized constants
        assert AUTH_ERROR_CODES == (401, 403)
        assert RETRIABLE_ERROR_CODES == (502, 503, 504)
        assert SERVER_ERROR_CODES == (500,)

        # Test no overlap between categories
        assert not set(AUTH_ERROR_CODES) & set(RETRIABLE_ERROR_CODES)
        assert not set(AUTH_ERROR_CODES) & set(SERVER_ERROR_CODES)
        assert not set(RETRIABLE_ERROR_CODES) & set(SERVER_ERROR_CODES)
