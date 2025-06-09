"""Tests for error handling, retries, timeouts and HTTP status codes.

This module tests exception handling, retry logic, timeout behavior,
and error scenarios for all API methods.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


class TestHTTPStatusErrorHandling:
    """Test HTTP status error handling and conversion to appropriate exceptions."""

    @pytest.mark.asyncio
    async def test_401_unauthorized_error(self):
        """Test 401 Unauthorized error handling."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Create a proper mock response with status_code
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.content = b'{"detail": "Unauthorized"}'
            mock_request = MagicMock()

            mock_panel.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "401 Unauthorized", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(SpanPanelAuthError, match="Authentication required"):
                await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_403_forbidden_error(self):
        """Test 403 Forbidden error handling."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Create a proper mock response with status_code
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_response.content = b'{"detail": "Forbidden"}'
            mock_request = MagicMock()

            mock_panel.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "403 Forbidden", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(SpanPanelAuthError, match="Authentication required"):
                await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_500_internal_server_error(self):
        """Test 500 Internal Server Error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            # Create a proper mock response with status_code
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.content = b'{"detail": "Internal Server Error"}'
            mock_request = MagicMock()

            mock_status.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "500 Internal Server Error",
                    request=mock_request,
                    response=mock_response,
                )
            )

            with pytest.raises(SpanPanelServerError, match="Server error 500"):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_502_bad_gateway_error(self):
        """Test 502 Bad Gateway error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            # Create a proper mock response with status_code
            mock_response = MagicMock()
            mock_response.status_code = 502
            mock_response.content = b'{"detail": "Bad Gateway"}'
            mock_request = MagicMock()

            mock_status.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "502 Bad Gateway", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(
                SpanPanelRetriableError, match="Retriable server error 502"
            ):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_503_service_unavailable_error(self):
        """Test 503 Service Unavailable error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            # Create a proper mock response with status_code
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_response.content = b'{"detail": "Service Unavailable"}'
            mock_request = MagicMock()

            mock_status.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "503 Service Unavailable",
                    request=mock_request,
                    response=mock_response,
                )
            )

            with pytest.raises(
                SpanPanelRetriableError, match="Retriable server error 503"
            ):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_504_gateway_timeout_error(self):
        """Test 504 Gateway Timeout error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            # Create a proper mock response with status_code
            mock_response = MagicMock()
            mock_response.status_code = 504
            mock_response.content = b'{"detail": "Gateway Timeout"}'
            mock_request = MagicMock()

            mock_status.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "504 Gateway Timeout", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(
                SpanPanelRetriableError, match="Retriable server error 504"
            ):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_other_http_error(self):
        """Test other HTTP error handling (e.g., 404)."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            # Create a proper mock response with status_code
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.content = b'{"detail": "Not Found"}'
            mock_request = MagicMock()

            mock_status.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "404 Not Found", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(SpanPanelAPIError, match="HTTP 404"):
                await client.get_status()


class TestUnexpectedStatusHandling:
    """Test _handle_unexpected_status edge cases."""

    def test_handle_unexpected_status_auth_error(self):
        """Test handling authentication errors."""
        from span_panel_api.generated_client.errors import UnexpectedStatus

        client = SpanPanelClient("192.168.1.100")

        mock_response = MagicMock()
        mock_response.content = b'{"error": "unauthorized"}'
        unexpected_status = UnexpectedStatus(401, mock_response)

        with pytest.raises(SpanPanelAuthError, match="Authentication required"):
            client._handle_unexpected_status(unexpected_status)

    def test_handle_unexpected_status_retriable_error(self):
        """Test handling retriable server errors."""
        from span_panel_api.generated_client.errors import UnexpectedStatus

        client = SpanPanelClient("192.168.1.100")

        mock_response = MagicMock()
        mock_response.content = b'{"error": "bad_gateway"}'
        unexpected_status = UnexpectedStatus(502, mock_response)

        with pytest.raises(SpanPanelRetriableError, match="Retriable server error 502"):
            client._handle_unexpected_status(unexpected_status)

    def test_handle_unexpected_status_server_error(self):
        """Test handling non-retriable server errors."""
        from span_panel_api.generated_client.errors import UnexpectedStatus

        client = SpanPanelClient("192.168.1.100")

        mock_response = MagicMock()
        mock_response.content = b'{"error": "internal_error"}'
        unexpected_status = UnexpectedStatus(500, mock_response)

        with pytest.raises(SpanPanelServerError, match="Server error 500"):
            client._handle_unexpected_status(unexpected_status)

    def test_handle_unexpected_status_other_error(self):
        """Test handling other HTTP errors."""
        from span_panel_api.generated_client.errors import UnexpectedStatus

        client = SpanPanelClient("192.168.1.100")

        mock_response = MagicMock()
        mock_response.content = b'{"error": "not_found"}'
        unexpected_status = UnexpectedStatus(404, mock_response)

        with pytest.raises(SpanPanelAPIError, match="HTTP 404"):
            client._handle_unexpected_status(unexpected_status)


class TestRetryLogic:
    """Test retry logic and exponential backoff."""

    @pytest.mark.asyncio
    async def test_no_retries_configuration(self):
        """Test that by default (retries=0), operations fail immediately without retries."""
        client = SpanPanelClient("192.168.1.100", retries=0, retry_timeout=0.001)

        call_count = 0

        async def mock_operation():
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("Connection failed")

        with pytest.raises(httpx.ConnectError):
            await client._retry_with_backoff(mock_operation)

        # Should only be called once (no retries)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_one_retry_configuration(self):
        """Test that retries=1 means 1 retry (2 total attempts)."""
        client = SpanPanelClient("192.168.1.100", retries=1, retry_timeout=0.001)

        call_count = 0

        async def mock_operation():
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("Request timeout")

        with pytest.raises(httpx.TimeoutException):
            await client._retry_with_backoff(mock_operation)

        # Should be called twice (1 retry = 2 total attempts)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_multiple_retries_with_eventual_success(self):
        """Test retries=3 with eventual success on the last attempt."""
        client = SpanPanelClient("192.168.1.100", retries=3, retry_timeout=0.001)

        call_count = 0

        async def mock_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 4:  # Fail 3 times, succeed on 4th
                raise httpx.ConnectError("Connection failed")
            return "success"

        result = await client._retry_with_backoff(mock_operation)

        # Should succeed on the 4th attempt
        assert result == "success"
        assert call_count == 4

    @pytest.mark.asyncio
    async def test_retry_with_exponential_backoff(self):
        """Test that exponential backoff delays are calculated correctly."""
        client = SpanPanelClient(
            "192.168.1.100", retries=2, retry_timeout=0.1, retry_backoff_multiplier=2.0
        )

        call_count = 0
        start_time = asyncio.get_event_loop().time()

        async def mock_operation():
            nonlocal call_count
            call_count += 1
            raise httpx.TimeoutException("Request timeout")

        with pytest.raises(httpx.TimeoutException):
            await client._retry_with_backoff(mock_operation)

        end_time = asyncio.get_event_loop().time()
        elapsed = end_time - start_time

        # Should have been called 3 times (retries=2 means 3 total attempts)
        assert call_count == 3

        # Should have taken at least the backoff delays: 0.1 + 0.2 = 0.3s
        # Using a lenient check since timing can be imprecise in tests
        assert elapsed >= 0.2  # Allow some slack for test timing

    @pytest.mark.asyncio
    async def test_retry_only_on_retriable_errors(self):
        """Test that retries only happen for retriable errors."""
        client = SpanPanelClient("192.168.1.100", retries=2, retry_timeout=0.001)

        # Test retriable errors (should retry)
        retriable_exceptions = [
            httpx.ConnectError("Connection failed"),
            httpx.TimeoutException("Request timeout"),
        ]

        for exception in retriable_exceptions:
            call_count = 0

            async def mock_operation():
                nonlocal call_count
                call_count += 1
                raise exception

            with pytest.raises(type(exception)):
                await client._retry_with_backoff(mock_operation)

            # Should retry (3 total attempts for retries=2)
            assert call_count == 3, f"Failed for {type(exception)}"

    @pytest.mark.asyncio
    async def test_retry_with_httpx_status_error_retriable(self):
        """Test retry logic with httpx.HTTPStatusError that's retriable."""
        client = SpanPanelClient("192.168.1.100", retries=1, retry_timeout=0.001)

        call_count = 0

        async def mock_operation():
            nonlocal call_count
            call_count += 1
            # Create mock response for 502 error
            mock_response = MagicMock()
            mock_response.status_code = 502
            mock_request = MagicMock()

            raise httpx.HTTPStatusError(
                "502 Bad Gateway", request=mock_request, response=mock_response
            )

        # The raw httpx exception should be re-raised after retries
        with pytest.raises(httpx.HTTPStatusError):
            await client._retry_with_backoff(mock_operation)

        # Should have retried
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_with_httpx_status_error_non_retriable(self):
        """Test retry logic with httpx.HTTPStatusError that's not retriable."""
        client = SpanPanelClient("192.168.1.100", retries=2, retry_timeout=0.001)

        call_count = 0

        async def mock_operation():
            nonlocal call_count
            call_count += 1
            # Create mock response for 400 error (not retriable)
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_request = MagicMock()

            raise httpx.HTTPStatusError(
                "400 Bad Request", request=mock_request, response=mock_response
            )

        # The raw httpx exception should be re-raised immediately (no retries)
        with pytest.raises(httpx.HTTPStatusError):
            await client._retry_with_backoff(mock_operation)

        # Should not have retried
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_retry_configurations(self):
        """Test different retry configurations work as expected."""
        test_configs = [
            {"retries": 0, "expected_attempts": 1},
            {"retries": 1, "expected_attempts": 2},
            {"retries": 3, "expected_attempts": 4},
            {"retries": 5, "expected_attempts": 6},
        ]

        for config in test_configs:
            client = SpanPanelClient(
                "192.168.1.100", retries=config["retries"], retry_timeout=0.001
            )

            call_count = 0

            async def mock_operation():
                nonlocal call_count
                call_count += 1
                raise httpx.ConnectError("Connection failed")

            with pytest.raises(httpx.ConnectError):
                await client._retry_with_backoff(mock_operation)

            assert (
                call_count == config["expected_attempts"]
            ), f"Failed for config {config}"


class TestTimeoutBehavior:
    """Test timeout behavior and timeout error handling."""

    @pytest.mark.asyncio
    async def test_mocked_timeout_exceptions(self):
        """Test that mocked timeout exceptions are properly converted."""
        client = SpanPanelClient("192.168.1.100", timeout=1.0)

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Request timeout")
            )

            with pytest.raises(
                SpanPanelTimeoutError, match="Request timed out after 1.0s"
            ):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_timeout_with_authentication(self):
        """Test timeout handling during authentication."""
        client = SpanPanelClient("192.168.1.100", timeout=2.0)

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            mock_auth.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Auth timeout")
            )

            with pytest.raises(
                SpanPanelTimeoutError, match="Request timed out after 2.0s"
            ):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_real_timeout_to_unreachable_host(self):
        """Test timeout behavior with mocked connection timeout."""
        client = SpanPanelClient("192.168.1.100", timeout=0.001)  # Very short timeout

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Connection timeout")
            )

            with pytest.raises(
                SpanPanelTimeoutError, match="Request timed out after 0.001s"
            ):
                await client.get_status()


class TestAPIMethodErrors:
    """Test error scenarios specific to API methods."""

    @pytest.mark.asyncio
    async def test_get_storage_soe_connection_error(self):
        """Test storage SOE connection error."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with patch(
            "span_panel_api.client.get_storage_soe_api_v1_storage_soe_get"
        ) as mock_storage:
            mock_storage.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Failed to connect")
            )

            with pytest.raises(SpanPanelConnectionError, match="Failed to connect"):
                await client.get_storage_soe()

    @pytest.mark.asyncio
    async def test_set_circuit_priority_invalid_priority(self):
        """Test setting circuit priority with invalid priority."""
        client = SpanPanelClient("192.168.1.100")
        client.set_access_token("test-token")

        with pytest.raises(SpanPanelAPIError, match="is not a valid Priority"):
            await client.set_circuit_priority("circuit-1", "INVALID_PRIORITY")

    @pytest.mark.asyncio
    async def test_connection_error_handling(self):
        """Test connection error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )

            with pytest.raises(
                SpanPanelConnectionError, match="Failed to connect to 192.168.1.100"
            ):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """Test general API error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio = AsyncMock(side_effect=ValueError("Invalid data"))

            with pytest.raises(SpanPanelAPIError, match="API error: Invalid data"):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_get_status_api_error(self):
        """Test get_status API error handling."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio = AsyncMock(side_effect=Exception("General error"))

            with pytest.raises(
                SpanPanelAPIError, match="Unexpected error: General error"
            ):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_get_status_value_error(self):
        """Test ValueError handling in get_status."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio.side_effect = ValueError("Pydantic validation failed")

            with pytest.raises(
                SpanPanelAPIError, match="API error: Pydantic validation failed"
            ):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_get_status_generic_exception(self):
        """Test generic Exception handling in get_status."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio.side_effect = RuntimeError("Unexpected error")

            with pytest.raises(
                SpanPanelAPIError, match="Unexpected error: Unexpected error"
            ):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_get_circuits_value_error(self):
        """Test ValueError handling in get_circuits."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.get_circuits_api_v1_circuits_get"
        ) as mock_circuits:
            mock_circuits.asyncio.side_effect = ValueError("Circuit validation failed")

            with pytest.raises(
                SpanPanelAPIError, match="API error: Circuit validation failed"
            ):
                await client.get_circuits()

    @pytest.mark.asyncio
    async def test_get_storage_soe_generic_exception(self):
        """Test generic Exception handling in get_storage_soe."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.get_storage_soe_api_v1_storage_soe_get"
        ) as mock_storage:
            mock_storage.asyncio.side_effect = KeyError("Missing storage key")

            with pytest.raises(
                SpanPanelAPIError, match="Unexpected error: 'Missing storage key'"
            ):
                await client.get_storage_soe()

    @pytest.mark.asyncio
    async def test_set_circuit_relay_value_error(self):
        """Test ValueError handling in set_circuit_relay."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_relay:
            mock_relay.asyncio.side_effect = ValueError("Invalid relay state")

            with pytest.raises(
                SpanPanelAPIError, match="API error: Invalid relay state"
            ):
                await client.set_circuit_relay("circuit-1", "OPEN")

    @pytest.mark.asyncio
    async def test_set_circuit_priority_generic_exception(self):
        """Test generic Exception handling in set_circuit_priority."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_priority:
            mock_priority.asyncio.side_effect = AttributeError("Missing attribute")

            with pytest.raises(
                SpanPanelAPIError, match="Unexpected error: Missing attribute"
            ):
                await client.set_circuit_priority("circuit-1", "MUST_HAVE")


class TestPropertyValidationEdgeCases:
    """Test edge cases in property validation."""

    def test_retries_negative_validation(self):
        """Test retries property validation with negative values."""
        client = SpanPanelClient("192.168.1.100")

        with pytest.raises(ValueError, match="retries must be non-negative"):
            client.retries = -1

    def test_retry_timeout_negative_validation(self):
        """Test retry_timeout property validation with negative values."""
        client = SpanPanelClient("192.168.1.100")

        with pytest.raises(ValueError, match="retry_timeout must be non-negative"):
            client.retry_timeout = -0.5

    def test_retry_backoff_multiplier_too_small_validation(self):
        """Test retry_backoff_multiplier property validation with value < 1."""
        client = SpanPanelClient("192.168.1.100")

        with pytest.raises(
            ValueError, match="retry_backoff_multiplier must be at least 1"
        ):
            client.retry_backoff_multiplier = 0.5


class TestIntegrationScenarios:
    """Test integration scenarios for production usage."""

    @pytest.mark.asyncio
    async def test_span_integration_default_config(self):
        """Test SPAN integration with default configuration (no retries)."""
        client = SpanPanelClient("192.168.1.100")  # Default: retries=0

        # Default config should fail fast without retries
        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )

            with pytest.raises(SpanPanelTimeoutError):
                await client.get_status()

            # Should only be called once (no retries)
            assert mock_status.asyncio.call_count == 1

    @pytest.mark.asyncio
    async def test_span_integration_runtime_configuration(self):
        """Test SPAN integration with runtime configuration changes."""
        client = SpanPanelClient("192.168.1.100")

        # Start with no retries
        assert client.retries == 0

        # Change to production config with retries
        client.retries = 2
        client.retry_timeout = 0.001  # Fast for testing

        call_count = 0

        async def mock_operation():
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("Connection failed")

        with pytest.raises(httpx.ConnectError):
            await client._retry_with_backoff(mock_operation)

        # Should retry based on new configuration
        assert call_count == 3  # retries=2 means 3 total attempts

    @pytest.mark.asyncio
    async def test_testing_configurations(self):
        """Test configurations suitable for testing (fast feedback)."""
        # Fast failure for tests
        client = SpanPanelClient("192.168.1.100", timeout=0.001, retries=0)

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            mock_status.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )

            with pytest.raises(SpanPanelTimeoutError):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_context_manager_with_retries(self):
        """Test context manager with retry configuration."""
        client = SpanPanelClient("192.168.1.100", retries=1, retry_timeout=0.001)

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            call_count = 0

            def side_effect(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 2:
                    raise httpx.ConnectError("Connection failed")
                return MagicMock(system=MagicMock(manufacturer="SPAN"))

            mock_status.asyncio = AsyncMock(side_effect=side_effect)

            async with client:
                # Should succeed on retry
                status = await client.get_status()
                assert status is not None

            # Should have been called twice (1 failure + 1 retry success)
            assert call_count == 2


def test_performance_summary():
    """Test to verify semantic clarity of retry configuration."""
    # Test that retry configuration is intuitive
    client = SpanPanelClient("192.168.1.100")

    # Default: no retries for fast feedback
    assert client.retries == 0

    # retries=1 means 1 retry (2 total attempts)
    client.retries = 1
    assert client.retries == 1

    # retries=3 means 3 retries (4 total attempts)
    client.retries = 3
    assert client.retries == 3


class TestHTTPStatusErrorInAPIMethods:
    """Test HTTPStatusError handling in individual API methods."""

    @pytest.mark.asyncio
    async def test_authenticate_httpx_status_error_auth_codes(self):
        """Test HTTPStatusError handling in authenticate for auth error codes."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            # Create HTTPStatusError for 401
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_request = MagicMock()

            mock_auth.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "401 Unauthorized", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(SpanPanelAuthError, match="Authentication failed"):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_authenticate_httpx_status_error_other_codes(self):
        """Test HTTPStatusError handling in authenticate for non-auth error codes."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            # Create HTTPStatusError for 404
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_request = MagicMock()

            mock_auth.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "404 Not Found", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(SpanPanelAPIError, match="HTTP 404"):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_get_status_httpx_status_error(self):
        """Test HTTPStatusError handling in get_status."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.system_status_api_v1_status_get"
        ) as mock_status:
            # Create HTTPStatusError
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.content = b"Internal Server Error"
            mock_request = MagicMock()

            mock_status.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "500 Internal Server Error",
                    request=mock_request,
                    response=mock_response,
                )
            )

            with pytest.raises(SpanPanelServerError, match="Server error 500"):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_get_panel_state_httpx_status_error(self):
        """Test HTTPStatusError handling in get_panel_state."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            # Create HTTPStatusError
            mock_response = MagicMock()
            mock_response.status_code = 502
            mock_response.content = b"Bad Gateway"
            mock_request = MagicMock()

            mock_panel.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "502 Bad Gateway", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(
                SpanPanelRetriableError, match="Retriable server error 502"
            ):
                await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_get_circuits_httpx_status_error(self):
        """Test HTTPStatusError handling in get_circuits."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.get_circuits_api_v1_circuits_get"
        ) as mock_circuits:
            # Create HTTPStatusError
            mock_response = MagicMock()
            mock_response.status_code = 503
            mock_response.content = b"Service Unavailable"
            mock_request = MagicMock()

            mock_circuits.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "503 Service Unavailable",
                    request=mock_request,
                    response=mock_response,
                )
            )

            with pytest.raises(
                SpanPanelRetriableError, match="Retriable server error 503"
            ):
                await client.get_circuits()

    @pytest.mark.asyncio
    async def test_get_storage_soe_httpx_status_error(self):
        """Test HTTPStatusError handling in get_storage_soe."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.get_storage_soe_api_v1_storage_soe_get"
        ) as mock_storage:
            # Create HTTPStatusError
            mock_response = MagicMock()
            mock_response.status_code = 504
            mock_response.content = b"Gateway Timeout"
            mock_request = MagicMock()

            mock_storage.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "504 Gateway Timeout", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(
                SpanPanelRetriableError, match="Retriable server error 504"
            ):
                await client.get_storage_soe()

    @pytest.mark.asyncio
    async def test_set_circuit_relay_httpx_status_error(self):
        """Test HTTPStatusError handling in set_circuit_relay."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_relay:
            # Create HTTPStatusError
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.content = b"Bad Request"
            mock_request = MagicMock()

            mock_relay.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "400 Bad Request", request=mock_request, response=mock_response
                )
            )

            with pytest.raises(SpanPanelAPIError, match="HTTP 400"):
                await client.set_circuit_relay("circuit-1", "OPEN")

    @pytest.mark.asyncio
    async def test_set_circuit_priority_httpx_status_error(self):
        """Test HTTPStatusError handling in set_circuit_priority."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post"
        ) as mock_priority:
            # Create HTTPStatusError
            mock_response = MagicMock()
            mock_response.status_code = 422
            mock_response.content = b"Unprocessable Entity"
            mock_request = MagicMock()

            mock_priority.asyncio = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "422 Unprocessable Entity",
                    request=mock_request,
                    response=mock_response,
                )
            )

            with pytest.raises(SpanPanelAPIError, match="HTTP 422"):
                await client.set_circuit_priority("circuit-1", "MUST_HAVE")

    @pytest.mark.asyncio
    async def test_authenticate_httpx_connection_error(self):
        """Test HTTPStatusError vs ConnectError handling in authenticate."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            # Test ConnectError
            mock_auth.asyncio = AsyncMock(
                side_effect=httpx.ConnectError("Connection failed")
            )

            with pytest.raises(
                SpanPanelConnectionError, match="Failed to connect to 192.168.1.100"
            ):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_authenticate_httpx_timeout_error(self):
        """Test TimeoutException handling in authenticate."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            # Test TimeoutException
            mock_auth.asyncio = AsyncMock(
                side_effect=httpx.TimeoutException("Request timed out")
            )

            with pytest.raises(
                SpanPanelTimeoutError, match="Request timed out after 30.0s"
            ):
                await client.authenticate("test-app", "Test Application")
