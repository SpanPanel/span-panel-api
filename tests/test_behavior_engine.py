"""Tests for behavior engine edge cases and time-of-day patterns.

This module tests specific edge cases in the RealisticBehaviorEngine
to achieve test coverage.
"""

import time
from pathlib import Path

import pytest

from span_panel_api.simulation import DynamicSimulationEngine, RealisticBehaviorEngine


class TestBehaviorEngineEdgeCases:
    """Test edge cases in the RealisticBehaviorEngine."""

    def test_behavior_engine_direct_solar_pattern(self):
        """Test the behavior engine solar pattern directly to cover specific lines."""
        config_path = Path(__file__).parent / "fixtures" / "configs" / "behavior_test_config.yaml"
        current_time = time.time()

        # Load config to get the solar template
        import yaml

        with config_path.open() as f:
            config = yaml.safe_load(f)

        engine = RealisticBehaviorEngine(current_time, config)

        # Get solar template from example config
        solar_template = config["circuit_templates"]["variable_solar"]

        # Test nighttime for solar (covers line 215 - should return 0.0)
        from datetime import datetime

        # Create a proper 2 AM timestamp in current timezone
        now = datetime.now()
        night_time = now.replace(hour=2, minute=0, second=0, microsecond=0)
        night_timestamp = night_time.timestamp()
        night_modulated = engine._apply_time_of_day_modulation(-1000.0, solar_template, night_timestamp)
        assert abs(night_modulated) < 1e-10, f"Solar should produce ~0 power at night (got {night_modulated})"

        # Test daytime for solar (covers lines 213-214 - sine curve calculation)
        # Create a proper 10 AM timestamp in current timezone
        day_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
        day_timestamp = day_time.timestamp()
        day_modulated = engine._apply_time_of_day_modulation(-1000.0, solar_template, day_timestamp)
        # Should be positive and use time-of-day profile (peak factor = 1.0)
        assert day_modulated > 0, "Solar should produce positive power during day"
        # With peak_hours configuration, solar should produce full power during peak hours
        tolerance = 1e-10
        assert (
            abs(day_modulated - 1000.0) <= tolerance
        ), f"Solar power should be full power during peak hours, got {day_modulated}W"

    def test_behavior_engine_time_of_day_modulation(self):
        """Test time-of-day modulation for regular circuits to cover specific lines."""
        config_path = Path(__file__).parent / "fixtures" / "configs" / "behavior_test_config.yaml"
        current_time = time.time()

        # Load config
        import yaml

        with config_path.open() as f:
            config = yaml.safe_load(f)

        engine = RealisticBehaviorEngine(current_time, config)

        # Create a template with specific peak hours to test exact behavior
        test_template = {
            "energy_profile": {
                "mode": "consumer",
                "power_range": [0.0, 1000.0],
                "typical_power": 100.0,
                "power_variation": 0.2,
            },
            "time_of_day_profile": {"enabled": True, "peak_hours": [20]},  # 8 PM is peak
        }

        # Test peak hours (covers line 218 - should be 1.3x)
        from datetime import datetime

        # Create a proper 8 PM timestamp in current timezone
        now = datetime.now()
        peak_time = now.replace(hour=20, minute=0, second=0, microsecond=0)
        peak_timestamp = peak_time.timestamp()
        peak_modulated = engine._apply_time_of_day_modulation(100.0, test_template, peak_timestamp)
        expected_peak = 100.0 * 1.3
        tolerance = 1e-10
        assert abs(peak_modulated - expected_peak) <= tolerance, f"Peak power should be {expected_peak}W (line 218)"

        # Test overnight hours (covers line 221 - should be 0.3x)
        # Create a proper 2 AM timestamp in current timezone
        night_time = now.replace(hour=2, minute=0, second=0, microsecond=0)
        night_timestamp = night_time.timestamp()
        night_modulated = engine._apply_time_of_day_modulation(100.0, test_template, night_timestamp)
        expected_night = 100.0 * 0.3
        assert abs(night_modulated - expected_night) <= tolerance, f"Night power should be {expected_night}W (line 221)"

        # Test normal hours (covers line 224 - should be 1.0x)
        normal_timestamp = 15 * 3600  # 3 PM
        normal_modulated = engine._apply_time_of_day_modulation(100.0, test_template, normal_timestamp)
        assert abs(normal_modulated - 100.0) <= tolerance, "Normal hours should return base power (line 224)"

    @pytest.mark.asyncio
    async def test_simulation_engine_error_coverage(self):
        """Test error conditions in simulation engine."""
        from span_panel_api.exceptions import SimulationConfigurationError

        # Test missing config path
        engine = DynamicSimulationEngine("TEST")
        with pytest.raises(SimulationConfigurationError, match="Configuration not loaded"):
            await engine._generate_from_config()

        # Test missing circuit templates
        config_path = Path(__file__).parent / "fixtures" / "configs" / "simple_test_config.yaml"
        engine_with_path = DynamicSimulationEngine("TEST", config_path=config_path)
        await engine_with_path.initialize_async()

        # This should work normally
        panel_data = await engine_with_path.get_panel_data()
        assert "circuits" in panel_data
        assert "panel" in panel_data

    def test_behavior_engine_cycling_behavior_coverage(self):
        """Test cycling behavior edge cases."""
        config_path = Path(__file__).parent / "fixtures" / "configs" / "behavior_test_config.yaml"
        current_time = time.time()

        import yaml

        with config_path.open() as f:
            config = yaml.safe_load(f)

        engine = RealisticBehaviorEngine(current_time, config)
        hvac_template = config["circuit_templates"]["cycling_hvac"]

        # Test cycling behavior
        power1 = engine.get_circuit_power("test_hvac", hvac_template, current_time)
        power2 = engine.get_circuit_power("test_hvac", hvac_template, current_time + 1)

        # Both should be valid power values
        assert power1 >= 0
        assert power2 >= 0

    def test_relay_state_open_coverage(self):
        """Test that OPEN relay state returns 0.0 power - covers line 170."""
        config_path = Path(__file__).parent / "fixtures" / "configs" / "behavior_test_config.yaml"
        current_time = time.time()

        import yaml

        with config_path.open() as f:
            config = yaml.safe_load(f)

        engine = RealisticBehaviorEngine(current_time, config)
        template = {
            "energy_profile": {
                "mode": "consumer",
                "power_range": [0.0, 2000.0],
                "typical_power": 1000.0,
                "power_variation": 0.1,
            },
        }

        # Test OPEN relay state (covers line 170)
        tolerance = 1e-10
        power_open = engine.get_circuit_power("test_circuit", template, current_time, relay_state="OPEN")
        assert abs(power_open - 0.0) <= tolerance, "OPEN relay should return 0.0 power (line 170)"

        # Test CLOSED relay state (normal operation)
        power_closed = engine.get_circuit_power("test_circuit", template, current_time, relay_state="CLOSED")
        assert power_closed != 0.0, "CLOSED relay should return non-zero power"

    @pytest.mark.asyncio
    async def test_simulation_engine_initialization_edge_cases(self):
        """Test simulation engine initialization edge cases."""
        # Test engine with config path
        config_path = Path(__file__).parent / "fixtures" / "configs" / "simple_test_config.yaml"
        engine_with_config = DynamicSimulationEngine("TEST-WITH-CONFIG", config_path=config_path)
        await engine_with_config.initialize_async()
        assert engine_with_config._config is not None

        # Test that config is loaded properly
        panel_data = await engine_with_config.get_panel_data()
        assert "circuits" in panel_data
        assert "panel" in panel_data
