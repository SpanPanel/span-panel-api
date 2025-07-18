"""Tests for simulation edge cases and missing coverage lines."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from span_panel_api import SpanPanelClient
from span_panel_api.simulation import SimulationConfigurationError
import time


class TestSimulationMissingCoverage:
    """Test cases to cover missing lines in simulation.py."""

    @pytest.mark.asyncio
    async def test_battery_behavior_disabled(self):
        """Test battery behavior when disabled in config (line 269)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="battery-disabled-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get the simulation engine and ensure it's initialized
            engine = client._simulation_engine
            assert engine is not None
            await engine.initialize_async()

            # Modify battery template to disable battery behavior
            battery_template = engine._config["circuit_templates"]["battery"]
            battery_template["battery_behavior"]["enabled"] = False

            # Test that battery behavior returns base power when disabled
            behavior_engine = engine._behavior_engine
            template = engine._config["circuit_templates"]["battery"]

            # This should hit line 269 (battery behavior disabled check)
            power = behavior_engine._apply_battery_behavior(1000.0, template, time.time())
            assert power == 1000.0  # Should return base power unchanged

    @pytest.mark.asyncio
    async def test_battery_behavior_not_dict(self):
        """Test battery behavior when config is not a dict (line 274)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="battery-not-dict-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get the simulation engine and ensure it's initialized
            engine = client._simulation_engine
            assert engine is not None
            await engine.initialize_async()

            # Modify battery template to have non-dict battery_behavior
            battery_template = engine._config["circuit_templates"]["battery"]
            battery_template["battery_behavior"] = "not_a_dict"

            # Test that battery behavior returns base power when config is not dict
            behavior_engine = engine._behavior_engine

            # This should hit line 274 (battery config not dict check)
            power = behavior_engine._apply_battery_behavior(1000.0, battery_template, time.time())
            assert power == 1000.0  # Should return base power unchanged

    @pytest.mark.asyncio
    async def test_simulation_time_invalid_format(self):
        """Test simulation time initialization with invalid datetime format (lines 427, 434-435)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="invalid-time-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get the simulation engine
            engine = client._simulation_engine
            assert engine is not None

            # Test override_simulation_start_time with invalid format
            # This should hit lines 434-435 (invalid datetime fallback)
            engine.override_simulation_start_time("invalid-datetime-format")

            # Should fall back to real time (use_simulation_time = False)
            assert not engine._use_simulation_time

    @pytest.mark.asyncio
    async def test_template_reference_validation(self):
        """Test circuit template reference validation (line 472)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="template-validation-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get the simulation engine and ensure it's initialized
            engine = client._simulation_engine
            assert engine is not None
            await engine.initialize_async()

            # Test validation with unknown template reference
            invalid_circuit = {"id": "test_circuit", "name": "Test Circuit", "template": "nonexistent_template", "tabs": [1]}

            # This should hit line 472 (template reference validation)
            with pytest.raises(ValueError, match="references unknown template"):
                engine._validate_single_circuit(0, invalid_circuit, engine._config["circuit_templates"])

    @pytest.mark.asyncio
    async def test_primary_secondary_power_split(self):
        """Test primary/secondary power split logic (lines 1033-1040)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="power-split-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get the simulation engine and ensure it's initialized
            engine = client._simulation_engine
            assert engine is not None
            await engine.initialize_async()

            # Create a sync config with primary_secondary split
            sync_config = {
                "tabs": [33, 35],
                "behavior": "240v_split_phase",
                "power_split": "primary_secondary",
                "energy_sync": True,
                "template": "battery_sync",
            }

            # Test primary tab gets full power
            primary_power = engine._get_synchronized_power(33, 1000.0, sync_config)
            assert primary_power == 1000.0

            # Test secondary tab gets 0 power
            secondary_power = engine._get_synchronized_power(35, 1000.0, sync_config)
            assert secondary_power == 0.0

    @pytest.mark.asyncio
    async def test_tab_sync_config_not_found(self):
        """Test tab sync config not found scenario (line 1006)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="sync-config-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get the simulation engine and ensure it's initialized
            engine = client._simulation_engine
            assert engine is not None
            await engine.initialize_async()

            # Test with a tab that has no sync config
            # This should hit line 1006 (tab sync config not found)
            sync_config = engine._get_tab_sync_config(999)  # Non-existent tab
            assert sync_config is None

    @pytest.mark.asyncio
    async def test_energy_sync_fallback(self):
        """Test energy sync fallback when sync config fails (line 1077)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="energy-sync-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get the simulation engine
            engine = client._simulation_engine
            assert engine is not None

            # Test energy synchronization with a tab that has no sync group
            # This should hit line 1077 (energy sync fallback)
            produced, consumed = engine._synchronize_energy_for_tab(999, "test_circuit", 100.0, time.time())
            assert isinstance(produced, float)
            assert isinstance(consumed, float)

    @pytest.mark.asyncio
    async def test_no_config_provided_error(self):
        """Test error when no config is provided (line 381)."""
        # Test initialization without config
        from span_panel_api.simulation import DynamicSimulationEngine

        engine = DynamicSimulationEngine()

        # This should hit line 381 (no config provided error)
        with pytest.raises(ValueError, match="YAML configuration is required"):
            await engine.initialize_async()

    @pytest.mark.asyncio
    async def test_simulation_time_init_error(self):
        """Test simulation time initialization error (lines 389-390)."""
        from span_panel_api.simulation import DynamicSimulationEngine

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

    @pytest.mark.asyncio
    async def test_soe_calculation_edge_cases(self):
        """Test SOE calculation edge cases (lines 876, 881)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="soe-edge-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Get the simulation engine
            engine = client._simulation_engine
            assert engine is not None

            # Test SOE calculation with no battery circuits
            # This should hit line 876 (no battery circuits)
            with patch.object(engine, '_config') as mock_config:
                mock_config.get.return_value = []  # No battery circuits
                soe = engine._calculate_dynamic_soe()
                assert 15.0 <= soe <= 95.0

            # Test SOE calculation with battery circuits
            # This should hit line 881 (with battery circuits)
            soe = engine._calculate_dynamic_soe()
            assert 15.0 <= soe <= 95.0
