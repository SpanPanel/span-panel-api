"""Tests for electrical phase validation of all example configurations.

This test suite validates that all YAML example configurations follow proper
electrical phase relationships for 240V circuits and tab synchronizations.
"""

import pytest
import yaml
from pathlib import Path
from typing import Any, Dict, List, Tuple

from span_panel_api.phase_validation import (
    get_tab_phase,
    are_tabs_opposite_phase,
    validate_solar_tabs,
    get_phase_distribution,
)
from span_panel_api.simulation import DynamicSimulationEngine


class TestExamplePhaseValidation:
    """Test electrical phase validation for all example configurations."""

    @pytest.fixture
    def example_configs(self) -> Dict[str, Dict[str, Any]]:
        """Load all example YAML configurations."""
        examples_dir = Path(__file__).parent.parent / "examples"
        configs = {}

        # Load all YAML files in examples directory
        for yaml_file in examples_dir.glob("*.yaml"):
            if yaml_file.name.startswith("test_") or yaml_file.name == "README.md":
                continue

            with open(yaml_file) as f:
                try:
                    config = yaml.safe_load(f)
                    configs[yaml_file.name] = config
                except yaml.YAMLError as e:
                    pytest.fail(f"Failed to load {yaml_file.name}: {e}")

        return configs

    def test_all_examples_load_successfully(self, example_configs):
        """Test that all example YAML files load without errors."""
        assert len(example_configs) > 0, "No example configurations found"

        for config_name, config in example_configs.items():
            assert isinstance(config, dict), f"{config_name} did not load as dictionary"
            assert "panel_config" in config, f"{config_name} missing panel_config"
            assert "circuits" in config, f"{config_name} missing circuits"

    def test_yaml_structure_validation(self, example_configs):
        """Test that all examples pass basic YAML structure validation."""
        engine = DynamicSimulationEngine()

        for config_name, config in example_configs.items():
            try:
                engine._validate_yaml_config(config)
            except Exception as e:
                pytest.fail(f"YAML validation failed for {config_name}: {e}")

    def test_240v_circuit_phase_validation(self, example_configs):
        """Test that all 240V circuits use opposite phases (L1 + L2)."""
        validation_results = {}

        for config_name, config in example_configs.items():
            results = []
            total_tabs = config["panel_config"]["total_tabs"]
            valid_tabs = list(range(1, total_tabs + 1))

            # Check circuits with multiple tabs (240V circuits)
            for circuit in config.get("circuits", []):
                tabs = circuit.get("tabs", [])
                if len(tabs) == 2:
                    tab1, tab2 = tabs[0], tabs[1]
                    is_valid, message = validate_solar_tabs(tab1, tab2, valid_tabs)
                    results.append(
                        {
                            "circuit_id": circuit.get("id"),
                            "circuit_name": circuit.get("name"),
                            "tabs": tabs,
                            "is_valid": is_valid,
                            "message": message,
                        }
                    )

            validation_results[config_name] = results

            # Assert all 240V circuits are valid
            invalid_circuits = [r for r in results if not r["is_valid"]]
            if invalid_circuits:
                error_messages = []
                for circuit in invalid_circuits:
                    error_messages.append(f"  • {circuit['circuit_name']} (tabs {circuit['tabs']}): {circuit['message']}")
                pytest.fail(f"Invalid 240V circuit phase configuration in {config_name}:\n" + "\n".join(error_messages))

    def test_tab_synchronization_phase_validation(self, example_configs):
        """Test that tab synchronizations use opposite phases for 240V systems."""
        validation_results = {}

        for config_name, config in example_configs.items():
            results = []
            total_tabs = config["panel_config"]["total_tabs"]
            valid_tabs = list(range(1, total_tabs + 1))

            # Check tab synchronizations
            tab_syncs = config.get("tab_synchronizations", [])
            for sync in tab_syncs:
                tabs = sync.get("tabs", [])
                behavior = sync.get("behavior", "")

                if len(tabs) == 2 and "240v" in behavior.lower():
                    tab1, tab2 = tabs[0], tabs[1]
                    is_valid, message = validate_solar_tabs(tab1, tab2, valid_tabs)
                    results.append(
                        {
                            "sync_tabs": tabs,
                            "behavior": behavior,
                            "template": sync.get("template"),
                            "is_valid": is_valid,
                            "message": message,
                        }
                    )

            validation_results[config_name] = results

            # Assert all 240V synchronizations are valid
            invalid_syncs = [r for r in results if not r["is_valid"]]
            if invalid_syncs:
                error_messages = []
                for sync in invalid_syncs:
                    error_messages.append(f"  • Sync tabs {sync['sync_tabs']} ({sync['behavior']}): {sync['message']}")
                pytest.fail(
                    f"Invalid tab synchronization phase configuration in {config_name}:\n" + "\n".join(error_messages)
                )

    def test_phase_distribution_balance(self, example_configs):
        """Test that panel configurations have reasonable phase distribution."""
        for config_name, config in example_configs.items():
            total_tabs = config["panel_config"]["total_tabs"]
            valid_tabs = list(range(1, total_tabs + 1))

            # Get all tabs used by circuits
            used_tabs = []
            for circuit in config.get("circuits", []):
                used_tabs.extend(circuit.get("tabs", []))

            # Add tabs from synchronizations
            for sync in config.get("tab_synchronizations", []):
                used_tabs.extend(sync.get("tabs", []))

            # Add unmapped tabs
            used_tabs.extend(config.get("unmapped_tabs", []))

            if used_tabs:
                distribution = get_phase_distribution(used_tabs, valid_tabs)

                # Check for severe imbalance (more than 3 tab difference)
                if distribution["balance_difference"] > 3:
                    pytest.fail(
                        f"Severe phase imbalance in {config_name}: "
                        f"L1={distribution['L1_count']} tabs, L2={distribution['L2_count']} tabs "
                        f"(difference: {distribution['balance_difference']})"
                    )

    def test_battery_circuit_configurations(self, example_configs):
        """Test that battery circuits follow proper electrical configuration."""
        for config_name, config in example_configs.items():
            total_tabs = config["panel_config"]["total_tabs"]
            valid_tabs = list(range(1, total_tabs + 1))

            battery_circuits = []

            # Find battery circuits
            for circuit in config.get("circuits", []):
                template = circuit.get("template", "")
                if template == "battery":
                    battery_circuits.append(circuit)

            # Validate battery circuit tab configurations
            for circuit in battery_circuits:
                tabs = circuit.get("tabs", [])
                circuit_name = circuit.get("name", circuit.get("id"))

                if len(tabs) == 1:
                    # Single-tab battery (120V) - valid but note it
                    continue
                elif len(tabs) == 2:
                    # 240V battery - must be opposite phases
                    tab1, tab2 = tabs[0], tabs[1]
                    is_valid, message = validate_solar_tabs(tab1, tab2, valid_tabs)
                    if not is_valid:
                        pytest.fail(
                            f"Invalid battery circuit configuration in {config_name}: "
                            f"{circuit_name} (tabs {tabs}): {message}"
                        )
                else:
                    pytest.fail(
                        f"Invalid battery circuit in {config_name}: {circuit_name} has {len(tabs)} tabs (expected 1 or 2)"
                    )

    def test_solar_inverter_configurations(self, example_configs):
        """Test that solar inverter circuits follow proper electrical configuration."""
        for config_name, config in example_configs.items():
            total_tabs = config["panel_config"]["total_tabs"]
            valid_tabs = list(range(1, total_tabs + 1))

            solar_circuits = []

            # Find solar circuits
            for circuit in config.get("circuits", []):
                template = circuit.get("template", "")
                circuit_name = circuit.get("name", "").lower()
                if "solar" in template or "solar" in circuit_name:
                    solar_circuits.append(circuit)

            # Validate solar circuit tab configurations
            for circuit in solar_circuits:
                tabs = circuit.get("tabs", [])
                circuit_name = circuit.get("name", circuit.get("id"))

                if len(tabs) == 2:
                    # 240V solar inverter - must be opposite phases
                    tab1, tab2 = tabs[0], tabs[1]
                    is_valid, message = validate_solar_tabs(tab1, tab2, valid_tabs)
                    if not is_valid:
                        pytest.fail(
                            f"Invalid solar inverter configuration in {config_name}: {circuit_name} (tabs {tabs}): {message}"
                        )

    def test_generator_configurations(self, example_configs):
        """Test that generator circuits follow proper electrical configuration."""
        for config_name, config in example_configs.items():
            total_tabs = config["panel_config"]["total_tabs"]
            valid_tabs = list(range(1, total_tabs + 1))

            generator_circuits = []

            # Find generator circuits
            for circuit in config.get("circuits", []):
                template = circuit.get("template", "")
                circuit_name = circuit.get("name", "").lower()
                if "generator" in template or "generator" in circuit_name:
                    generator_circuits.append(circuit)

            # Validate generator circuit tab configurations
            for circuit in generator_circuits:
                tabs = circuit.get("tabs", [])
                circuit_name = circuit.get("name", circuit.get("id"))

                if len(tabs) == 2:
                    # 240V generator - must be opposite phases
                    tab1, tab2 = tabs[0], tabs[1]
                    is_valid, message = validate_solar_tabs(tab1, tab2, valid_tabs)
                    if not is_valid:
                        pytest.fail(
                            f"Invalid generator configuration in {config_name}: {circuit_name} (tabs {tabs}): {message}"
                        )

    def test_high_power_appliance_configurations(self, example_configs):
        """Test that high-power appliances (>3kW) use 240V configuration."""
        for config_name, config in example_configs.items():
            total_tabs = config["panel_config"]["total_tabs"]
            valid_tabs = list(range(1, total_tabs + 1))

            for circuit in config.get("circuits", []):
                tabs = circuit.get("tabs", [])
                circuit_name = circuit.get("name", circuit.get("id"))
                overrides = circuit.get("overrides", {})

                # Check for high power ranges in overrides
                power_range = overrides.get("power_range", [0, 0])
                typical_power = overrides.get("typical_power", 0)

                max_power = max(abs(power_range[0]), abs(power_range[1]), abs(typical_power))

                # High power appliances (>3kW) should use 240V (2 tabs)
                if max_power > 3000 and len(tabs) == 2:
                    tab1, tab2 = tabs[0], tabs[1]
                    is_valid, message = validate_solar_tabs(tab1, tab2, valid_tabs)
                    if not is_valid:
                        pytest.fail(
                            f"Invalid high-power appliance configuration in {config_name}: "
                            f"{circuit_name} ({max_power}W, tabs {tabs}): {message}"
                        )

    def test_comprehensive_phase_report(self, example_configs):
        """Generate comprehensive phase validation report for all examples."""
        print("\n" + "=" * 80)
        print("COMPREHENSIVE ELECTRICAL PHASE VALIDATION REPORT")
        print("=" * 80)

        for config_name, config in example_configs.items():
            print(f"\n📋 {config_name}")
            print("-" * 60)

            total_tabs = config["panel_config"]["total_tabs"]
            valid_tabs = list(range(1, total_tabs + 1))

            # Circuit analysis
            print("🔌 Circuit Analysis:")
            for circuit in config.get("circuits", []):
                tabs = circuit.get("tabs", [])
                name = circuit.get("name", circuit.get("id"))
                template = circuit.get("template", "")

                if len(tabs) == 1:
                    phase = get_tab_phase(tabs[0], valid_tabs)
                    print(f"  • {name}: Tab {tabs[0]} ({phase}) - 120V")
                elif len(tabs) == 2:
                    phase1 = get_tab_phase(tabs[0], valid_tabs)
                    phase2 = get_tab_phase(tabs[1], valid_tabs)
                    valid = phase1 != phase2
                    status = "✓" if valid else "❌"
                    print(f"  • {name}: Tabs {tabs[0]}({phase1}) + {tabs[1]}({phase2}) - 240V {status}")

            # Synchronization analysis
            tab_syncs = config.get("tab_synchronizations", [])
            if tab_syncs:
                print("\n🔄 Tab Synchronizations:")
                for sync in tab_syncs:
                    tabs = sync.get("tabs", [])
                    behavior = sync.get("behavior", "")
                    template = sync.get("template", "")

                    if len(tabs) == 2:
                        phase1 = get_tab_phase(tabs[0], valid_tabs)
                        phase2 = get_tab_phase(tabs[1], valid_tabs)
                        valid = phase1 != phase2
                        status = "✓" if valid else "❌"
                        print(f"  • {template}: Tabs {tabs[0]}({phase1}) + {tabs[1]}({phase2}) - {behavior} {status}")

            # Phase distribution
            all_tabs = []
            for circuit in config.get("circuits", []):
                all_tabs.extend(circuit.get("tabs", []))
            for sync in config.get("tab_synchronizations", []):
                all_tabs.extend(sync.get("tabs", []))
            all_tabs.extend(config.get("unmapped_tabs", []))

            if all_tabs:
                distribution = get_phase_distribution(all_tabs, valid_tabs)
                balance_status = (
                    "✓ Balanced" if distribution["is_balanced"] else f"⚠️ Imbalanced (±{distribution['balance_difference']})"
                )
                print(
                    f"\n⚖️ Phase Distribution: L1={distribution['L1_count']} tabs, L2={distribution['L2_count']} tabs ({balance_status})"
                )

        print("\n" + "=" * 80)
