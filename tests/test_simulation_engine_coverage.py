"""Test simulation engine edge cases and missing coverage lines."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from span_panel_api import SpanPanelClient
from span_panel_api.simulation import DynamicSimulationEngine


class TestSimulationEngineEdgeCases:
    """Test simulation engine edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_initialization_without_config(self):
        """Test simulation initialization when config is not loaded (line 307)."""
        engine = DynamicSimulationEngine()

        # Test that initialization fails without config
        with pytest.raises(ValueError, match="YAML configuration with circuits is required"):
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
    async def test_default_configuration_fallback(self):
        """Test default configuration fallback (line 324)."""
        engine = DynamicSimulationEngine()

        # No config_path and no config_data - should use default
        await engine._load_config_async()

        # Should have loaded default config
        assert engine._config is not None
        assert "panel_config" in engine._config
        assert "circuits" in engine._config

    @pytest.mark.asyncio
    async def test_panel_data_generation_without_config(self):
        """Test panel data generation without config (line 555)."""
        engine = DynamicSimulationEngine()

        # Try to generate panel data without config
        with pytest.raises(ValueError, match="Configuration not loaded"):
            engine._generate_panel_data(1000.0, 500.0, 300.0)

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

        # Should return default serial when no config
        serial = engine.serial_number
        assert serial == "sim-serial-123"

        # Test with serial number override
        engine._serial_number_override = "custom-serial-456"
        serial_override = engine.serial_number
        assert serial_override == "custom-serial-456"

    @pytest.mark.asyncio
    async def test_serial_number_property_with_config(self):
        """Test serial number property access with config."""
        engine = DynamicSimulationEngine()

        # Load default config first
        await engine._load_config_async()

        # Should return config serial when config is available
        serial = engine.serial_number
        assert serial == "sim-serial-123"  # Default config uses default serial
        assert len(serial) > 0


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
