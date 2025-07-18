"""Tests for client simulation start time override functionality."""

import pytest
from pathlib import Path
from span_panel_api import SpanPanelClient


class TestClientSimulationStartTime:
    """Test client simulation start time override functionality."""

    @pytest.mark.asyncio
    async def test_simulation_start_time_override_initialization(self):
        """Test simulation start time override during client initialization."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        client = SpanPanelClient(
            host="localhost",
            simulation_mode=True,
            simulation_config_path=str(config_path),
            simulation_start_time="2024-06-15T12:00:00",
        )

        async with client:
            # Verify the override was set during construction
            assert client._simulation_start_time_override == "2024-06-15T12:00:00"

            # Trigger initialization by making an API call
            status = await client.get_status()
            assert status is not None

            # Now verify initialization completed
            assert client._simulation_initialized is True
            assert client._simulation_engine is not None
