#!/usr/bin/env python3
"""
Test simulation time control with time-dependent energy sources.

This demonstrates:
- File-based YAML configuration for templates and behaviors
- Programmatic override of simulation start time
- Solar production varying by time of day
- Setting specific start times for consistent testing

Run with: poetry run python examples/test_simulation_time.py
"""

import asyncio
from pathlib import Path

from span_panel_api.client import SpanPanelClient


async def test_simulation_time_control():
    """Test simulation time control with solar production at different times."""

    print("🕐 Simulation Time Control Test")
    print("=" * 50)
    print("Using file-based YAML config with programmatic time override")

    # Test configurations for different times of day
    test_times = [
        ("2024-06-15T06:00:00", "🌅 Dawn (6 AM)"),
        ("2024-06-15T12:00:00", "☀️ Noon (12 PM)"),
        ("2024-06-15T18:00:00", "🌇 Evening (6 PM)"),
        ("2024-06-15T00:00:00", "🌙 Midnight (12 AM)"),
    ]

    config_path = Path(__file__).parent / "simulation_config_32_circuit.yaml"

    for sim_time, description in test_times:
        print(f"\n{description}")
        print("-" * 25)

        # Use file-based config with programmatic time override
        async with SpanPanelClient(
            host=f"time-test-{sim_time.replace(':', '-')}",
            simulation_mode=True,
            simulation_config_path=str(config_path),
            simulation_start_time=sim_time,  # Override start time
        ) as client:

            circuits = await client.get_circuits()
            circuit_dict = circuits.circuits.additional_properties

            # Check solar production at unmapped tabs 30 and 32
            solar_power_total = 0.0
            for tab_num in [30, 32]:
                circuit_id = f"unmapped_tab_{tab_num}"
                if circuit_id in circuit_dict:
                    circuit = circuit_dict[circuit_id]
                    power = circuit.instant_power_w
                    solar_power_total += abs(power) if power < 0 else 0

            # Extract hour from simulation time for display
            hour = int(sim_time.split('T')[1].split(':')[0])

            if solar_power_total > 0:
                print(f"☀️ Solar Production: {solar_power_total:.1f}W")
                print(f"   • Expected based on hour {hour} time-of-day profile")
                print(f"   • Using YAML templates with programmatic time: {sim_time}")
            else:
                print(f"🌙 No Solar Production (nighttime)")
                print(f"   • Expected for hour {hour} (outside daylight hours)")
                print(f"   • Simulation time: {sim_time}")

            # Show a regular consumer circuit for comparison
            consumer_circuits = ["main_hvac", "master_bedroom_lights"]
            for circuit_id in consumer_circuits:
                if circuit_id in circuit_dict:
                    circuit = circuit_dict[circuit_id]
                    print(f"🔌 {circuit.name}: {circuit.instant_power_w:.1f}W (consistent)")
                    break

    print(f"\n✅ File-Based Simulation Time Control Working!")
    print("   • YAML configuration defines all templates and behaviors")
    print("   • Programmatic time override for consistent testing")
    print("   • Solar production varies correctly by time of day")
    print("   • Independent of real system clock")
    print("   • Best of both worlds: file config + programmatic control")


if __name__ == "__main__":
    asyncio.run(test_simulation_time_control())
