"""Tests for client simulation error conditions."""

import pytest
from src.span_panel_api.client import SpanPanelClient
from src.span_panel_api.exceptions import SpanPanelAPIError


class TestClientSimulationErrors:
    """Tests for client simulation error conditions."""

    async def test_simulation_engine_not_initialized_error(self):
        """Test error when simulation engine is not initialized."""
        client = SpanPanelClient(host="test-serial", simulation_mode=True)

        # Manually set simulation engine to None to trigger error
        client._simulation_engine = None

        async with client:
            with pytest.raises(SpanPanelAPIError, match="Simulation engine not initialized"):
                await client._get_status_simulation()

    async def test_storage_simulation_error(self):
        """Test get_storage in simulation mode with uninitialized engine."""
        client = SpanPanelClient(
            host="test-serial", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            # First ensure initialization happens
            await client.get_status()

            # Now set engine to None to trigger error
            client._simulation_engine = None

            with pytest.raises(SpanPanelAPIError, match="Simulation engine not initialized"):
                await client.get_storage_soe()

    async def test_panel_state_simulation_error(self):
        """Test get_panel_state in simulation mode with uninitialized engine."""
        client = SpanPanelClient(
            host="test-serial", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            # First ensure initialization happens
            await client.get_status()

            # Now set engine to None to trigger error
            client._simulation_engine = None

            with pytest.raises(SpanPanelAPIError, match="Simulation engine not initialized"):
                await client._get_panel_state_simulation()

    async def test_simulation_methods_coverage(self):
        """Test simulation method paths."""
        client = SpanPanelClient(
            host="test-serial", simulation_mode=True, simulation_config_path="examples/simple_test_config.yaml"
        )

        async with client:
            # Test all simulation methods
            status = await client.get_status()
            assert status is not None

            soe = await client.get_storage_soe()
            assert soe is not None

            circuits = await client.get_circuits()
            assert circuits is not None

            panel_state = await client.get_panel_state()
            assert panel_state is not None
