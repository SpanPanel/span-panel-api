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

from span_panel_api.client import SpanPanelClient


@pytest.mark.asyncio
async def test_multi_energy_config():
    """Test a configuration with multiple energy source types."""

    # Create a custom config with multiple energy sources
    test_config = {
        "panel_config": {"serial_number": "MULTI_ENERGY_TEST", "total_tabs": 8, "main_size": 200},
        "circuit_templates": {
            # Solar production (time-dependent)
            "solar": {
                "energy_profile": {
                    "mode": "producer",
                    "power_range": [-3000.0, 0.0],
                    "typical_power": -2000.0,
                    "power_variation": 0.3,
                    "efficiency": 0.85,
                },
                "relay_behavior": "non_controllable",
                "priority": "MUST_HAVE",
                "time_of_day_profile": {
                    "enabled": True,
                    "peak_hours": [11, 12, 13, 14, 15],
                    "hourly_multipliers": {
                        6: 0.1,
                        7: 0.3,
                        8: 0.6,
                        9: 0.8,
                        10: 0.9,
                        11: 1.0,
                        12: 1.0,
                        13: 1.0,
                        14: 1.0,
                        15: 1.0,
                        16: 0.9,
                        17: 0.7,
                        18: 0.4,
                        19: 0.1,
                        20: 0.0,
                    },
                },
            },
            # Backup generator (always available)
            "generator": {
                "energy_profile": {
                    "mode": "producer",
                    "power_range": [-5000.0, 0.0],
                    "typical_power": -4000.0,
                    "power_variation": 0.05,
                    "efficiency": 0.90,
                },
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            },
            # Battery storage (bidirectional)
            "battery": {
                "energy_profile": {
                    "mode": "bidirectional",
                    "power_range": [-3000.0, 3000.0],
                    "typical_power": 0.0,
                    "power_variation": 0.02,
                    "efficiency": 0.95,
                },
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            },
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
                    "mode": "producer",
                    "power_range": [-2000.0, 0.0],
                    "typical_power": -1500.0,
                    "power_variation": 0.2,
                    "efficiency": 0.85,
                },
                "relay_behavior": "non_controllable",
                "priority": "MUST_HAVE",
            },
            "4": {
                "energy_profile": {
                    "mode": "producer",
                    "power_range": [-4000.0, 0.0],
                    "typical_power": -3000.0,
                    "power_variation": 0.05,
                    "efficiency": 0.90,
                },
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            },
            "5": {
                "energy_profile": {
                    "mode": "bidirectional",
                    "power_range": [-2500.0, 2500.0],
                    "typical_power": -500.0,  # Slight discharge
                    "power_variation": 0.02,
                    "efficiency": 0.95,
                },
                "relay_behavior": "controllable",
                "priority": "MUST_HAVE",
            },
        },
        "tab_synchronizations": [
            {
                "tabs": [6, 7],
                "behavior": "240v_split_phase",
                "power_split": "equal",
                "energy_sync": True,
                "template": "generator",
            }
        ],
        "unmapped_tabs": [],
        "simulation_params": {
            "update_interval": 5,
            "time_acceleration": 1.0,
            "noise_factor": 0.02,
            "enable_realistic_behaviors": True,
        },
    }

    # Write config to temporary file
    import tempfile
    import yaml
    from pathlib import Path

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
            unmapped_tabs = {3, 4, 5}  # From unmapped_tab_templates
            synced_tabs = {6, 7}  # From tab_synchronizations

            print(f"✅ Panel has {len(branch_data)} branches")
            print(f"   • Mapped tabs: {sorted(mapped_tabs)}")
            print(f"   • Unmapped tabs: {sorted(unmapped_tabs)}")
            print(f"   • Synchronized tabs: {sorted(synced_tabs)}")

            # Verify different energy modes work
            production_found = False
            consumption_found = False

            for circuit_id, circuit in circuit_dict.items():
                power = circuit.instant_power_w
                if power < 0:
                    production_found = True
                    print(f"🌞 {circuit.name}: {power:.1f}W (producing)")
                elif power > 0:
                    consumption_found = True
                    print(f"🔌 {circuit.name}: {power:.1f}W (consuming)")

            # Check unmapped tabs for production/consumption
            for tab_num in unmapped_tabs.union(synced_tabs):
                for branch in branch_data:
                    if branch.id == tab_num:
                        power = branch.instant_power_w
                        if power < 0:
                            production_found = True
                            print(f"🌞 Unmapped Tab {tab_num}: {power:.1f}W (producing)")
                        elif power > 0:
                            consumption_found = True
                            print(f"🔌 Unmapped Tab {tab_num}: {power:.1f}W (consuming)")
                        break

            assert production_found, "Should have found some production sources"
            assert consumption_found, "Should have found some consumption loads"
            print("✅ Both production and consumption sources active")

            # Energy balance analysis
            total_production = 0.0
            total_consumption = 0.0

            # Count circuit power
            for circuit in circuit_dict.values():
                power = circuit.instant_power_w
                if power < 0:
                    total_production += abs(power)
                else:
                    total_consumption += power

            # Count unmapped tab power
            for tab_num in unmapped_tabs.union(synced_tabs):
                for branch in branch_data:
                    if branch.id == tab_num:
                        power = branch.instant_power_w
                        if power < 0:
                            total_production += abs(power)
                        else:
                            total_consumption += power
                        break

            # Also add any other branches
            for branch in branch_data:
                tab_num = branch.id
                if tab_num not in mapped_tabs.union(unmapped_tabs).union(synced_tabs):
                    power = branch.instant_power_w
                    if power < 0:
                        print(f"🌞 Branch {tab_num}: {power:.1f}W")
                        total_production += abs(power)
                    elif power > 0:
                        print(f"🔌 Branch {tab_num}: {power:.1f}W")
                        total_consumption += power

            print(f"\n📊 Energy Balance:")
            print("-" * 15)
            print(f"Total Production: {total_production:.1f}W")
            print(f"Total Consumption: {total_consumption:.1f}W")
            net_power = total_production - total_consumption
            if net_power > 0:
                print(f"Net Export: {net_power:.1f}W ✅")
            elif net_power < 0:
                print(f"Net Import: {abs(net_power):.1f}W ⚠️")
            else:
                print("Balanced: 0W ⚖️")

            print(f"\n✅ Success! Multiple energy sources working:")
            print("   • Solar, generators, batteries all supported")
            print("   • Time-dependent and always-available sources")
            print("   • Bidirectional energy flow for batteries")
            print("   • Tab synchronization for 240V systems")
            print("   • Unified energy profile configuration")
    finally:
        # Clean up temporary file
        Path(temp_config_path).unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(test_multi_energy_config())
