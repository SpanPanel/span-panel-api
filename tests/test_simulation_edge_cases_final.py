"""Test final simulation edge cases to achieve 100% coverage."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from span_panel_api.simulation import DynamicSimulationEngine


class TestSimulationFinalEdgeCases:
    """Test final edge cases to reach 100% coverage."""

    @pytest.mark.asyncio
    async def test_double_checked_locking_pattern(self):
        """Test the double-checked locking pattern in initialize_async (line 307)."""
        engine = DynamicSimulationEngine()

        # Set base_data to test the early return path (line 307)
        engine._base_data = {"already": "set"}

        # Call initialize_async which should return early due to _base_data being set
        await engine.initialize_async()

        # Should still have the original data
        assert engine._base_data == {"already": "set"}

    @pytest.mark.asyncio
    async def test_config_validation_with_invalid_config_data(self):
        """Test YAML config validation with invalid config structure (line 314)."""
        engine = DynamicSimulationEngine()

        # Set up invalid config data that will trigger validation
        engine._config_data = {"invalid": "structure", "missing": "required_fields"}

        # This should hit the validation error path on line 314
        with pytest.raises(ValueError):
            await engine._load_config_async()

    @pytest.mark.asyncio
    async def test_no_config_path_and_no_config_data_error(self):
        """Test that missing config raises error when no path or data provided."""
        engine = DynamicSimulationEngine()

        # Ensure no config path or data
        engine._config_path = None
        engine._config_data = None

        # This should raise an error
        with pytest.raises(ValueError, match="Simulation mode requires either config_data or a valid config_path"):
            await engine._load_config_async()

    @pytest.mark.asyncio
    async def test_global_override_power_multiplier_application(self):
        """Test global power multiplier override application (line 657)."""
        # Create engine with actual config
        config_path = Path(__file__).parent.parent / "examples" / "behavior_test_config.yaml"
        engine = DynamicSimulationEngine(config_path=config_path)

        # Load the config
        await engine._load_config_async()

        # Set up a global power multiplier override
        engine._global_overrides = {"power_multiplier": 2.0}

        # Create a mock circuit info
        circuit_info = {"instantPowerW": 100.0, "relayState": "CLOSED", "priority": "MUST_HAVE"}

        # Apply dynamic overrides - this should hit line 657
        engine._apply_dynamic_overrides("test_circuit", circuit_info)

        # Power should be multiplied by the global multiplier
        assert circuit_info["instantPowerW"] == 200.0  # 100.0 * 2.0

    @pytest.mark.asyncio
    async def test_global_override_with_circuit_override_combination(self):
        """Test combination of circuit and global overrides to ensure line 657 is hit."""
        # Create engine with actual config
        config_path = Path(__file__).parent.parent / "examples" / "behavior_test_config.yaml"
        engine = DynamicSimulationEngine(config_path=config_path)

        # Load the config
        await engine._load_config_async()

        # Set up both circuit and global overrides
        engine._circuit_overrides = {"test_circuit": {"power_override": 150.0}}
        engine._global_overrides = {"power_multiplier": 1.5}

        # Create a mock circuit info
        circuit_info = {"instantPowerW": 100.0, "relayState": "CLOSED", "priority": "MUST_HAVE"}

        # Apply dynamic overrides - this should hit both circuit and global override paths
        engine._apply_dynamic_overrides("test_circuit", circuit_info)

        # Power should be set to override (150.0) - global multiplier doesn't apply to power_override
        assert circuit_info["instantPowerW"] == 150.0  # power_override takes precedence


# Standalone function to cover the serial_number error path


def test_serial_number_no_config_or_override():
    """Test that accessing serial_number with no config or override raises ValueError."""
    from span_panel_api.simulation import DynamicSimulationEngine

    engine = DynamicSimulationEngine()
    with pytest.raises(ValueError, match="No configuration loaded - serial number not available"):
        _ = engine.serial_number
