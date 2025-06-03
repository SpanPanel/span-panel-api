"""Tests for authentication and token management.

This module tests authentication flows, token management,
and authentication-related error scenarios.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelAuthError


class TestAuthentication:
    """Test authentication functionality."""

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
        """Test authentication with validation error response."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            from span_panel_api.generated_client.models.http_validation_error import (
                HTTPValidationError,
            )

            validation_error = HTTPValidationError()
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
            # Return something that's not AuthOut, HTTPValidationError, or None
            mock_auth.asyncio = AsyncMock(return_value="unexpected")

            with pytest.raises(SpanPanelAPIError, match="Unexpected response type"):
                await client.authenticate("test-app", "Test Application")

    @pytest.mark.asyncio
    async def test_authenticate_value_error(self):
        """Test ValueError handling in authenticate."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            mock_auth.asyncio.side_effect = ValueError("Validation failed")

            with pytest.raises(SpanPanelAPIError, match="API error: Validation failed"):
                await client.authenticate("test", "Test Client")

    @pytest.mark.asyncio
    async def test_authenticate_generic_exception(self):
        """Test generic Exception handling in authenticate."""
        client = SpanPanelClient("192.168.1.100")

        with patch(
            "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
        ) as mock_auth:
            mock_auth.asyncio.side_effect = RuntimeError("Unexpected error")

            with pytest.raises(SpanPanelAPIError, match="API error: Unexpected error"):
                await client.authenticate("test", "Test Client")

    @pytest.mark.asyncio
    async def test_authenticate_http_errors(self):
        """Test various HTTP errors during authentication."""
        client = SpanPanelClient("192.168.1.100")

        # Test cases for different HTTP status codes
        test_cases = [
            (401, SpanPanelAuthError, "Authentication failed"),
            (403, SpanPanelAuthError, "Authentication failed"),
            (
                500,
                SpanPanelAPIError,
                "Server error 500",
            ),  # Changed from SpanPanelServerError
            (
                502,
                SpanPanelAPIError,
                "Retriable server error 502",
            ),  # Changed from SpanPanelRetriableError
            (
                503,
                SpanPanelAPIError,
                "Retriable server error 503",
            ),  # Changed from SpanPanelRetriableError
            (
                504,
                SpanPanelAPIError,
                "Retriable server error 504",
            ),  # Changed from SpanPanelRetriableError
            (404, SpanPanelAPIError, "HTTP 404"),
            (400, SpanPanelAPIError, "HTTP 400"),
        ]

        for status_code, expected_exception, error_msg_pattern in test_cases:
            with patch(
                "span_panel_api.client.generate_jwt_api_v1_auth_register_post"
            ) as mock_auth:
                # Create a proper mock response
                mock_response = MagicMock()
                mock_response.status_code = status_code
                mock_request = MagicMock()

                mock_auth.asyncio = AsyncMock(
                    side_effect=httpx.HTTPStatusError(
                        f"{status_code} Error",
                        request=mock_request,
                        response=mock_response,
                    )
                )

                with pytest.raises(expected_exception, match=error_msg_pattern):
                    await client.authenticate("test-app", "Test Application")


class TestTokenManagement:
    """Test token management and access token handling."""

    def test_set_access_token_same_token(self):
        """Test setting the same token twice does nothing."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "existing-token"

        # Should return early without changes
        client.set_access_token("existing-token")
        assert client._access_token == "existing-token"

    @pytest.mark.asyncio
    async def test_set_access_token_outside_context_with_existing_client(self):
        """Test setting token outside context when client exists."""
        client = SpanPanelClient("192.168.1.100")

        # Simulate existing client
        mock_client = MagicMock()
        client._client = mock_client
        client._httpx_client_owned = True

        # Set new token
        client.set_access_token("new-token")

        # Should reset client
        assert client._client is None
        assert not client._httpx_client_owned
        assert client._access_token == "new-token"

    @pytest.mark.asyncio
    async def test_set_access_token_in_context_with_client_upgrade(self):
        """Test upgrading from Client to AuthenticatedClient in context."""
        from span_panel_api.generated_client import Client

        client = SpanPanelClient("192.168.1.100")
        client._in_context = True

        # Create a regular Client
        unauthenticated_client = Client(
            base_url="http://test",
            timeout=httpx.Timeout(30.0),
            verify_ssl=False,
            raise_on_unexpected_status=True,
        )

        # Mock the async client
        mock_async_client = MagicMock()
        mock_async_client.headers = {}
        unauthenticated_client._async_client = mock_async_client
        client._client = unauthenticated_client

        # Set token - should upgrade client
        client.set_access_token("test-token")

        # Should be upgraded to AuthenticatedClient
        from span_panel_api.generated_client import AuthenticatedClient

        assert isinstance(client._client, AuthenticatedClient)
        assert client._access_token == "test-token"

    @pytest.mark.asyncio
    async def test_set_access_token_update_existing_authenticated_client(self):
        """Test updating token on existing AuthenticatedClient."""
        from span_panel_api.generated_client import AuthenticatedClient

        client = SpanPanelClient("192.168.1.100")
        client._in_context = True

        # Create authenticated client
        auth_client = AuthenticatedClient(
            base_url="http://test",
            token="old-token",
            timeout=httpx.Timeout(30.0),
            verify_ssl=False,
            raise_on_unexpected_status=True,
        )

        # Mock the async and sync clients
        mock_async_client = MagicMock()
        mock_async_client.headers = {}
        mock_sync_client = MagicMock()
        mock_sync_client.headers = {}

        auth_client._async_client = mock_async_client
        auth_client._client = mock_sync_client
        client._client = auth_client

        # Update token
        client.set_access_token("new-token")

        # Should update existing client
        assert client._client.token == "new-token"
        assert client._access_token == "new-token"


class TestAuthenticationRequirements:
    """Test authentication requirements for different endpoints."""

    @pytest.mark.asyncio
    async def test_get_client_for_endpoint_requires_auth_no_token(self):
        """Test that endpoints requiring auth fail without token."""
        client = SpanPanelClient("192.168.1.100")

        with pytest.raises(
            SpanPanelAuthError,
            match="This endpoint requires authentication. Call authenticate\\(\\) first.",
        ):
            client._get_client_for_endpoint(requires_auth=True)

    @pytest.mark.asyncio
    async def test_get_client_for_endpoint_no_auth_required(self):
        """Test that endpoints not requiring auth work without token."""
        client = SpanPanelClient("192.168.1.100")

        # Should not raise an exception
        result_client = client._get_client_for_endpoint(requires_auth=False)
        from span_panel_api.generated_client import Client

        assert isinstance(result_client, Client)

    @pytest.mark.asyncio
    async def test_get_panel_state_auth_error(self):
        """Test panel state requires authentication."""
        client = SpanPanelClient("192.168.1.100")

        with pytest.raises(
            SpanPanelAuthError,
            match="This endpoint requires authentication. Call authenticate\\(\\) first.",
        ):
            await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_get_panel_state_401_exception_string(self):
        """Test exception with '401 Unauthorized' in string."""
        client = SpanPanelClient("192.168.1.100")
        client._access_token = "test-token"

        with patch(
            "span_panel_api.client.get_panel_state_api_v1_panel_get"
        ) as mock_panel:
            mock_panel.asyncio.side_effect = Exception("401 Unauthorized access")

            with pytest.raises(SpanPanelAuthError, match="Authentication required"):
                await client.get_panel_state()
