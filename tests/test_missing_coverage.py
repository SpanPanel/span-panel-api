"""Tests targeting missing coverage lines in client.py.

This module contains tests specifically designed to exercise code paths
that are currently missing test coverage, focusing on error conditions
and edge cases that are difficult to reach in normal operation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from span_panel_api.exceptions import SpanPanelAPIError, SpanPanelAuthError
from tests.test_factories import create_live_client, create_sim_client


class TestMissingCoverageErrorPaths:
    """Test error paths that are missing coverage."""

    @pytest.mark.asyncio
    async def test_authentication_error_paths(self):
        """Test authentication error handling paths (lines 559-566)."""
        client = create_live_client(cache_window=0)

        # Test ValueError in authentication
        with patch("span_panel_api.client.generate_jwt_api_v1_auth_register_post") as mock_auth:
            mock_auth.asyncio = AsyncMock(side_effect=ValueError("Invalid input"))

            with pytest.raises(SpanPanelAPIError, match="API error: Invalid input"):
                await client.authenticate("test", "description")

        # Test generic exception in authentication
        with patch("span_panel_api.client.generate_jwt_api_v1_auth_register_post") as mock_auth:
            mock_auth.asyncio = AsyncMock(side_effect=RuntimeError("Unexpected error"))

            with pytest.raises(SpanPanelAPIError, match="API error: Unexpected error"):
                await client.authenticate("test", "description")

    @pytest.mark.asyncio
    async def test_panel_state_error_paths(self):
        """Test panel state error handling paths (lines 738, 743, 745)."""
        client = create_live_client(cache_window=0)
        client.set_access_token("test-token")

        # Test ValueError in panel state
        with patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel:
            mock_panel.asyncio = AsyncMock(side_effect=ValueError("Validation error"))

            with pytest.raises(SpanPanelAPIError, match="API error: Validation error"):
                await client.get_panel_state()

        # Test generic exception with 401 in string in panel state (line 745)
        with patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel:
            mock_panel.asyncio = AsyncMock(side_effect=RuntimeError("401 Unauthorized access"))

            with pytest.raises(SpanPanelAuthError, match="Authentication required"):
                await client.get_panel_state()

        # Test generic exception without 401 in string
        with patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel:
            mock_panel.asyncio = AsyncMock(side_effect=RuntimeError("Some other error"))

            with pytest.raises(SpanPanelAPIError, match="Unexpected error: Some other error"):
                await client.get_panel_state()

    @pytest.mark.asyncio
    async def test_circuits_error_paths(self):
        """Test circuits error handling paths (lines 871, 876, 878)."""
        client = create_live_client(cache_window=0)
        client.set_access_token("test-token")

        # Test ValueError in circuits
        with patch("span_panel_api.client.get_circuits_api_v1_circuits_get") as mock_circuits:
            mock_circuits.asyncio = AsyncMock(side_effect=ValueError("Circuit validation error"))

            with pytest.raises(SpanPanelAPIError, match="API error: Circuit validation error"):
                await client.get_circuits()

        # Test generic exception in circuits
        with patch("span_panel_api.client.get_circuits_api_v1_circuits_get") as mock_circuits:
            mock_circuits.asyncio = AsyncMock(side_effect=RuntimeError("Circuit error"))

            with pytest.raises(SpanPanelAPIError, match="Unexpected error: Circuit error"):
                await client.get_circuits()

    @pytest.mark.asyncio
    async def test_storage_soe_error_paths(self):
        """Test storage SOE error handling paths (lines 982, 990, 997)."""
        client = create_live_client(cache_window=0)
        client.set_access_token("test-token")

        # Test ValueError in storage SOE
        with patch("span_panel_api.client.get_storage_soe_api_v1_storage_soe_get") as mock_storage:
            mock_storage.asyncio = AsyncMock(side_effect=ValueError("Storage validation error"))

            with pytest.raises(SpanPanelAPIError, match="API error: Storage validation error"):
                await client.get_storage_soe()

        # Test generic exception in storage SOE
        with patch("span_panel_api.client.get_storage_soe_api_v1_storage_soe_get") as mock_storage:
            mock_storage.asyncio = AsyncMock(side_effect=RuntimeError("Storage error"))

            with pytest.raises(SpanPanelAPIError, match="Unexpected error: Storage error"):
                await client.get_storage_soe()

    @pytest.mark.asyncio
    async def test_set_circuit_relay_error_paths(self):
        """Test set circuit relay error handling paths (lines 1030-1032, 1047, 1052, 1054)."""
        client = create_live_client(cache_window=0)
        client.set_access_token("test-token")

        # Test ValueError in set circuit relay (test the built-in validation)
        with pytest.raises(SpanPanelAPIError, match="Invalid relay state 'INVALID'"):
            await client.set_circuit_relay("circuit-1", "INVALID")

        # Test generic exception in set circuit relay
        with patch("span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post") as mock_set:
            mock_set.asyncio = AsyncMock(side_effect=RuntimeError("Relay error"))

            with pytest.raises(SpanPanelAPIError, match="Unexpected error: Relay error"):
                await client.set_circuit_relay("circuit-1", "OPEN")

    @pytest.mark.asyncio
    async def test_set_circuit_priority_error_paths(self):
        """Test set circuit priority error handling paths (lines 1104, 1109, 1111)."""
        client = create_live_client(cache_window=0)
        client.set_access_token("test-token")

        # Test ValueError in set circuit priority (test the built-in validation)
        with pytest.raises(SpanPanelAPIError, match="'INVALID' is not a valid Priority"):
            await client.set_circuit_priority("circuit-1", "INVALID")

        # Test generic exception in set circuit priority
        with patch("span_panel_api.client.set_circuit_state_api_v_1_circuits_circuit_id_post") as mock_set:
            mock_set.asyncio = AsyncMock(side_effect=RuntimeError("Priority error"))

            with pytest.raises(SpanPanelAPIError, match="Unexpected error: Priority error"):
                await client.set_circuit_priority("circuit-1", "MUST_HAVE")

    @pytest.mark.asyncio
    async def test_cache_hit_coverage(self):
        """Test cache hit paths to ensure they're covered (lines 721, 829, 976)."""
        client = create_live_client(cache_window=1.0)  # Enable caching
        client.set_access_token("test-token")

        # Test panel state cache hit (line 721)
        with patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel:
            mock_response = MagicMock()
            mock_panel.asyncio = AsyncMock(return_value=mock_response)

            # First call - should hit API and cache result
            result1 = await client.get_panel_state()
            assert mock_panel.asyncio.call_count == 1

            # Second call - should hit cache (line 721)
            result2 = await client.get_panel_state()
            assert mock_panel.asyncio.call_count == 1  # No additional call
            assert result1 == result2

        # Test circuits cache hit (line 829)
        with (
            patch("span_panel_api.client.get_circuits_api_v1_circuits_get") as mock_circuits,
            patch("span_panel_api.client.get_panel_state_api_v1_panel_get") as mock_panel_for_circuits,
        ):
            mock_circuits_response = MagicMock()
            mock_circuits_response.circuits = MagicMock()
            mock_circuits_response.circuits.additional_properties = {}
            mock_circuits.asyncio = AsyncMock(return_value=mock_circuits_response)

            mock_panel_response = MagicMock()
            mock_panel_response.branches = []
            mock_panel_for_circuits.asyncio = AsyncMock(return_value=mock_panel_response)

            # First call - should hit API and cache result
            result1 = await client.get_circuits()
            circuits_call_count = mock_circuits.asyncio.call_count

            # Second call - should hit cache (line 829)
            result2 = await client.get_circuits()
            assert mock_circuits.asyncio.call_count == circuits_call_count  # No additional call

        # Test storage SOE cache hit (line 976)
        with patch("span_panel_api.client.get_storage_soe_api_v1_storage_soe_get") as mock_storage:
            mock_storage_response = MagicMock()
            mock_storage.asyncio = AsyncMock(return_value=mock_storage_response)

            # First call - should hit API and cache result
            result1 = await client.get_storage_soe()
            assert mock_storage.asyncio.call_count == 1

            # Second call - should hit cache (line 976)
            result2 = await client.get_storage_soe()
            assert mock_storage.asyncio.call_count == 1  # No additional call
            assert result1 == result2


class TestEdgeCaseScenarios:
    """Test edge case scenarios for improved coverage."""

    @pytest.mark.asyncio
    async def test_create_unmapped_tab_circuit_coverage(self):
        """Test the _create_unmapped_tab_circuit method coverage."""
        client = create_live_client(cache_window=0)
        client.set_access_token("test-token")

        # Create a mock branch for testing
        mock_branch = MagicMock()
        mock_branch.id = 1
        mock_branch.relay_state = "CLOSED"

        # Test the _create_unmapped_tab_circuit method
        circuit = client._create_unmapped_tab_circuit(mock_branch, 2)

        assert circuit.id == "unmapped_tab_2"
        assert circuit.name == "Unmapped Tab 2"
        assert circuit.tabs == [2]
        assert circuit.relay_state.value == "UNKNOWN"  # Default value for unmapped circuits
        assert circuit.priority.value == "UNKNOWN"
        assert circuit.is_user_controllable is False

    def test_simulation_mode_initialization(self):
        """Test simulation mode initialization paths."""
        # Test simulation mode enabled
        sim_client = create_sim_client()
        assert sim_client._simulation_mode is True
        assert sim_client._simulation_engine is not None

        # Test live mode
        live_client = create_live_client()
        assert live_client._simulation_mode is False
        assert live_client._simulation_engine is None
