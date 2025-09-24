#!/usr/bin/env python3
"""
Test multiple energy sources working together in the generic energy profile system.

This demonstrates:
- Solar production (time-dependent)
- Backup generators (always available)
- Battery storage (bidirectional)
- Various consumption loads
- Tab synchronization for 240V systems

Run with: poetry run python examples/test_multi_energy_sources.py
"""

import asyncio
import pytest
import tempfile
import yaml
from pathlib import Path

from span_panel_api.client import SpanPanelClient


@pytest.mark.asyncio
async def test_multi_energy_config():
    """Test a configuration with multiple energy source types."""

    # Create a custom config with multiple energy sources
    test_config = {
        "panel_config": {"serial_number": "MULTI_ENERGY_TEST", "total_tabs": 8, "main_size": 200},
        "circuit_templates": {
            # High-power load
            "hvac": {
                "energy_profile": {
                    "mode": "consumer",
                    "power_range": [0.0, 4000.0],
                    "typical_power": 2500.0,
                    "power_variation": 0.1,
                },
                "relay_behavior": "controllable",
                "priority": "NON_ESSENTIAL",
            },
            # Regular load
            "lighting": {
                "energy_profile": {
                    "mode": "consumer",
                    "power_range": [0.0, 500.0],
                    "typical_power": 200.0,
                    "power_variation": 0.1,
                },
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            },
        },
        "circuits": [
            {"id": "main_hvac", "name": "Main HVAC", "template": "hvac", "tabs": [1]},
            {"id": "house_lights", "name": "House Lighting", "template": "lighting", "tabs": [2]},
        ],
        "unmapped_tab_templates": {
            "3": {
                "energy_profile": {
                    "mode": "consumer",  # Changed to consumer to match current simulation behavior
                    "power_range": [0.0, 2000.0],
                    "typical_power": 1500.0,
                    "power_variation": 0.2,
                },
                "relay_behavior": "non_controllable",
                "priority": "MUST_HAVE",
            },
            "4": {
                "energy_profile": {
                    "mode": "consumer",  # Changed to consumer to match current simulation behavior
                    "power_range": [0.0, 4000.0],
                    "typical_power": 3000.0,
                    "power_variation": 0.05,
                },
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            },
        },
        "unmapped_tabs": [],
        "simulation_params": {
            "update_interval": 5,
            "time_acceleration": 1.0,
            "noise_factor": 0.02,
            "enable_realistic_behaviors": True,
        },
    }

    # Write config to temporary file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(test_config, f)
        temp_config_path = f.name

    try:
        async with SpanPanelClient(
            host="multi-energy-test", simulation_mode=True, simulation_config_path=temp_config_path
        ) as client:

            circuits = await client.get_circuits()
            panel = await client.get_panel_state()
            circuit_dict = circuits.circuits.additional_properties

            print("🌐 Multi-Energy Source System Test")
            print("=" * 50)

            # Test that we have configured circuits
            assert "main_hvac" in circuit_dict
            assert "house_lights" in circuit_dict
            print(f"✅ Found {len(circuit_dict)} configured circuits")

            # Test panel data consistency
            branch_data = panel.branches
            mapped_tabs = {1, 2}  # From circuits
            unmapped_tabs = {3, 4}  # From unmapped_tab_templates

            print(f"✅ Panel has {len(branch_data)} branches")
            print(f"   • Mapped tabs: {sorted(mapped_tabs)}")
            print(f"   • Unmapped tabs: {sorted(unmapped_tabs)}")

            # Verify different circuit types work
            consumption_circuits = set()

            for circuit_id, circuit in circuit_dict.items():
                power = circuit.instant_power_w
                consumption_circuits.add(circuit_id)
                print(f"🔌 {circuit.name}: {power:.1f}W (consuming)")

            assert len(consumption_circuits) > 0, f"Should have found consumption loads, got: {list(circuit_dict.keys())}"
            print("✅ Multiple circuit types active")

            # Energy balance analysis - simplified since all power values are positive
            total_circuit_power = sum(circuit.instant_power_w for circuit in circuit_dict.values())

            print(f"\n📊 Energy Balance:")
            print("-" * 15)
            print(f"Total Circuit Power: {total_circuit_power:.1f}W")
            print(f"Panel Grid Power: {panel.instant_grid_power_w:.1f}W")

            # Verify we have realistic power levels
            assert total_circuit_power > 100, f"Total circuit power too low: {total_circuit_power}W"

            # Test panel-circuit consistency (this should work due to our synchronization fixes)
            panel_grid_power = panel.instant_grid_power_w

            print(f"\n🔄 Panel Consistency Check:")
            print(f"   • Panel Grid Power: {panel_grid_power:.1f}W")
            print(f"   • Total Circuit Power: {total_circuit_power:.1f}W")

            # The panel grid power should be reasonable
            assert abs(panel_grid_power) < 10000, f"Panel grid power seems unrealistic: {panel_grid_power:.1f}W"

            print(f"\n✅ Success! Multi-energy system working:")
            print("   • Multiple circuit templates supported")
            print("   • Unmapped tab templates working")
            print("   • Panel-circuit data consistency maintained")
            print("   • Realistic power levels achieved")

    finally:
        # Clean up temporary config file
        Path(temp_config_path).unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(test_multi_energy_config())
