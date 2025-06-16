"""Tests for core SpanPanelClient functionality.

This module tests client initialization, properties, configuration,
and basic API method success cases.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import SpanPanelAPIError


class TestClientInitialization:
    """Test client initialization and configuration."""

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

    def test_client_with_custom_port(self):
        """Test client initialization with custom port."""
        client = SpanPanelClient("192.168.1.100", port=8080)
        assert client._port == 8080
        assert client._base_url == "http://192.168.1.100:8080"

    def test_client_with_ssl(self):
        """Test client with SSL enabled."""
        client = SpanPanelClient("192.168.1.100", use_ssl=True)
        assert client._use_ssl is True
        assert client._base_url == "https://192.168.1.100:80"

    def test_imports(self):
        """Test that necessary imports work."""
        from span_panel_api import SpanPanelClient
        from span_panel_api.exceptions import SpanPanelAuthError

        assert SpanPanelClient is not None
        assert SpanPanelAPIError is not None
        assert SpanPanelAuthError is not None

    def test_imports_work(self):
        """Test that all expected imports are available."""
        from span_panel_api.generated_client import AuthenticatedClient, Client
        from span_panel_api.generated_client.api.default import system_status_api_v1_status_get

        assert AuthenticatedClient is not None
        assert Client is not None
        assert system_status_api_v1_status_get is not None


class TestRetryConfiguration:
    """Test retry configuration and properties."""

    def test_default_configuration(self):
        """Test default retry configuration."""
        client = SpanPanelClient("192.168.1.100")
        assert client.retries == 0
        assert client.retry_timeout == 0.5
        assert client.retry_backoff_multiplier == 2.0

    def test_timeout_only_configuration(self):
        """Test configuration with only timeout."""
        client = SpanPanelClient("192.168.1.100", timeout=10.0)
        assert client._timeout == 10.0
        assert client.retries == 0

    def test_custom_constructor_configuration(self):
        """Test custom retry configuration via constructor."""
        client = SpanPanelClient("192.168.1.100", timeout=15.0, retries=3, retry_timeout=1.0, retry_backoff_multiplier=1.5)
        assert client._timeout == 15.0
        assert client.retries == 3
        assert client.retry_timeout == 1.0
        assert client.retry_backoff_multiplier == 1.5

    def test_runtime_configuration_changes(self):
        """Test changing retry configuration at runtime."""
        client = SpanPanelClient("192.168.1.100")

        # Change retries
        client.retries = 2
        assert client.retries == 2

        # Change retry timeout
        client.retry_timeout = 1.5
        assert client.retry_timeout == 1.5

        # Change backoff multiplier
        client.retry_backoff_multiplier = 3.0
        assert client.retry_backoff_multiplier == 3.0

    def test_validation_retries(self):
        """Test validation of retries parameter."""
        client = SpanPanelClient("192.168.1.100")
        with pytest.raises(ValueError, match="retries must be non-negative"):
            client.retries = -1

    def test_validation_retry_timeout(self):
        """Test validation of retry_timeout parameter."""
        client = SpanPanelClient("192.168.1.100")
        with pytest.raises(ValueError, match="retry_timeout must be non-negative"):
            client.retry_timeout = -0.5

    def test_validation_backoff_multiplier(self):
        """Test validation of retry_backoff_multiplier parameter."""
        client = SpanPanelClient("192.168.1.100")
        with pytest.raises(ValueError, match="retry_backoff_multiplier must be at least 1"):
            client.retry_backoff_multiplier = 0.5

    def test_constructor_validation(self):
        """Test validation during construction."""
        with pytest.raises(ValueError, match="retries must be non-negative"):
            SpanPanelClient("192.168.1.100", retries=-1)

        with pytest.raises(ValueError, match="retry_timeout must be non-negative"):
            SpanPanelClient("192.168.1.100", retry_timeout=-1.0)

        with pytest.raises(ValueError, match="retry_backoff_multiplier must be at least 1"):
            SpanPanelClient("192.168.1.100", retry_backoff_multiplier=0.5)


class TestClientInternals:
    """Test client internal methods and state management."""

    def test_set_access_token(self):
        """Test setting access token."""
        client = SpanPanelClient("192.168.1.100")
        assert client._access_token is None

        client.set_access_token("test-token")
        assert client._access_token == "test-token"

    def test_get_client_with_authenticated_client_path(self):
        """Test _get_client with authenticated client path."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        http_client = client._get_client()
        from span_panel_api.generated_client import AuthenticatedClient

        assert isinstance(http_client, AuthenticatedClient)

    def test_get_unauthenticated_client(self):
        """Test getting unauthenticated client."""
        client = SpanPanelClient("192.168.1.100")
        unauthenticated_client = client._get_unauthenticated_client()

        from span_panel_api.generated_client import Client

        assert isinstance(unauthenticated_client, Client)

    def test_get_unauthenticated_client_when_not_in_context_and_no_existing_client(self):
        """Test _get_unauthenticated_client when not in context and no existing client."""
        client = SpanPanelClient("192.168.1.100")

        # Ensure initial state
        client._in_context = False
        client._client = None
        client._httpx_client_owned = False

        # Get unauthenticated client
        unauthenticated_client = client._get_unauthenticated_client()

        # Should create and set the client
        assert client._client is unauthenticated_client
        assert client._httpx_client_owned is True

    def test_get_unauthenticated_client_when_in_context(self):
        """Test _get_unauthenticated_client when in context."""
        client = SpanPanelClient("192.168.1.100")
        client._in_context = True

        # Get unauthenticated client
        client._get_unauthenticated_client()

        # Should not set the client or ownership when in context
        assert client._client is None
        assert client._httpx_client_owned is False

    def test_get_unauthenticated_client_when_not_in_context_but_has_existing_client(self):
        """Test _get_unauthenticated_client when not in context but already has client."""
        client = SpanPanelClient("192.168.1.100")
        client._in_context = False
        client._client = MagicMock()  # Existing client

        # Get unauthenticated client
        unauthenticated_client = client._get_unauthenticated_client()

        # Should not change existing client or ownership
        assert client._client is not unauthenticated_client
        assert client._httpx_client_owned is False


class TestAPIMethodsSuccess:
    """Test successful API method calls."""

    @pytest.mark.asyncio
    async def test_get_status_success(self):
        """Test successful status retrieval."""
        client = SpanPanelClient("192.168.1.100")

        with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_status:
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

        with patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel:
            # Mock panel state response
            panel_response = MagicMock()
            panel_response.main_relay_state = "CLOSED"
            panel_response.instant_grid_power_w = 5000
            mock_panel.asyncio = AsyncMock(return_value=panel_response)

            result = await client.get_panel_state()

            assert result == panel_response
            mock_panel.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_circuits_success(self):
        """Test successful circuits retrieval."""
        client = SpanPanelClient("192.168.1.100", cache_window=0)
        client.set_access_token("test-token")

        with (
            patch("span_panel_api.client.get_circuits_api_v1_circuits_get") as mock_circuits,
            patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel_state,
        ):
            # Mock circuits response
            circuits_response = MagicMock()
            circuits_response.circuits = MagicMock()
            circuits_response.circuits.additional_properties = {"1": MagicMock(name="Main", instant_power_w=1500)}
            mock_circuits.asyncio = AsyncMock(return_value=circuits_response)

            # Mock panel state response (needed for enhanced circuits)
            panel_state_response = MagicMock()
            panel_state_response.branches = []  # No unmapped tabs
            mock_panel_state.asyncio = AsyncMock(return_value=panel_state_response)

            result = await client.get_circuits()

            assert result == circuits_response
            mock_circuits.asyncio.assert_called_once()
            mock_panel_state.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_storage_soe_success(self):
        """Test successful storage SOE retrieval."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch("span_panel_api.client.get_storage_soe_api_v1_storage_soe_get") as mock_storage:
            # Mock storage SOE response
            storage_response = MagicMock()
            storage_response.soe = 0.85
            storage_response.max_energy_kwh = 13.5
            mock_storage.asyncio = AsyncMock(return_value=storage_response)

            result = await client.get_storage_soe()

            assert result == storage_response
            mock_storage.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_circuit_relay_success(self):
        """Test successful circuit relay setting."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch("span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post") as mock_set_circuit:
            # Mock circuit update response
            circuit_response = MagicMock()
            circuit_response.relay_state = "OPEN"
            mock_set_circuit.asyncio = AsyncMock(return_value=circuit_response)

            result = await client.set_circuit_relay("circuit-1", "OPEN")

            assert result == circuit_response
            mock_set_circuit.asyncio.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_circuit_priority_success(self):
        """Test successful circuit priority setting."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch("span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post") as mock_set_circuit:
            # Mock circuit update response
            circuit_response = MagicMock()
            circuit_response.priority = "MUST_HAVE"
            mock_set_circuit.asyncio = AsyncMock(return_value=circuit_response)

            result = await client.set_circuit_priority("circuit-1", "MUST_HAVE")

            assert result == circuit_response
            mock_set_circuit.asyncio.assert_called_once()


class TestGeneratedClient:
    """Test the generated httpx-based client."""

    def test_client_import(self):
        """Test that the generated client can be imported."""
        from span_panel_api.generated_client import AuthenticatedClient, Client

        # This should not raise any import errors
        assert Client is not None
        assert AuthenticatedClient is not None

    def test_client_creation(self):
        """Test that clients can be created."""
        from span_panel_api.generated_client import AuthenticatedClient, Client

        client = Client(base_url="https://test.example.com")
        assert client is not None

        auth_client = AuthenticatedClient(base_url="https://test.example.com", token="test-token")
        assert auth_client is not None

    def test_api_functions_import(self):
        """Test that API functions can be imported."""
        from span_panel_api.generated_client.api.default import system_status_api_v1_status_get

        # This should not raise import errors
        assert system_status_api_v1_status_get is not None

    @pytest.mark.asyncio
    async def test_client_context_manager(self):
        """Test that the client works as an async context manager."""
        from span_panel_api.generated_client import Client

        async with Client(base_url="https://test.example.com") as client:
            # Just test that the context manager works
            assert client is not None


class TestExceptionInheritance:
    """Test exception type hierarchy and constants."""

    def test_exceptions_inheritance(self):
        """Test that exceptions inherit from the correct base classes."""
        from span_panel_api.exceptions import (
            SpanPanelAuthError,
            SpanPanelConnectionError,
            SpanPanelError,
            SpanPanelRetriableError,
            SpanPanelServerError,
            SpanPanelTimeoutError,
        )

        # All exceptions should inherit from SpanPanelError
        assert issubclass(SpanPanelAuthError, SpanPanelError)
        assert issubclass(SpanPanelConnectionError, SpanPanelError)
        assert issubclass(SpanPanelAPIError, SpanPanelError)
        assert issubclass(SpanPanelTimeoutError, SpanPanelError)

        # Server errors should inherit from SpanPanelAPIError
        assert issubclass(SpanPanelRetriableError, SpanPanelAPIError)
        assert issubclass(SpanPanelServerError, SpanPanelAPIError)

    def test_api_error_with_status_code(self):
        """Test SpanPanelAPIError with status code."""

        error = SpanPanelAPIError("Test error", 404)
        assert str(error) == "Test error"
        assert error.status_code == 404

    def test_exception_inheritance(self):
        """Test exception inheritance structure."""
        from span_panel_api.exceptions import (
            SpanPanelAuthError,
            SpanPanelConnectionError,
            SpanPanelError,
            SpanPanelRetriableError,
            SpanPanelServerError,
            SpanPanelTimeoutError,
        )

        # Test that all specific errors inherit from base SpanPanelError
        assert issubclass(SpanPanelAuthError, SpanPanelError)
        assert issubclass(SpanPanelConnectionError, SpanPanelError)
        assert issubclass(SpanPanelAPIError, SpanPanelError)
        assert issubclass(SpanPanelTimeoutError, SpanPanelError)

        # Test that server errors inherit from SpanPanelAPIError
        assert issubclass(SpanPanelRetriableError, SpanPanelAPIError)
        assert issubclass(SpanPanelServerError, SpanPanelAPIError)

        # Test that all inherit from standard Exception
        assert issubclass(SpanPanelError, Exception)

    def test_status_code_constants(self):
        """Test that status code constants are defined correctly."""
        from span_panel_api.const import AUTH_ERROR_CODES, RETRIABLE_ERROR_CODES, SERVER_ERROR_CODES

        # Auth errors: 401, 403
        assert 401 in AUTH_ERROR_CODES
        assert 403 in AUTH_ERROR_CODES

        # Retriable errors: 502, 503, 504
        assert 502 in RETRIABLE_ERROR_CODES
        assert 503 in RETRIABLE_ERROR_CODES
        assert 504 in RETRIABLE_ERROR_CODES

        # Server errors: 500
        assert 500 in SERVER_ERROR_CODES
