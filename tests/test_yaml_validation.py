"""Tests for YAML configuration validation in simulation mode."""

import pytest
import tempfile
from pathlib import Path
from span_panel_api.simulation import DynamicSimulationEngine


class TestYAMLValidation:
    """Test YAML configuration validation."""

    def test_valid_yaml_config_passes_validation(self):
        """Test that valid YAML configuration passes validation."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "examples" / "validation_test_config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        engine = DynamicSimulationEngine()
        # Should not raise any exceptions
        engine._validate_yaml_config(config)

    def test_missing_panel_config_raises_error(self):
        """Test that missing panel_config raises validation error."""
        config = {
            "circuit_templates": {},
            "circuits": [],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Missing required section: panel_config"):
            engine._validate_yaml_config(config)

    def test_missing_circuit_templates_raises_error(self):
        """Test that missing circuit_templates raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuits": [],
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Missing required section: circuit_templates"):
            engine._validate_yaml_config(config)

    def test_missing_circuits_raises_error(self):
        """Test that missing circuits raises validation error."""
        config = {
            "panel_config": {"serial_number": "TEST", "total_tabs": 8, "main_size": 100},
            "circuit_templates": {},
        }

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Missing required section: circuits"):
            engine._validate_yaml_config(config)

    def test_invalid_circuits_type_raises_error(self):
        """Test that invalid circuits type raises validation error."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "examples" / "validation_test_config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Modify to create invalid condition
        config["circuits"] = "not_a_list"  # Should be a list

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="circuits must be a list"):
            engine._validate_yaml_config(config)

    def test_empty_circuits_raises_error(self):
        """Test that empty circuits list raises validation error."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "examples" / "validation_test_config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Modify to create invalid condition
        config["circuits"] = []  # Empty list

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="At least one circuit must be defined"):
            engine._validate_yaml_config(config)

    def test_invalid_circuit_structure_raises_error(self):
        """Test that invalid circuit structure raises validation error."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "examples" / "validation_test_config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Modify to create invalid condition
        config["circuits"] = ["not_a_dict"]  # Should be a dictionary

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Circuit 0 must be a dictionary"):
            engine._validate_yaml_config(config)

    def test_missing_circuit_fields_raises_error(self):
        """Test that missing required circuit fields raises validation error."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "examples" / "validation_test_config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Modify to create invalid condition
        config["circuits"] = [
            {
                "id": "test_circuit",
                "name": "Test Circuit",
                # Missing template and tabs
            }
        ]

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Missing required field 'template' in circuit 0"):
            engine._validate_yaml_config(config)

    def test_unknown_template_reference_raises_error(self):
        """Test that unknown template reference raises validation error."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "examples" / "validation_test_config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Modify to create invalid condition
        config["circuits"][0]["template"] = "nonexistent_template"  # References unknown template

        engine = DynamicSimulationEngine()
        with pytest.raises(ValueError, match="Circuit 0 references unknown template 'nonexistent_template'"):
            engine._validate_yaml_config(config)

    async def test_yaml_file_validation_on_load(self):
        """Test that YAML file validation occurs during async loading."""
        invalid_yaml_content = """
panel_config:
  serial_number: "TEST"
  total_tabs: 8
  main_size: 100
circuit_templates:
  test:
    energy_profile:
      mode: "consumer"
      power_range: [0.0, 1000.0]
      typical_power: 500.0
      power_variation: 0.1
    relay_behavior: "controllable"
    priority: "NON_ESSENTIAL"
circuits: "not_a_list"  # Invalid type
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(invalid_yaml_content)
            temp_path = f.name

        try:
            engine = DynamicSimulationEngine(config_path=Path(temp_path))
            with pytest.raises(ValueError, match="circuits must be a list"):
                await engine.initialize_async()
        finally:
            Path(temp_path).unlink()
