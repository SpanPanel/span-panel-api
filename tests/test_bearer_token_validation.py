"""Test Bearer token authentication and validation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from span_panel_api import SpanPanelClient
from span_panel_api.exceptions import SpanPanelAuthError, SpanPanelAPIError
from span_panel_api.generated_client.errors import UnexpectedStatus


class TestBearerTokenValidation:
    """Test Bearer token authentication and error handling."""

    @pytest.mark.asyncio
    async def test_bearer_token_header_format(self):
        """Test that access token is properly formatted as Bearer token in headers."""
        async with SpanPanelClient(host="test-bearer") as client:
            # Set an access token
            client.set_access_token("test-jwt-token-12345")

            # Get the authenticated client
            auth_client = client._get_client_for_endpoint(requires_auth=True)

            # Verify the Authorization header is set correctly
            httpx_client = auth_client.get_async_httpx_client()
            auth_header = httpx_client.headers.get("Authorization")

            assert auth_header == "Bearer test-jwt-token-12345"
            assert auth_client.prefix == "Bearer"
            assert auth_client.token == "test-jwt-token-12345"
            assert auth_client.auth_header_name == "Authorization"

    @pytest.mark.asyncio
    async def test_401_unauthorized_with_bearer_token(self):
        """Test that 401 errors with Bearer token are properly converted to SpanPanelAuthError."""
        async with SpanPanelClient(host="test-401") as client:
            client.set_access_token("invalid-jwt-token")

            # Mock the API call to return 401
            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.content = b'{"detail": "Invalid authentication credentials"}'
            mock_response.text = "Unauthorized"

            with patch("span_panel_api.generated_client.api.default.get_panel_state_api_v1_panel_get.asyncio") as mock_panel:
                mock_panel.side_effect = httpx.HTTPStatusError(
                    "401 Unauthorized", request=mock_request, response=mock_response
                )

                with pytest.raises(SpanPanelAuthError) as exc_info:
                    await client.get_panel_state()

                assert "Authentication failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_403_forbidden_with_bearer_token(self):
        """Test that 403 errors with Bearer token are properly converted to SpanPanelAuthError."""
        async with SpanPanelClient(host="test-403") as client:
            client.set_access_token("valid-but-insufficient-token")

            # Mock the API call to return 403
            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 403
            mock_response.content = b'{"detail": "Insufficient permissions"}'
            mock_response.text = "Forbidden"

            with patch("span_panel_api.generated_client.api.default.get_panel_state_api_v1_panel_get.asyncio") as mock_panel:
                mock_panel.side_effect = httpx.HTTPStatusError("403 Forbidden", request=mock_request, response=mock_response)

                with pytest.raises(SpanPanelAuthError) as exc_info:
                    await client.get_panel_state()

                assert "Authentication failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_unexpected_status_401_handling(self):
        """Test that UnexpectedStatus with 401 is properly handled."""
        async with SpanPanelClient(host="test-unexpected-401") as client:
            client.set_access_token("expired-token")

            with patch("span_panel_api.generated_client.api.default.get_panel_state_api_v1_panel_get.asyncio") as mock_panel:
                mock_response_content = b'{"error": "Token expired"}'
                mock_panel.side_effect = UnexpectedStatus(401, mock_response_content)

                with pytest.raises(SpanPanelAuthError) as exc_info:
                    await client.get_panel_state()

                assert "Authentication failed" in str(exc_info.value)
                assert "Status 401" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_token_required_endpoint_raises_auth_error(self):
        """Test that endpoints requiring auth raise SpanPanelAuthError when no token is set."""
        async with SpanPanelClient(host="test-no-token") as client:
            # Don't set any access token

            # The error gets wrapped, so we need to check the inner error or the message
            with pytest.raises((SpanPanelAuthError, SpanPanelAPIError)) as exc_info:
                await client.get_circuits()

            # Check that the error message indicates authentication is required
            assert "authentication" in str(exc_info.value).lower() or "authenticate" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_token_updates_authorization_header(self):
        """Test that updating the token properly updates the Authorization header."""
        async with SpanPanelClient(host="test-token-update") as client:
            # Set initial token
            client.set_access_token("token-v1")
            auth_client = client._get_client_for_endpoint(requires_auth=True)
            httpx_client = auth_client.get_async_httpx_client()

            assert httpx_client.headers["Authorization"] == "Bearer token-v1"

            # Update token
            client.set_access_token("token-v2")

            # Header should be updated
            assert httpx_client.headers["Authorization"] == "Bearer token-v2"
            assert auth_client.token == "token-v2"

    @pytest.mark.asyncio
    async def test_bearer_token_authentication_flow(self):
        """Test complete Bearer token authentication flow."""
        async with SpanPanelClient(host="test-auth-flow") as client:
            # Initially no token
            assert client._access_token is None

            # Mock successful authentication
            with patch(
                "span_panel_api.generated_client.api.default.generate_jwt_api_v1_auth_register_post.asyncio"
            ) as mock_auth:
                mock_response = MagicMock()
                mock_response.access_token = "jwt-token-from-auth"
                mock_response.token_type = "Bearer"
                mock_response.iat_ms = 1234567890000
                mock_auth.return_value = mock_response

                auth_result = await client.authenticate("test-client", "Test integration")

                # Verify token is set
                assert client._access_token == "jwt-token-from-auth"
                assert auth_result.access_token == "jwt-token-from-auth"
                assert auth_result.token_type == "Bearer"

                # Verify client can make authenticated requests
                auth_client = client._get_client_for_endpoint(requires_auth=True)
                httpx_client = auth_client.get_async_httpx_client()
                assert httpx_client.headers["Authorization"] == "Bearer jwt-token-from-auth"

    @pytest.mark.asyncio
    async def test_generic_exception_handler_401_detection(self):
        """Test that generic exception handler detects 401 in exception strings."""
        async with SpanPanelClient(host="test-generic-401") as client:
            client.set_access_token("some-token")

            with patch("span_panel_api.generated_client.api.default.get_panel_state_api_v1_panel_get.asyncio") as mock_panel:
                # Simulate a generic exception that contains "401 Unauthorized"
                mock_panel.side_effect = RuntimeError("401 Unauthorized access denied")

                with pytest.raises(SpanPanelAuthError) as exc_info:
                    await client.get_panel_state()

                assert "Authentication failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_client_type_validation_with_auth(self):
        """Test that client type is properly validated when authentication is required."""
        client = SpanPanelClient(host="test-client-type")

        # Start context
        await client.__aenter__()

        try:
            # Set token after context is started
            client.set_access_token("test-token")

            # Should be able to get authenticated client
            auth_client = client._get_client_for_endpoint(requires_auth=True)
            assert auth_client is not None

            # Should have the right type
            from span_panel_api.generated_client.client import AuthenticatedClient

            assert isinstance(auth_client, AuthenticatedClient)

        finally:
            await client.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_auth_error_propagation_chain(self):
        """Test that authentication errors properly propagate through the exception chain."""
        async with SpanPanelClient(host="test-error-chain") as client:
            client.set_access_token("bad-token")

            mock_request = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Token validation failed"

            original_error = httpx.HTTPStatusError("401 Unauthorized", request=mock_request, response=mock_response)

            with patch("span_panel_api.generated_client.api.default.get_panel_state_api_v1_panel_get.asyncio") as mock_panel:
                mock_panel.side_effect = original_error

                with pytest.raises(SpanPanelAuthError) as exc_info:
                    await client.get_panel_state()

                # Verify the error chain - HTTPStatusError gets converted to UnexpectedStatus
                assert isinstance(exc_info.value.__cause__, UnexpectedStatus)
                assert exc_info.value.__cause__.status_code == 401
                assert "Authentication failed" in str(exc_info.value)
                assert "Status 401" in str(exc_info.value)
