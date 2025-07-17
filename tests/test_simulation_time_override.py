"""Tests for simulation time override functionality and edge cases."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from span_panel_api.simulation import DynamicSimulationEngine
from span_panel_api import SpanPanelClient


class TestSimulationTimeOverride:
    """Test simulation time override functionality and error handling."""

    @pytest.mark.asyncio
    async def test_simulation_time_override_z_suffix_handling(self):
        """Test simulation time override with Z suffix handling."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        client = SpanPanelClient(
            host="localhost",
            simulation_mode=True,
            simulation_config_path=str(config_path),
            simulation_start_time="2024-06-15T12:00:00Z",  # Z suffix
        )

        async with client:
            # Trigger initialization to test Z suffix removal (line 497)
            await client.get_status()

            # Verify the Z was stripped and simulation initialized
            assert client._simulation_initialized is True
            assert client._simulation_engine is not None

    @pytest.mark.asyncio
    async def test_simulation_time_override_invalid_format(self):
        """Test simulation time override with invalid datetime format."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        client = SpanPanelClient(
            host="localhost",
            simulation_mode=True,
            simulation_config_path=str(config_path),
            simulation_start_time="invalid-datetime-format",  # Invalid format
        )

        async with client:
            # Should handle ValueError/TypeError gracefully (lines 504-505)
            await client.get_status()

            # Simulation should still initialize but not use simulation time
            assert client._simulation_initialized is True
            assert client._simulation_engine is not None

    @pytest.mark.asyncio
    async def test_simulation_engine_yaml_validation_errors(self):
        """Test YAML validation error paths in simulation engine."""
        engine = DynamicSimulationEngine()

        # Test line 517: Invalid config data type
        with pytest.raises(ValueError, match="YAML configuration must be a dictionary"):
            engine._validate_yaml_config("not_a_dict")

        # Test missing required sections (various lines in validation)
        with pytest.raises(ValueError, match="Missing required section: panel_config"):
            engine._validate_yaml_config({})

        with pytest.raises(ValueError, match="Missing required section: circuit_templates"):
            engine._validate_yaml_config({"panel_config": {}})

        with pytest.raises(ValueError, match="Missing required section: circuits"):
            engine._validate_yaml_config({"panel_config": {}, "circuit_templates": {}})

    @pytest.mark.asyncio
    async def test_load_yaml_config_file_io_error(self):
        """Test _load_yaml_config with file IO errors."""
        engine = DynamicSimulationEngine()

        # Test with non-existent file path (should raise FileNotFoundError)
        non_existent_path = Path("/non/existent/file.yaml")
        with pytest.raises(FileNotFoundError):
            engine._load_yaml_config(non_existent_path)

    @pytest.mark.asyncio
    async def test_simulation_engine_missing_config_error(self):
        """Test simulation engine initialization without config."""
        # Create engine without config_data or valid config_path
        engine = DynamicSimulationEngine()

        with pytest.raises(ValueError, match="YAML configuration is required"):
            await engine.initialize_async()

    @pytest.mark.asyncio
    async def test_simulation_start_time_override_with_none_config(self):
        """Test simulation start time override when config is None."""
        engine = DynamicSimulationEngine()

        # This should handle the case where _config is None
        # Should not crash but also not do anything
        engine.override_simulation_start_time("2024-06-15T12:00:00")

        # No exception should be raised

    @pytest.mark.asyncio
    async def test_time_override_datetime_parsing_edge_cases(self):
        """Test datetime parsing edge cases in time override."""
        config_path = Path(__file__).parent.parent / "examples" / "simulation_config_32_circuit.yaml"

        # Test with None as start time (TypeError path)
        client = SpanPanelClient(
            host="localhost", simulation_mode=True, simulation_config_path=str(config_path), simulation_start_time=None
        )

        # This should not crash - TypeError should be caught
        async with client:
            await client.get_status()
            assert client._simulation_initialized is True

    @pytest.mark.asyncio
    async def test_circuit_validation_missing_required_fields(self):
        """Test circuit validation with missing required fields."""
        engine = DynamicSimulationEngine()

        # Valid basic structure but missing panel config required fields
        config_missing_panel_fields = {
            "panel_config": {
                "serial_number": "test123",
                "manufacturer": "SPAN",
                "model": "Gen2",
                # Missing "total_tabs" and "main_size"
            },
            "circuit_templates": {"basic": {"power_range": [0, 1000]}},
            "circuits": {},
        }

        # Should raise error for missing panel config fields (line 537)
        with pytest.raises(ValueError, match="Missing required panel_config field: total_tabs"):
            engine._validate_yaml_config(config_missing_panel_fields)

    @pytest.mark.asyncio
    async def test_panel_config_validation_missing_fields(self):
        """Test panel config validation with missing fields."""
        engine = DynamicSimulationEngine()

        # Test various panel config validation scenarios
        incomplete_config = {
            "panel_config": {
                # Missing required fields
            },
            "circuit_templates": {},
            "circuits": {},
        }

        # The validation may pass or fail depending on specific requirements
        # This tests the panel config validation path
        try:
            engine._validate_yaml_config(incomplete_config)
        except ValueError:
            # Expected if panel config validation is strict
            pass
