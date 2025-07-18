"""Final coverage tests for simulation.py missing lines."""

import pytest
import time
from pathlib import Path
from unittest.mock import Mock, patch
import yaml

from span_panel_api import SpanPanelClient, SimulationConfigurationError
from span_panel_api.simulation import DynamicSimulationEngine


class TestSimulationTimeInitialization:
    """Test simulation time initialization missing lines."""

    @pytest.mark.asyncio
    async def test_simulation_time_with_start_time_string(self):
        """Test simulation time initialization with start_time_str in config."""
        # Create a config with simulation start time
        config_data = {
            "panel_config": {"serial_number": "TEST-PANEL-001", "total_tabs": 40, "main_size": 200},
            "circuit_templates": {
                "test_template": {
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
            "circuits": [{"id": "test_circuit", "name": "Test Circuit", "template": "test_template", "tabs": [1]}],
            "unmapped_tabs": [],
            "simulation_params": {"use_simulation_time": True, "simulation_start_time": "2024-06-15T12:00:00"},
        }

        engine = DynamicSimulationEngine(config_data=config_data)
        await engine.initialize_async()

        # Should have simulation time enabled
        assert engine._use_simulation_time

    @pytest.mark.asyncio
    async def test_override_simulation_time_exception_handling(self):
        """Test override_simulation_start_time exception handling."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="test-override-exception", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            engine = client._simulation_engine
            assert engine is not None

            # Test with invalid time format that will cause exception
            engine.override_simulation_start_time("invalid-time-format")

            # Should disable simulation time due to exception
            assert not engine._use_simulation_time


class TestSOECalculationPaths:
    """Test SOE calculation missing lines."""

    @pytest.mark.asyncio
    async def test_soe_calculation_with_battery_charging(self):
        """Test SOE calculation when battery is charging significantly."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="test-soe-charging", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            engine = client._simulation_engine
            assert engine is not None

            # Mock battery circuits to have high charging power
            with patch.object(engine, "_config") as mock_config:
                # Create a mock config with battery circuits having high charging power
                mock_config.__getitem__.side_effect = lambda key: {
                    "circuits": [
                        {"id": "battery_system_1", "template": "battery", "tabs": [33, 35]},
                        {"id": "battery_system_2", "template": "battery", "tabs": [34, 36]},
                    ]
                }[key]

                # Mock the circuits data to have high charging power
                with patch.object(engine, "_base_data") as mock_base_data:
                    mock_base_data.__getitem__.return_value = {
                        "circuits": {
                            "additional_properties": {
                                "battery_system_1": Mock(instant_power_w=-2000.0),
                                "battery_system_2": Mock(instant_power_w=-1500.0),
                            }
                        }
                    }

                    soe_data = await engine.get_soe()
                    assert soe_data["soe"]["percentage"] >= 75.0  # Should be higher due to charging

    @pytest.mark.asyncio
    async def test_soe_calculation_with_battery_discharging(self):
        """Test SOE calculation when battery is discharging significantly."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="test-soe-discharging", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            engine = client._simulation_engine
            assert engine is not None

            # Mock battery circuits to have high discharging power
            with patch.object(engine, "_config") as mock_config:
                mock_config.__getitem__.side_effect = lambda key: {
                    "circuits": [
                        {"id": "battery_system_1", "template": "battery", "tabs": [33, 35]},
                        {"id": "battery_system_2", "template": "battery", "tabs": [34, 36]},
                    ]
                }[key]

                with patch.object(engine, "_base_data") as mock_base_data:
                    mock_base_data.__getitem__.return_value = {
                        "circuits": {
                            "additional_properties": {
                                "battery_system_1": Mock(instant_power_w=2000.0),
                                "battery_system_2": Mock(instant_power_w=1500.0),
                            }
                        }
                    }

                    soe_data = await engine.get_soe()
                    # Just verify we get a valid SOE value, don't make assumptions about the exact value
                    assert isinstance(soe_data["soe"]["percentage"], float)
                    assert 0.0 <= soe_data["soe"]["percentage"] <= 100.0


class TestTabSynchronizationEdgeCases:
    """Test tab synchronization edge cases."""

    @pytest.mark.asyncio
    async def test_tab_sync_config_without_config(self):
        """Test _get_tab_sync_config when no config is loaded."""
        engine = DynamicSimulationEngine()

        # Should raise SimulationConfigurationError when no config
        with pytest.raises(
            SimulationConfigurationError, match="Simulation configuration is required for tab synchronization"
        ):
            engine._get_tab_sync_config(1)

    @pytest.mark.asyncio
    async def test_energy_synchronization_with_sync_enabled(self):
        """Test energy synchronization when energy_sync is enabled."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="test-energy-sync-enabled", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            engine = client._simulation_engine
            assert engine is not None

            # Test energy synchronization for a tab that has sync enabled
            # This tests the missing lines in _synchronize_energy_for_tab
            produced, consumed = engine._synchronize_energy_for_tab(33, "test_circuit", 100.0, time.time())
            assert isinstance(produced, float)
            assert isinstance(consumed, float)

    @pytest.mark.asyncio
    async def test_energy_synchronization_without_sync_group(self):
        """Test energy synchronization when sync group is not found."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="test-energy-sync-no-group", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            engine = client._simulation_engine
            assert engine is not None

            # Test with a tab that has sync config but no sync group
            # This tests the fallback path
            with patch.object(engine, "_tab_sync_groups", {}):
                produced, consumed = engine._synchronize_energy_for_tab(33, "test_circuit", 100.0, time.time())
                assert isinstance(produced, float)
                assert isinstance(consumed, float)


class TestSimulationTimeAcceleration:
    """Test simulation time with acceleration."""

    @pytest.mark.asyncio
    async def test_simulation_time_with_acceleration_config(self):
        """Test simulation time calculation with time acceleration."""
        config_data = {
            "panel_config": {"serial_number": "TEST-PANEL-001", "total_tabs": 40, "main_size": 200},
            "circuit_templates": {
                "test_template": {
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
            "circuits": [{"id": "test_circuit", "name": "Test Circuit", "template": "test_template", "tabs": [1]}],
            "unmapped_tabs": [],
            "simulation_params": {
                "use_simulation_time": True,
                "simulation_start_time": "2024-06-15T12:00:00",
                "time_acceleration": 2.0,
            },
        }

        engine = DynamicSimulationEngine(config_data=config_data)
        await engine.initialize_async()

        # Test get_current_simulation_time with acceleration
        sim_time = engine.get_current_simulation_time()
        assert isinstance(sim_time, float)
        assert sim_time > 0


class TestCircuitGenerationWithSync:
    """Test circuit generation with tab synchronization."""

    @pytest.mark.asyncio
    async def test_circuit_generation_with_synchronized_tabs(self):
        """Test circuit generation when tabs are synchronized."""
        config_data = {
            "panel_config": {"serial_number": "TEST-PANEL-001", "total_tabs": 40, "main_size": 200},
            "circuit_templates": {
                "test_template": {
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
            "circuits": [
                {
                    "id": "test_circuit",
                    "name": "Test Circuit",
                    "template": "test_template",
                    "tabs": [33, 35],  # Multi-tab circuit
                }
            ],
            "unmapped_tabs": [],
            "simulation_params": {"noise_factor": 0.02},
            "tab_synchronizations": [
                {
                    "tabs": [33, 35],
                    "behavior": "240v_split_phase",
                    "power_split": "equal",
                    "energy_sync": True,
                    "template": "test_sync",
                }
            ],
        }

        engine = DynamicSimulationEngine(config_data=config_data)
        await engine.initialize_async()

        # Test circuit generation with synchronized tabs
        circuits_data = await engine._generate_from_config()
        assert "test_circuit" in circuits_data["circuits"]["circuits"]


class TestDynamicOverridesEdgeCases:
    """Test dynamic overrides edge cases."""

    @pytest.mark.asyncio
    async def test_dynamic_overrides_with_priority(self):
        """Test dynamic overrides with priority setting."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="test-overrides-priority", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            engine = client._simulation_engine
            assert engine is not None

            # Set circuit-specific overrides including priority
            engine.set_dynamic_overrides(
                {"kitchen_lights": {"power_override": 100.0, "relay_state": "CLOSED", "priority": "NON_ESSENTIAL"}}
            )

            # Get circuits to trigger override application
            circuits = await client.get_circuits()
            assert circuits is not None

    @pytest.mark.asyncio
    async def test_global_power_multiplier_override(self):
        """Test global power multiplier override."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_40_circuit_with_battery.yaml"

        async with SpanPanelClient(
            host="test-global-multiplier", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            engine = client._simulation_engine
            assert engine is not None

            # Set global power multiplier
            engine.set_dynamic_overrides(global_overrides={"power_multiplier": 2.0})

            # Get circuits to trigger override application
            circuits = await client.get_circuits()
            assert circuits is not None
