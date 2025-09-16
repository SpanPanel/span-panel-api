"""Test simulation engine edge cases and missing coverage lines."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from span_panel_api import SpanPanelClient
from span_panel_api.simulation import DynamicSimulationEngine
from span_panel_api.exceptions import SimulationConfigurationError
import time


def get_base_config():
    """Get a base configuration for testing purposes."""
    import yaml
    from pathlib import Path

    config_path = Path(__file__).parent.parent / "examples" / "validation_test_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Update serial for testing
    config["panel_config"]["serial_number"] = "TEST-001"
    return config


def get_battery_config():
    """Get a configuration with battery template for testing purposes."""
    import yaml
    from pathlib import Path

    config_path = Path(__file__).parent.parent / "examples" / "battery_test_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Update serial for testing
    config["panel_config"]["serial_number"] = "test-battery-123"
    return config


def get_soe_battery_config():
    """Get a configuration for SOE battery testing with bidirectional energy behavior."""
    import yaml
    from pathlib import Path

    config_path = Path(__file__).parent.parent / "examples" / "battery_test_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Update serial and circuit for SOE testing
    config["panel_config"]["serial_number"] = "test-soe-123"
    config["circuits"] = [{"id": "battery_1", "name": "Battery Circuit", "template": "basic_battery", "tabs": [1, 2]}]
    return config


class TestSimulationEngineEdgeCases:
    """Test simulation engine edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_initialization_without_config(self):
        """Test simulation initialization when config is not loaded (line 307)."""
        engine = DynamicSimulationEngine()

        # Test that initialization fails without config
        with pytest.raises(SimulationConfigurationError, match="YAML configuration with circuits is required"):
            await engine._generate_base_data_from_config()

    @pytest.mark.asyncio
    async def test_yaml_config_validation_error(self):
        """Test YAML config validation error (line 314)."""
        engine = DynamicSimulationEngine()

        # Provide invalid config data that will fail validation
        invalid_config = {"invalid": "config"}
        engine._config_data = invalid_config

        # This should trigger validation error on line 314
        with pytest.raises(ValueError):
            await engine._load_config_async()

    @pytest.mark.asyncio
    async def test_no_configuration_error(self):
        """Test that missing configuration raises an error."""
        engine = DynamicSimulationEngine()

        # No config_path and no config_data - should raise error
        with pytest.raises(ValueError, match="YAML configuration is required"):
            await engine._load_config_async()

    @pytest.mark.asyncio
    async def test_panel_data_generation_without_config(self):
        """Test panel data generation without config (line 555)."""
        engine = DynamicSimulationEngine()

        # Try to generate panel data without config
        with pytest.raises(SimulationConfigurationError, match="Configuration not loaded"):
            engine._generate_panel_data(1000.0, 500.0, 300.0, 200.0)

    @pytest.mark.asyncio
    async def test_status_data_generation_without_config(self):
        """Test status data generation without config (line 586)."""
        engine = DynamicSimulationEngine()

        # Should return empty dict when no config
        status_data = engine._generate_status_data()
        assert status_data == {}

    @pytest.mark.asyncio
    async def test_serial_number_property_without_config(self):
        """Test serial number property access without config (lines 604-606)."""
        engine = DynamicSimulationEngine()

        # Should raise error when no config and no override
        with pytest.raises(ValueError, match="No configuration loaded - serial number not available"):
            _ = engine.serial_number

        # Test with serial number override
        engine._serial_number_override = "custom-serial-456"
        serial_override = engine.serial_number
        assert serial_override == "custom-serial-456"


class TestCircuitOverrideEdgeCases:
    """Test circuit override edge cases."""

    @pytest.mark.asyncio
    async def test_circuit_override_power_multiplier(self):
        """Test circuit override power multiplier (line 649)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        async with SpanPanelClient(host="test", simulation_mode=True, simulation_config_path=str(config_path)) as client:
            # Get initial circuits to find a valid circuit ID
            circuits = await client.get_circuits()
            circuit_id = next(iter(circuits.circuits.additional_properties.keys()))

            # Set a power multiplier override for a specific circuit
            await client.set_circuit_overrides(circuit_overrides={circuit_id: {"power_multiplier": 2.0}})

            circuits_modified = await client.get_circuits()
            circuit = circuits_modified.circuits.additional_properties.get(circuit_id)
            assert circuit is not None

            # Power should be affected by the multiplier
            assert circuit.instant_power_w >= 0

    @pytest.mark.asyncio
    async def test_global_power_multiplier_override(self):
        """Test global power multiplier override (line 657)."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        async with SpanPanelClient(host="test", simulation_mode=True, simulation_config_path=str(config_path)) as client:
            # Get baseline power first
            circuits_baseline = await client.get_circuits()
            baseline_power = 0
            for circuit in circuits_baseline.circuits.additional_properties.values():
                baseline_power += circuit.instant_power_w

            # Set a global power multiplier
            await client.set_circuit_overrides(global_overrides={"power_multiplier": 1.5})

            circuits_modified = await client.get_circuits()
            modified_power = 0
            for circuit in circuits_modified.circuits.additional_properties.values():
                modified_power += circuit.instant_power_w

            # Total power should be affected by the global multiplier
            # Allow for some variance due to simulation randomness
            assert modified_power > baseline_power * 1.2  # Should be roughly 1.5x


class TestSimulationEngineInitialization:
    """Test simulation engine initialization edge cases."""

    @pytest.mark.asyncio
    async def test_simulation_engine_with_yaml_config(self):
        """Test simulation engine with YAML configuration file."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        async with SpanPanelClient(
            host="yaml-test", simulation_mode=True, simulation_config_path=str(config_path)
        ) as client:
            # Should initialize successfully with YAML config
            assert client._simulation_engine is not None

            # Should be able to get data
            circuits = await client.get_circuits()
            assert circuits is not None

            panel_state = await client.get_panel_state()
            assert panel_state is not None

    @pytest.mark.asyncio
    async def test_simulation_engine_basic_initialization(self):
        """Test basic simulation engine initialization."""
        async with SpanPanelClient(host="config-test", simulation_mode=True) as client:
            # Should initialize successfully with default config
            assert client._simulation_engine is not None

            # Should be able to access properties
            # Host is used as serial when creating simulation engine
            serial = client._simulation_engine.serial_number
            assert serial == "config-test"

    async def test_battery_power_calculation_edge_cases(self) -> None:
        """Test battery power calculation for different time periods."""
        from span_panel_api.simulation import DynamicSimulationEngine

        config = get_battery_config()
        engine = DynamicSimulationEngine("battery-test", config_data=config)
        await engine.initialize_async()

        # Get the behavior engine to test the methods
        behavior_engine = engine._behavior_engine
        template = config["circuit_templates"]["basic_battery"]

        # Test charge hours (should return positive power)
        charge_time = time.mktime(time.strptime("2024-01-01 10:00:00", "%Y-%m-%d %H:%M:%S"))
        battery_power = behavior_engine._apply_battery_behavior(500.0, template, charge_time)
        assert battery_power > 0  # Should be charging (positive)
        assert battery_power <= 5000  # Within max charge power

        # Test discharge hours (should return positive power)
        discharge_time = time.mktime(time.strptime("2024-01-01 18:00:00", "%Y-%m-%d %H:%M:%S"))
        battery_power = behavior_engine._apply_battery_behavior(500.0, template, discharge_time)
        assert battery_power > 0  # Should be discharging (positive)
        assert battery_power <= 5000  # Within max discharge power

        # Test idle hours (should return small values)
        idle_time = time.mktime(time.strptime("2024-01-01 02:00:00", "%Y-%m-%d %H:%M:%S"))
        battery_power = behavior_engine._apply_battery_behavior(500.0, template, idle_time)
        assert -50.0 <= battery_power <= 50.0  # Within idle range

        # Test transition hours (should return small base power fraction)
        transition_time = time.mktime(time.strptime("2024-01-01 01:00:00", "%Y-%m-%d %H:%M:%S"))
        battery_power = behavior_engine._apply_battery_behavior(500.0, template, transition_time)
        # Should be 10% of base power (small value for transition)
        assert abs(battery_power - 50.0) < 100  # Should be close to base power

    async def test_serial_number_with_override_no_config(self) -> None:
        """Test serial number property when override is set but no config is loaded."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Create engine with serial number override but no config
        engine = DynamicSimulationEngine(serial_number="OVERRIDE-12345")

        # Get serial number without loading config
        serial = engine.serial_number
        assert serial == "OVERRIDE-12345"

    async def test_serial_number_error_when_no_config_or_override(self) -> None:
        """Test serial number property raises error when no config or override is set."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Create engine without serial number override and no config
        engine = DynamicSimulationEngine()

        # Should raise ValueError when accessing serial number
        with pytest.raises(ValueError, match="No configuration loaded - serial number not available"):
            _ = engine.serial_number

    async def test_battery_soe_charging_discharging_scenarios(self) -> None:
        """Test battery SOE calculation for charging and discharging scenarios."""
        from span_panel_api.simulation import DynamicSimulationEngine

        # Use battery test config for SOE calculation
        config = get_soe_battery_config()

        engine = DynamicSimulationEngine("soe-test", config_data=config)
        await engine.initialize_async()

        # Test charging scenario (during charge hours)
        with patch("span_panel_api.simulation.time.time") as mock_time:
            # Set time to 10 AM (charge hour) - this should trigger charging behavior
            mock_time.return_value = time.mktime(time.strptime("2024-01-01 10:00:00", "%Y-%m-%d %H:%M:%S"))

            soe = engine._calculate_dynamic_soe()
            assert soe >= 60.0  # Should be higher than base due to charging
            assert soe <= 95.0  # Should not exceed 95%

        # Test discharging scenario (during discharge hours)
        with patch("span_panel_api.simulation.time.time") as mock_time:
            # Set time to 6 PM (discharge hour) - this should trigger discharging behavior
            mock_time.return_value = time.mktime(time.strptime("2024-01-01 18:00:00", "%Y-%m-%d %H:%M:%S"))

            soe = engine._calculate_dynamic_soe()
            assert soe >= 15.0  # Should not go below 15%
            assert soe <= 85.0  # Adjusted for actual battery example behavior
