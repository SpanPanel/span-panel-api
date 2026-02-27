"""Tests for simulation edge cases and missing coverage lines."""

import pytest
from pathlib import Path

from span_panel_api.simulation import DynamicSimulationEngine
from span_panel_api.exceptions import SimulationConfigurationError


class TestSimulationMissingCoverage:
    """Test cases to cover missing lines in simulation.py."""

    @pytest.mark.asyncio
    async def test_no_config_provided_error(self):
        """Test error when no config is provided (line 381)."""
        engine = DynamicSimulationEngine()

        # This should hit line 381 (no config provided error)
        with pytest.raises(ValueError, match="YAML configuration is required"):
            await engine.initialize_async()

    @pytest.mark.asyncio
    async def test_simulation_time_init_error(self):
        """Test simulation time initialization error (lines 389-390)."""
        # Create engine with invalid config that will cause simulation time init error
        invalid_config = {
            "panel_config": {"serial_number": "test", "total_tabs": 40, "main_size": 200},
            "circuit_templates": {
                "test": {
                    "energy_profile": {
                        "mode": "consumer",
                        "power_range": [0, 100],
                        "typical_power": 50,
                        "power_variation": 0.1,
                    },
                    "relay_behavior": "controllable",
                    "priority": "MUST_HAVE",
                }
            },
            "circuits": [{"id": "test", "name": "Test", "template": "test", "tabs": [1]}],
            "unmapped_tabs": [],
            "simulation_params": {},
        }

        engine = DynamicSimulationEngine(config_data=invalid_config)

        # This should hit lines 389-390 (simulation time init error)
        with pytest.raises(SimulationConfigurationError, match="Simulation configuration is required"):
            engine._initialize_simulation_time()
