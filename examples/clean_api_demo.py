#!/usr/bin/env python3
"""
Demonstration of the new clean YAML-based simulation API.

This example shows:
1. Clean API - no variation parameters on core methods
2. YAML configuration for realistic behaviors
3. Dynamic override system for temporary variations
4. Multiple panel configurations

Run with: poetry run python examples/clean_api_demo.py
"""

import asyncio
from pathlib import Path

from span_panel_api.client import SpanPanelClient


async def demo_clean_api():
    """Demonstrate the clean YAML-based simulation API."""

    print("🏠 SPAN Panel API - Clean YAML Simulation Demo")
    print("=" * 50)

    # Example 1: Basic simulation with YAML configuration
    print("\n📋 Example 1: YAML-configured simulation")
    print("-" * 40)

    config_path = Path(__file__).parent / "simulation_config_32_circuit.yaml"

    async with SpanPanelClient(
        host="demo-house-32", simulation_mode=True, simulation_config_path=str(config_path)
    ) as client:

        # Clean API - no variation parameters needed
        circuits = await client.get_circuits()
        panel = await client.get_panel_state()
        storage = await client.get_storage_soe()
        status = await client.get_status()

        print(f"✅ Connected to simulated panel: {status.system.serial}")
        print(f"📊 Found {len(circuits.circuits.additional_properties)} circuits")
        print(f"⚡ Grid power: {panel.instant_grid_power_w:.1f}W")
        print(f"🔋 Battery SOE: {storage.soe.percentage:.0f}%")

        # Show some interesting circuits
        print("\n🔌 Sample circuit data:")
        for _circuit_id, circuit in list(circuits.circuits.additional_properties.items())[:5]:
            print(f"  • {circuit.name}: {circuit.instant_power_w:.1f}W ({circuit.relay_state})")

    # Example 2: Dynamic overrides for testing scenarios
    print("\n🎛️  Example 2: Dynamic circuit overrides")
    print("-" * 40)

    async with SpanPanelClient(
        host="demo-house-test", simulation_mode=True, simulation_config_path=str(config_path)
    ) as client:

        # Get baseline data
        circuits_before = await client.get_circuits()
        ev_before = circuits_before.circuits.additional_properties.get("ev_charger_garage")

        if ev_before:
            print(f"🚗 EV Charger before override: {ev_before.instant_power_w:.1f}W")
        else:
            print("🚗 EV Charger circuit not found, using default values")

        # Apply dynamic override to simulate high-power charging
        await client.set_circuit_overrides(
            {"ev_charger_garage": {"power_override": 11000.0, "relay_state": "CLOSED"}}  # Max charging power
        )

        # Get updated data
        circuits_after = await client.get_circuits()
        ev_after = circuits_after.circuits.additional_properties.get("ev_charger_garage")

        if ev_after:
            print(f"🚗 EV Charger after override: {ev_after.instant_power_w:.1f}W")
        else:
            print("🚗 EV Charger circuit not found after override")
        print("✅ Dynamic override applied successfully!")

        # Clear overrides to return to YAML-defined behavior
        await client.clear_circuit_overrides()

        circuits_restored = await client.get_circuits()
        ev_restored = circuits_restored.circuits.additional_properties.get("ev_charger_garage")

        if ev_restored:
            print(f"🚗 EV Charger after clearing: {ev_restored.instant_power_w:.1f}W")
        else:
            print("🚗 EV Charger circuit not found after clearing")
        print("✅ Overrides cleared - back to YAML configuration")

    # Example 3: Global overrides for stress testing
    print("\n🌍 Example 3: Global power multiplier")
    print("-" * 40)

    async with SpanPanelClient(
        host="demo-stress-test", simulation_mode=True, simulation_config_path=str(config_path)
    ) as client:

        # Get baseline total power
        circuits_normal = await client.get_circuits()
        total_normal = sum(circuit.instant_power_w for circuit in circuits_normal.circuits.additional_properties.values())

        print(f"⚡ Total power (normal): {total_normal:.1f}W")

        # Apply 2x power multiplier for stress testing
        await client.set_circuit_overrides(global_overrides={"power_multiplier": 2.0})

        circuits_stressed = await client.get_circuits()
        total_stressed = sum(
            circuit.instant_power_w for circuit in circuits_stressed.circuits.additional_properties.values()
        )

        print(f"⚡ Total power (2x multiplier): {total_stressed:.1f}W")
        print(f"🔥 Power increase: {(total_stressed/total_normal):.1f}x")

    # Example 4: Multiple configurations
    print("\n🏘️  Example 4: Different house configurations")
    print("-" * 40)

    # Small house simulation (using same config for demo purposes)
    async with SpanPanelClient(
        host="small-house", simulation_mode=True, simulation_config_path=str(config_path)
    ) as small_client:

        small_circuits = await small_client.get_circuits()
        print(f"🏠 Small house: {len(small_circuits.circuits.additional_properties)} circuits")

    # Large house simulation (would use 40-circuit YAML)
    async with SpanPanelClient(
        host="large-house", simulation_mode=True, simulation_config_path=str(config_path)  # Same config, different serial
    ) as large_client:

        large_circuits = await large_client.get_circuits()
        large_status = await large_client.get_status()
        print(f"🏰 Large house: {len(large_circuits.circuits.additional_properties)} circuits")
        print(f"   Serial: {large_status.system.serial}")

    print("\n✨ Demo:")
    print("  • Clean API - no variation parameters on core methods")
    print("  • YAML configuration defines realistic behaviors")
    print("  • Dynamic overrides for temporary test scenarios")
    print("  • Multiple panel configurations via different YAML files")
    print("  • Realistic time-of-day patterns, cycling, and smart behaviors")


async def demo_yaml_features():
    """Demonstrate specific YAML configuration features."""

    print("\n🔧 YAML Configuration Features Demo")
    print("=" * 50)

    config_path = Path(__file__).parent / "simulation_config_32_circuit.yaml"

    async with SpanPanelClient(host="feature-demo", simulation_mode=True, simulation_config_path=str(config_path)) as client:

        circuits = await client.get_circuits()

        print("\n🏠 Circuit Templates Demonstrated:")

        # Show different circuit types
        circuit_examples = {
            "exterior_lights": "🌙 Time-of-day profile (nighttime peak)",
            "main_hvac": "🔄 Cycling behavior (20min on, 40min off)",
            "solar_inverter_main": "☀️ Solar production (daylight hours)",
            "ev_charger_garage": "🧠 Smart grid response",
            "refrigerator": "❄️ Always-on with compressor cycling",
            "pool_pump": "🏊 Scheduled operation (2h on, 4h off)",
        }

        for circuit_id, description in circuit_examples.items():
            if circuit_id in circuits.circuits.additional_properties:
                circuit = circuits.circuits.additional_properties[circuit_id]
                print(f"  • {description}")
                print(f"    {circuit.name}: {circuit.instant_power_w:.1f}W ({circuit.priority})")

        print("\n📈 Realistic Behaviors Active:")
        print("  • Time-based variations (lighting, solar)")
        print("  • Equipment cycling (HVAC, appliances)")
        print("  • Smart load response (EV charger)")
        print("  • Seasonal efficiency changes")
        print("  • Random noise (±2%) for realism")


if __name__ == "__main__":
    # Run both demos
    asyncio.run(demo_clean_api())
    asyncio.run(demo_yaml_features())
