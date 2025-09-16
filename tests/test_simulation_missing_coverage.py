"""Tests for missing coverage lines in simulation.py."""

import pytest
import time
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime

from span_panel_api import SpanPanelClient, SimulationConfigurationError
from span_panel_api.simulation import DynamicSimulationEngine, RealisticBehaviorEngine


class TestIdleRangeNegativeValues:
    """Test idle range handling with negative values."""

    def test_idle_range_both_negative(self):
        """Test idle range when both min and max are negative (line 343)."""
        engine = RealisticBehaviorEngine(0, {})

        # Test with both negative values - should swap min/max and convert to positive
        battery_config = {"idle_power_range": [-10.0, -5.0]}
        result = engine._get_idle_power(battery_config)

        # Should be between 5.0 and 10.0 (abs of the swapped values)
        assert 5.0 <= result <= 10.0

    def test_idle_range_min_negative_max_positive(self):
        """Test idle range when min is negative, max is positive."""
        engine = RealisticBehaviorEngine(0, {})

        # Test with min negative, max positive - should use 0 as minimum
        battery_config = {"idle_power_range": [-5.0, 10.0]}
        result = engine._get_idle_power(battery_config)

        # Should be between 0.0 and 10.0
        assert 0.0 <= result <= 10.0

    def test_idle_range_both_positive(self):
        """Test idle range when both values are positive."""
        engine = RealisticBehaviorEngine(0, {})

        # Test with both positive values - should use as-is
        battery_config = {"idle_power_range": [5.0, 15.0]}
        result = engine._get_idle_power(battery_config)

        # Should be between 5.0 and 15.0
        assert 5.0 <= result <= 15.0


class TestTimeProfileHourFactors:
    """Test time profile hour factors handling."""

    def test_hour_factors_in_time_profile(self):
        """Test hour_factors in time profile (line 383)."""
        engine = RealisticBehaviorEngine(0, {})

        # Create a template with time profile containing hour_factors
        template = {"time_of_day_profile": {"enabled": True, "hour_factors": {10: 0.8, 14: 1.2, 18: 0.6}}}

        # Test hour that has a factor (10 AM)
        current_time = datetime(2024, 6, 15, 10, 0, 0).timestamp()
        result = engine._get_solar_production_factor_from_profile(template, current_time)
        assert result == 0.8

        # Test hour that doesn't have a factor (12 PM)
        current_time = datetime(2024, 6, 15, 12, 0, 0).timestamp()
        result = engine._get_solar_production_factor_from_profile(template, current_time)
        assert result == 0.0  # Should return 0.0 for hours not in the list

    def test_production_hours_with_peak_factor(self):
        """Test production_hours with peak_factor (lines 389-390)."""
        engine = RealisticBehaviorEngine(0, {})

        # Create a template with production_hours and peak_factor
        template = {
            "time_of_day_profile": {"enabled": True, "production_hours": [10, 11, 12, 13, 14, 15], "peak_factor": 1.5}
        }

        # Test hour that's in production_hours (12 PM)
        current_time = datetime(2024, 6, 15, 12, 0, 0).timestamp()
        result = engine._get_solar_production_factor_from_profile(template, current_time)
        assert result == 1.5

        # Test hour that's not in production_hours (8 AM)
        current_time = datetime(2024, 6, 15, 8, 0, 0).timestamp()
        result = engine._get_solar_production_factor_from_profile(template, current_time)
        assert result == 0.0

    def test_peak_hours_alternative_name(self):
        """Test peak_hours as alternative name for production_hours."""
        engine = RealisticBehaviorEngine(0, {})

        # Create a template with peak_hours instead of production_hours
        template = {
            "time_of_day_profile": {"enabled": True, "peak_hours": [9, 10, 11, 12, 13, 14, 15, 16], "peak_factor": 1.3}
        }

        # Test hour that's in peak_hours (12 PM)
        current_time = datetime(2024, 6, 15, 12, 0, 0).timestamp()
        result = engine._get_solar_production_factor_from_profile(template, current_time)
        assert result == 1.3

        # Test hour that's not in peak_hours (7 AM)
        current_time = datetime(2024, 6, 15, 7, 0, 0).timestamp()
        result = engine._get_solar_production_factor_from_profile(template, current_time)
        assert result == 0.0


class TestSolarProductionNight:
    """Test solar production at night."""

    def test_solar_production_at_night(self):
        """Test solar production at night (line 422)."""
        engine = RealisticBehaviorEngine(0, {})

        # Test night hours (before 6 AM or after 6 PM)
        night_hours = [0, 1, 2, 3, 4, 5, 19, 20, 21, 22, 23]

        for hour in night_hours:
            current_time = datetime(2024, 6, 15, hour, 0, 0).timestamp()
            result = engine._get_default_solar_factor(current_time)
            assert result == 0.0  # No solar production at night

    def test_solar_production_daytime(self):
        """Test solar production during daytime."""
        engine = RealisticBehaviorEngine(0, {})

        # Test daytime hours (6 AM to 6 PM)
        daytime_hours = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18]

        for hour in daytime_hours:
            current_time = datetime(2024, 6, 15, hour, 0, 0).timestamp()
            result = engine._get_default_solar_factor(current_time)
            assert 0.0 <= result <= 1.0  # Should be between 0 and 1


class TestConfigValidation:
    """Test configuration validation."""

    @pytest.mark.asyncio
    async def test_config_validation_error_path(self):
        """Test config validation error path (line 487)."""
        engine = DynamicSimulationEngine()

        # Try to initialize without config - should raise ValueError
        with pytest.raises(ValueError, match="YAML configuration is required"):
            await engine.initialize_async()


class TestSimulationTimeParsing:
    """Test simulation time parsing."""

    def test_iso_time_parsing_with_z_suffix(self):
        """Test ISO time parsing with Z suffix (line 533)."""
        engine = DynamicSimulationEngine()

        # Test with Z suffix - should remove it and parse as local time
        config_data = {"simulation_params": {"use_simulation_time": True, "simulation_start_time": "2024-06-15T12:00:00Z"}}

        # Mock the _simulation_start_time to avoid initialization issues
        engine._simulation_start_time = time.time()
        engine._config = config_data

        # This should not raise an exception and should remove the Z
        engine._initialize_simulation_time()

        # Verify the offset was calculated
        assert hasattr(engine, '_simulation_time_offset')

    def test_time_parsing_error_handling(self):
        """Test time parsing error handling (lines 540-541)."""
        engine = DynamicSimulationEngine()

        # Test with invalid time format
        config_data = {"simulation_params": {"use_simulation_time": True, "simulation_start_time": "invalid-time-format"}}

        # Mock the _simulation_start_time to avoid initialization issues
        engine._simulation_start_time = time.time()
        engine._config = config_data

        # This should raise SimulationConfigurationError
        with pytest.raises(SimulationConfigurationError, match="Invalid simulation_start_time"):
            engine._initialize_simulation_time()


class TestAdditionalMissingLines:
    """Test additional missing coverage lines."""

    @pytest.mark.asyncio
    async def test_override_simulation_start_time_exception(self):
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

    @pytest.mark.asyncio
    async def test_simulation_time_with_acceleration(self):
        """Test simulation time with acceleration factor."""
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
                "time_acceleration": 3.0,
            },
        }

        engine = DynamicSimulationEngine(config_data=config_data)
        await engine.initialize_async()

        # Test get_current_simulation_time with acceleration
        sim_time = engine.get_current_simulation_time()
        assert isinstance(sim_time, float)
        assert sim_time > 0

    def test_behavior_engine_solar_intensity_config(self):
        """Test solar intensity from config."""
        battery_config = {"solar_intensity_profile": {6: 0.2, 12: 1.0, 18: 0.3}}

        engine = RealisticBehaviorEngine(0, {})

        # Test hours with configured intensity
        assert engine._get_solar_intensity_from_config(6, battery_config) == 0.2
        assert engine._get_solar_intensity_from_config(12, battery_config) == 1.0
        assert engine._get_solar_intensity_from_config(18, battery_config) == 0.3

        # Test hour without configured intensity (should return default)
        assert engine._get_solar_intensity_from_config(0, battery_config) == 0.1

    def test_behavior_engine_demand_factor_config(self):
        """Test demand factor from config."""
        battery_config = {"demand_factor_profile": {8: 0.5, 12: 0.8, 20: 1.2}}

        engine = RealisticBehaviorEngine(0, {})

        # Test hours with configured demand factor
        assert engine._get_demand_factor_from_config(8, battery_config) == 0.5
        assert engine._get_demand_factor_from_config(12, battery_config) == 0.8
        assert engine._get_demand_factor_from_config(20, battery_config) == 1.2

        # Test hour without configured demand factor (should return default 0.3)
        assert engine._get_demand_factor_from_config(0, battery_config) == 0.3
