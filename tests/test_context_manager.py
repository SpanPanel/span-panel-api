"""Tests for the context manager fix that resolves the "Cannot open a client instance more than once" error."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from span_panel_api import SpanPanelClient
from span_panel_api.const import RETRY_MAX_ATTEMPTS
from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelTimeoutError
from tests.test_factories import create_sim_client


class TestContextManagerFix:
    """Test suite for the context manager lifecycle fix."""

    @pytest.mark.asyncio
    async def test_unauthenticated_requests_work_properly(self):
        """Test that unauthenticated requests (like get_status) work both inside and outside context managers."""
        client = create_sim_client()  # Use simulation mode for testing

        # Test unauthenticated call OUTSIDE context manager
        # This should work without any access token set
        assert client._access_token is None
        status_outside_context = await client.get_status()
        assert status_outside_context is not None
        assert status_outside_context.system.manufacturer == "Span"

        # Test unauthenticated call INSIDE context manager WITHOUT setting token
        async with client:
            assert client._in_context is True
            assert client._access_token is None  # Still no token set

            status_inside_context = await client.get_status()
            assert status_inside_context is not None
            assert status_inside_context.system.manufacturer == "Span"

        # Test unauthenticated call INSIDE context manager WITH token set
        # (status endpoint should still work even if token is set since it doesn't require auth)
        # Create a new client for this test since the previous one was closed
        client2 = create_sim_client()
        async with client2:
            client2.set_access_token("test-token")
            assert client2._access_token == "test-token"

            status_with_token = await client2.get_status()
            assert status_with_token is not None
            assert status_with_token.system.manufacturer == "Span"

    @pytest.mark.asyncio
    async def test_context_manager_multiple_api_calls(self):
        """Test that multiple API calls work within a single context manager without double context errors."""
        client = create_sim_client()

        # Test multiple calls within a single context manager
        async with client:
            # Verify we're in context
            assert client._in_context is True
            assert client._client is not None

            # Multiple calls should not cause "Cannot open a client instance more than once"
            # These calls are unauthenticated (status endpoint doesn't require auth)
            status1 = await client.get_status()
            assert status1 is not None

            status2 = await client.get_status()
            assert status2 is not None

            # Set access token for authenticated calls (not needed in simulation but tests the flow)
            client.set_access_token("test-token")

            circuits = await client.get_circuits()
            assert circuits is not None

            panel = await client.get_panel_state()
            assert panel is not None

            storage = await client.get_storage_soe()
            assert storage is not None

        # Verify we exited context properly
        assert client._in_context is False

    @pytest.mark.asyncio
    async def test_context_manager_with_authentication_flow(self):
        """Test that authentication within a context manager doesn't break the context."""
        client = SpanPanelClient(
            "192.168.1.100",
            timeout=5.0,
        )

        with (
            patch("span_panel_api.client.generate_jwt_api_v1_auth_register_post") as mock_auth,
            patch("span_panel_api.client.get_circuits_api_v1_circuits_get") as mock_circuits,
            patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel_state,
            patch("span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post") as mock_set_circuit,
        ):
            # Setup mock responses
            auth_response = MagicMock(access_token="test-token-12345", token_type="Bearer")
            mock_auth.asyncio = AsyncMock(return_value=auth_response)

            # Mock circuits response with proper to_dict method
            circuits_response = MagicMock(circuits=MagicMock(additional_properties={}))
            circuits_response.to_dict.return_value = {"circuits": {}}
            mock_circuits.asyncio = AsyncMock(return_value=circuits_response)
            mock_circuits.asyncio_detailed = AsyncMock(return_value=MagicMock(status_code=200, parsed=circuits_response))

            mock_panel_state.asyncio = AsyncMock(return_value=MagicMock(branches=[]))
            mock_set_circuit.asyncio = AsyncMock(return_value=MagicMock(priority="MUST_HAVE"))

            async with client:
                # Verify initial state
                assert client._in_context is True
                assert client._access_token is None
                initial_client = client._client
                initial_async_client = getattr(initial_client, "_async_client", None)

                # Authenticate - this WILL upgrade the client to AuthenticatedClient within context
                auth_result = await client.authenticate("test-app", "Test Application")
                assert auth_result.access_token == "test-token-12345"
                assert client._access_token == "test-token-12345"

                # Client should be upgraded to AuthenticatedClient but preserve the httpx async client
                from span_panel_api.generated_client import AuthenticatedClient

                assert isinstance(client._client, AuthenticatedClient)
                # The underlying httpx async client should be preserved to avoid double context issues
                assert getattr(client._client, "_async_client", None) is initial_async_client

                # Post-authentication calls should work
                circuits = await client.get_circuits()
                assert circuits is not None

                set_result = await client.set_circuit_priority("circuit_1", "MUST_HAVE")
                assert set_result is not None

            # Verify clean exit
            assert client._in_context is False

    @pytest.mark.asyncio
    async def test_context_manager_error_handling_preserves_state(self):
        """Test that errors within the context don't break the context manager state."""
        client = SpanPanelClient(
            "192.168.1.100",
            timeout=5.0,
        )

        with patch("span_panel_api.client.system_status_api_v1_status_get") as mock_status:
            # First call succeeds, next calls fail (with retry attempts)
            # The retry logic will attempt up to RETRY_MAX_ATTEMPTS times for TimeoutException
            side_effects = [
                MagicMock(system=MagicMock(manufacturer="SPAN")),  # First call succeeds
                # Generate RETRY_MAX_ATTEMPTS timeout exceptions for the retries
                *([httpx.TimeoutException("Request timeout")] * RETRY_MAX_ATTEMPTS),
            ]
            mock_status.asyncio = AsyncMock(side_effect=side_effects)

            async with client:
                # First call should succeed
                status1 = await client.get_status()
                assert status1 is not None

                # Second call should fail but not break context
                with pytest.raises(SpanPanelTimeoutError):
                    await client.get_status()

                # Client should still be in context and functional
                assert client._in_context is True
                assert client._client is not None

            # Should exit cleanly even after error
            assert client._in_context is False

    @pytest.mark.asyncio
    async def test_set_access_token_behavior_in_context(self):
        """Test that set_access_token properly upgrades client when in context."""
        client = SpanPanelClient("192.168.1.100")

        # Test outside context first
        client.set_access_token("token1")
        assert client._access_token == "token1"
        assert client._client is None  # Should be reset when not in context

        async with client:
            initial_client = client._client
            initial_async_client = getattr(initial_client, "_async_client", None)
            assert initial_client is not None
            assert client._in_context is True

            # Setting token within context should upgrade to AuthenticatedClient
            # but preserve the underlying httpx async client
            client.set_access_token("token2")
            assert client._access_token == "token2"

            # Client should be upgraded to AuthenticatedClient
            from span_panel_api.generated_client import AuthenticatedClient

            assert isinstance(client._client, AuthenticatedClient)
            # But the underlying httpx async client should be preserved
            assert getattr(client._client, "_async_client", None) is initial_async_client

        assert client._in_context is False

    @pytest.mark.asyncio
    async def test_context_manager_lifecycle_integrity(self):
        """Test the complete context manager lifecycle integrity."""
        client = SpanPanelClient("192.168.1.100")

        # Initially not in context
        assert client._in_context is False
        assert client._client is None

        async with client:
            # Should be in context with client
            assert client._in_context is True
            assert client._client is not None
            context_client = client._client

            # Client should be consistent across calls
            endpoint_client = client._get_client_for_endpoint(requires_auth=False)
            assert endpoint_client is context_client

        # Should cleanly exit context
        assert client._in_context is False
        # Client should be cleaned up but access token preserved
        assert client._client is None

    @pytest.mark.asyncio
    async def test_context_manager_exception_during_exit(self):
        """Test that exceptions during context exit are handled gracefully."""
        client = SpanPanelClient("192.168.1.100")

        with patch.object(client, "_client") as mock_client:
            # Make the __aexit__ raise an exception
            mock_client.__aexit__ = AsyncMock(side_effect=Exception("Exit error"))

            async with client:
                assert client._in_context is True

            # Should still mark as not in context despite exit error
            assert client._in_context is False

    @pytest.mark.asyncio
    async def test_client_none_in_context_error(self):
        """Test that we get a clear error if client becomes None while in context."""
        client = SpanPanelClient("192.168.1.100")

        async with client:
            # Artificially set client to None to simulate the error condition
            client._client = None

            # Should raise a clear error
            with pytest.raises(SpanPanelAPIError, match="Client is None while in context"):
                client._get_client_for_endpoint(requires_auth=False)

    @pytest.mark.asyncio
    async def test_multiple_context_managers_not_allowed(self):
        """Test that we can't enter the same client context multiple times."""
        client = SpanPanelClient("192.168.1.100")

        async with client:
            # Trying to enter context again should fail at the httpx level
            with pytest.raises(RuntimeError, match="Cannot open a client instance more than once"):
                async with client:
                    pass

    def test_context_tracking_flag_behavior(self):
        """Test the _in_context flag behavior in various scenarios."""
        client = SpanPanelClient("192.168.1.100")

        # Initially false
        assert client._in_context is False

        # Should stay false when not in context
        client.set_access_token("test-token")
        assert client._in_context is False

        # get_client_for_endpoint should work outside context
        endpoint_client = client._get_client_for_endpoint(requires_auth=False)
        assert endpoint_client is not None
        assert client._in_context is False  # Still false after getting client


class TestContextManagerEdgeCases:
    """Test context manager edge cases for better coverage."""

    @pytest.mark.asyncio
    async def test_context_entry_failure_with_client_error(self):
        """Test context manager entry failure when client.__aenter__ fails."""
        client = SpanPanelClient("192.168.1.100")

        # Mock the client to fail on __aenter__
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(side_effect=Exception("Client enter failed"))

        with patch.object(client, "_get_unauthenticated_client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Failed to enter client context"):
                async with client:
                    pass

            # Verify state was reset
            assert not client._in_context
            assert not client._httpx_client_owned

    @pytest.mark.asyncio
    async def test_context_manager_when_client_is_none_error(self):
        """Test _get_client_for_endpoint when client is None in context."""
        client = SpanPanelClient("192.168.1.100")
        client._in_context = True
        client._client = None
        client._access_token = "test-token"  # Set token so auth check passes

        with pytest.raises(SpanPanelAPIError, match="Client is None while in context"):
            client._get_client_for_endpoint()

    @pytest.mark.asyncio
    async def test_context_manager_client_type_mismatch(self):
        """Test client type mismatch error in context."""
        from span_panel_api.generated_client import Client

        client = SpanPanelClient("192.168.1.100")
        client._in_context = True
        client._access_token = "test-token"

        # Create a regular Client when we need AuthenticatedClient
        unauthenticated_client = Client(
            base_url="http://test", timeout=httpx.Timeout(30.0), verify_ssl=False, raise_on_unexpected_status=True
        )
        client._client = unauthenticated_client

        with pytest.raises(SpanPanelAPIError, match="Client type mismatch"):
            client._get_client_for_endpoint(requires_auth=True)

    @pytest.mark.asyncio
    async def test_context_manager_cleanup_with_client(self):
        """Test context manager cleanup with existing client."""
        client = SpanPanelClient("192.168.1.100")

        # Mock a client
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_get_unauthenticated_client", return_value=mock_client):
            async with client:
                # Should set the client during context entry
                assert client._client is mock_client
                assert client._in_context is True

            # Should clean up after context exit
            assert client._in_context is False

    @pytest.mark.asyncio
    async def test_context_manager_cleanup_with_exception(self):
        """Test context manager cleanup when an exception occurs."""
        client = SpanPanelClient("192.168.1.100")

        # Mock a client that doesn't throw during cleanup
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_get_client", return_value=mock_client):
            with pytest.raises(ValueError, match="Test exception"):
                async with client:
                    assert client._in_context is True
                    raise ValueError("Test exception")

            # Should still clean up properly even with exception
            assert client._in_context is False

    @pytest.mark.asyncio
    async def test_context_manager_entry_exit(self):
        """Test context manager entry and exit behavior."""
        client = SpanPanelClient("192.168.1.100")

        # Mock client
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch.object(client, "_get_client", return_value=mock_client):
            async with client as context_client:
                assert context_client is client
                assert client._in_context is True
                assert client._httpx_client_owned is True

            assert client._in_context is False
