"""Tests for YAML configuration validation."""

import pytest
from pathlib import Path
import tempfile
import yaml

from src.span_panel_api.simulation import DynamicSimulationEngine


class TestYAMLValidation:
    """Test YAML configuration validation functionality."""

    def test_valid_yaml_config_passes_validation(self):
        """Test that a valid YAML configuration passes validation."""
        valid_config = {
            "panel_config": {"serial_number": "TEST-001", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {
                "test_template": {
                    "power_range": [10.0, 100.0],
                    "energy_behavior": "consume_only",
                    "typical_power": 50.0,
                    "power_variation": 0.1,
                    "relay_behavior": "controllable",
                    "priority": "NON_ESSENTIAL",
                }
            },
            "circuits": [{"id": "test_circuit", "name": "Test Circuit", "template": "test_template", "tabs": [1, 2]}],
            "unmapped_tabs": [3, 4],
            "simulation_params": {
                "update_interval": 1,
                "time_acceleration": 1.0,
                "noise_factor": 0.05,
                "enable_realistic_behaviors": True,
            },
        }

        engine = DynamicSimulationEngine(config_data=valid_config)
        # Should not raise any validation errors
        engine._validate_yaml_config(valid_config)

    def test_missing_required_section_raises_error(self):
        """Test that missing required sections raise validation errors."""
        incomplete_configs = [
            # Missing panel_config
            {"circuit_templates": {}, "circuits": []},
            # Missing circuit_templates
            {"panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100}, "circuits": []},
            # Missing circuits
            {"panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100}, "circuit_templates": {}},
        ]

        for config in incomplete_configs:
            engine = DynamicSimulationEngine()
            with pytest.raises(ValueError, match="Missing required section"):
                engine._validate_yaml_config(config)

    def test_invalid_config_type_raises_error(self):
        """Test that non-dictionary config raises validation error."""
        engine = DynamicSimulationEngine()

        with pytest.raises(ValueError, match="YAML configuration must be a dictionary"):
            engine._validate_yaml_config("not a dict")  # type: ignore

        with pytest.raises(ValueError, match="YAML configuration must be a dictionary"):
            engine._validate_yaml_config([])  # type: ignore

    def test_missing_panel_config_fields_raises_error(self):
        """Test that missing panel_config fields raise validation errors."""
        base_config = {
            "panel_config": {},  # Missing required fields
            "circuit_templates": {
                "test": {
                    "power_range": [1, 2],
                    "energy_behavior": "consume_only",
                    "typical_power": 1.0,
                    "power_variation": 0.1,
                    "relay_behavior": "controllable",
                    "priority": "NON_ESSENTIAL",
                }
            },
            "circuits": [{"id": "test", "name": "Test", "template": "test", "tabs": [1]}],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Missing required panel_config field"):
            engine._validate_yaml_config(base_config)

    def test_invalid_panel_config_type_raises_error(self):
        """Test that invalid panel_config type raises validation error."""
        config = {"panel_config": "not a dict", "circuit_templates": {}, "circuits": []}

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="panel_config must be a dictionary"):
            engine._validate_yaml_config(config)

    def test_empty_circuit_templates_raises_error(self):
        """Test that empty circuit_templates raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {},  # Empty templates
            "circuits": [],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="At least one circuit template must be defined"):
            engine._validate_yaml_config(config)

    def test_invalid_circuit_templates_type_raises_error(self):
        """Test that invalid circuit_templates type raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": "not a dict",
            "circuits": [],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="circuit_templates must be a dictionary"):
            engine._validate_yaml_config(config)

    def test_invalid_circuit_template_structure_raises_error(self):
        """Test that invalid circuit template structure raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {"test_template": "not a dict"},  # Should be dictionary
            "circuits": [],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Circuit template 'test_template' must be a dictionary"):
            engine._validate_yaml_config(config)

    def test_missing_circuit_template_fields_raises_error(self):
        """Test that missing circuit template fields raise validation errors."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {
                "incomplete_template": {
                    "power_range": [1, 2]
                    # Missing other required fields
                }
            },
            "circuits": [],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Missing required field .* in circuit template 'incomplete_template'"):
            engine._validate_yaml_config(config)

    def test_invalid_circuits_type_raises_error(self):
        """Test that invalid circuits type raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {
                "test": {
                    "power_range": [1, 2],
                    "energy_behavior": "consume_only",
                    "typical_power": 1.0,
                    "power_variation": 0.1,
                    "relay_behavior": "controllable",
                    "priority": "NON_ESSENTIAL",
                }
            },
            "circuits": "not a list",
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="circuits must be a list"):
            engine._validate_yaml_config(config)

    def test_empty_circuits_raises_error(self):
        """Test that empty circuits list raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {
                "test": {
                    "power_range": [1, 2],
                    "energy_behavior": "consume_only",
                    "typical_power": 1.0,
                    "power_variation": 0.1,
                    "relay_behavior": "controllable",
                    "priority": "NON_ESSENTIAL",
                }
            },
            "circuits": [],  # Empty circuits
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="At least one circuit must be defined"):
            engine._validate_yaml_config(config)

    def test_invalid_circuit_structure_raises_error(self):
        """Test that invalid circuit structure raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {
                "test": {
                    "power_range": [1, 2],
                    "energy_behavior": "consume_only",
                    "typical_power": 1.0,
                    "power_variation": 0.1,
                    "relay_behavior": "controllable",
                    "priority": "NON_ESSENTIAL",
                }
            },
            "circuits": ["not a dict"],  # Should be dictionary
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Circuit 0 must be a dictionary"):
            engine._validate_yaml_config(config)

    def test_missing_circuit_fields_raises_error(self):
        """Test that missing circuit fields raise validation errors."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {
                "test": {
                    "power_range": [1, 2],
                    "energy_behavior": "consume_only",
                    "typical_power": 1.0,
                    "power_variation": 0.1,
                    "relay_behavior": "controllable",
                    "priority": "NON_ESSENTIAL",
                }
            },
            "circuits": [
                {
                    "id": "test_circuit"
                    # Missing other required fields
                }
            ],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Missing required field .* in circuit 0"):
            engine._validate_yaml_config(config)

    def test_unknown_template_reference_raises_error(self):
        """Test that unknown template reference raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {
                "test": {
                    "power_range": [1, 2],
                    "energy_behavior": "consume_only",
                    "typical_power": 1.0,
                    "power_variation": 0.1,
                    "relay_behavior": "controllable",
                    "priority": "NON_ESSENTIAL",
                }
            },
            "circuits": [
                {
                    "id": "test_circuit",
                    "name": "Test Circuit",
                    "template": "nonexistent_template",  # References unknown template
                    "tabs": [1, 2],
                }
            ],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Circuit 0 references unknown template 'nonexistent_template'"):
            engine._validate_yaml_config(config)

    async def test_yaml_file_validation_on_load(self):
        """Test that YAML file validation occurs during async loading."""
        invalid_yaml_content = """
panel_config:
  # Missing required fields
circuit_templates: {}
circuits: []
"""

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(invalid_yaml_content)
            temp_path = f.name

        try:
            engine = DynamicSimulationEngine(config_path=temp_path)
            with pytest.raises(ValueError, match="panel_config must be a dictionary"):
                await engine.initialize_async()
        finally:
            Path(temp_path).unlink()

    async def test_config_data_validation_on_load(self):
        """Test that config_data validation occurs during async loading."""
        invalid_config = {"panel_config": {}, "circuit_templates": {}, "circuits": []}  # Missing required fields

        engine = DynamicSimulationEngine(config_data=invalid_config)
        with pytest.raises(ValueError, match="Missing required panel_config field"):
            await engine.initialize_async()
