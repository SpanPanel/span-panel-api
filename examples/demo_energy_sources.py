#!/usr/bin/env python3
"""
Demonstration of the generic energy profile system supporting various energy sources.

This example shows how the same energy profile structure can be used for:
- Solar panels
- Backup generators
- Wind turbines
- Battery storage (bidirectional)
- Regular consumption loads

Run with: poetry run python examples/demo_energy_sources.py
"""

import asyncio
from pathlib import Path

from span_panel_api.client import SpanPanelClient


async def demo_energy_sources():
    """Demonstrate various energy sources using the generic energy profile system."""

    print("⚡ Generic Energy Profile System Demo")
    print("=" * 50)
    print("Supporting: Solar, Generators, Wind, Batteries & More!")

    config_path = Path(__file__).parent / "simulation_config_32_circuit.yaml"

    async with SpanPanelClient(
        host="energy-sources-demo", simulation_mode=True, simulation_config_path=str(config_path)
    ) as client:

        # Get current state
        circuits = await client.get_circuits()
        circuit_dict = circuits.circuits.additional_properties

        print("\n🔋 Current Energy Sources:")
        print("-" * 30)

        # Show unmapped tabs 30 & 32 (configured as producers)
        for tab_num in [30, 32]:
            circuit_id = f"unmapped_tab_{tab_num}"
            if circuit_id in circuit_dict:
                circuit = circuit_dict[circuit_id]
                power = circuit.instant_power_w
                if power < 0:
                    print(f"🌞 Tab {tab_num}: {abs(power):.1f}W PRODUCTION")
                    print(f"   • Generic producer profile - could be solar, generator, or wind")
                    print(f"   • Produced Energy: {circuit.produced_energy_wh:.2f}Wh")
                    print(f"   • Consumed Energy: {circuit.consumed_energy_wh:.2f}Wh")

        # Show some consumer circuits
        print(f"\n🏠 Energy Consumers:")
        print("-" * 20)

        consumer_examples = ["main_hvac", "ev_charger_garage", "master_bedroom_lights"]
        for circuit_id in consumer_examples:
            if circuit_id in circuit_dict:
                circuit = circuit_dict[circuit_id]
                power = circuit.instant_power_w
                print(f"🔌 {circuit.name}: {power:.1f}W")

        print(f"\n📊 Energy Profile Benefits:")
        print("-" * 25)
        print("✅ Unified configuration for all energy sources")
        print("✅ Support for producers: solar, generators, wind, hydro")
        print("✅ Support for consumers: loads, appliances, HVAC")
        print("✅ Support for bidirectional: batteries, grid-tie inverters")
        print("✅ Tab synchronization for 240V multi-phase systems")
        print("✅ Realistic time-of-day and cycling behaviors")
        print("✅ Smart grid response capabilities")

        print(f"\n🎯 Configuration Examples:")
        print("-" * 22)
        print("Solar Panel:")
        print("  energy_profile:")
        print("    mode: 'producer'")
        print("    power_range: [-5000.0, 0.0]")
        print("    typical_power: -3000.0")
        print("")
        print("Backup Generator:")
        print("  energy_profile:")
        print("    mode: 'producer'")
        print("    power_range: [-8000.0, 0.0]")
        print("    efficiency: 0.92")
        print("")
        print("Battery Storage:")
        print("  energy_profile:")
        print("    mode: 'bidirectional'")
        print("    power_range: [-5000.0, 5000.0]")
        print("    efficiency: 0.95")


if __name__ == "__main__":
    asyncio.run(demo_energy_sources())
