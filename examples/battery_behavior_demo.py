#!/usr/bin/env python3
"""Demonstrate enhanced battery behavior with time-based charging/discharging and dynamic SOE."""

import asyncio
from datetime import datetime
from pathlib import Path

from span_panel_api import SpanPanelClient


def get_behavior_description(hour: int) -> tuple[str, str]:
    """Get expected battery behavior and SOE pattern for a given hour."""
    if 9 <= hour <= 16:
        return "⚡ CHARGING (Solar)", "📈 SOE Rising (Solar charging)"
    elif 17 <= hour <= 21:
        return "🔋 DISCHARGING (Peak)", "📉 SOE Falling (Peak demand)"
    elif hour in [0, 1, 2, 3, 4, 5, 22, 23]:
        return "😴 MINIMAL (Night)", "📊 SOE Slowly falling"
    else:
        return "🌅 TRANSITION", "📊 SOE Moderate"


async def demonstrate_battery_behavior():
    """Demonstrate enhanced battery behavior throughout different times of day."""
    config_path = Path(__file__).parent / "simulation_config_40_circuit_with_battery.yaml"

    print("🔋 ENHANCED BATTERY BEHAVIOR DEMONSTRATION 🔋")
    print("=" * 60)
    print("Features:")
    print("✅ Time-based charging during solar hours (9 AM - 4 PM)")
    print("✅ Time-based discharging during peak hours (5 PM - 9 PM)")
    print("✅ Dynamic SOE calculation based on battery activity")
    print("✅ YAML-driven configuration (no hardcoded values)")
    print("✅ Realistic solar intensity and demand profiles")
    print()

    async with SpanPanelClient(host="battery-demo", simulation_mode=True, simulation_config_path=str(config_path)) as client:

        # Show current actual time for reference
        current_time = datetime.now()
        current_hour = current_time.hour
        behavior_desc, soe_desc = get_behavior_description(current_hour)

        print(f"📅 Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🕐 Current hour: {current_hour}")
        print(f"🔋 Expected behavior: {behavior_desc}")
        print(f"📊 Expected SOE pattern: {soe_desc}")
        print()

        print("🔬 CURRENT BATTERY STATUS:")
        print("-" * 40)

        # Get current data
        circuits = await client.get_circuits()
        storage = await client.get_storage_soe()

        # Find battery systems
        battery_1 = circuits.circuits.additional_properties.get("battery_system_1")
        battery_2 = circuits.circuits.additional_properties.get("battery_system_2")

        if battery_1:
            status_1 = (
                "⚡ Charging"
                if battery_1.instant_power_w < -100
                else "🔋 Discharging" if battery_1.instant_power_w > 100 else "😴 Minimal"
            )
            print(f"Battery System 1: {battery_1.instant_power_w:8.1f}W  {status_1}")

        if battery_2:
            status_2 = (
                "⚡ Charging"
                if battery_2.instant_power_w < -100
                else "🔋 Discharging" if battery_2.instant_power_w > 100 else "😴 Minimal"
            )
            print(f"Battery System 2: {battery_2.instant_power_w:8.1f}W  {status_2}")

        if battery_1 and battery_2:
            combined_power = battery_1.instant_power_w + battery_2.instant_power_w
            combined_status = (
                "⚡ Charging" if combined_power < -200 else "🔋 Discharging" if combined_power > 200 else "😴 Minimal"
            )
            print(f"Combined Power:   {combined_power:8.1f}W  {combined_status}")

        print(f"Storage SOE:      {storage.soe.percentage:8.1f}%")
        print()

        print("📈 SIMULATED 24-HOUR BEHAVIOR PATTERNS:")
        print("-" * 60)

        # Show expected behavior for key hours throughout the day
        demo_hours = [
            (2, "Night"),
            (6, "Early Morning"),
            (10, "Solar Ramp-Up"),
            (12, "Peak Solar"),
            (14, "Continued Solar"),
            (18, "Peak Demand"),
            (20, "High Demand"),
            (23, "Night Wind-Down"),
        ]

        for hour, description in demo_hours:
            behavior, soe_pattern = get_behavior_description(hour)

            # Show YAML-configured values for this hour
            simulation_engine = client._simulation_engine
            if simulation_engine and simulation_engine._config:
                config = simulation_engine._config
                battery_template = config["circuit_templates"]["battery"]
                battery_behavior = battery_template["battery_behavior"]

                # Get solar intensity and demand factor from YAML
                solar_intensity = battery_behavior["solar_intensity_profile"].get(hour, 0.0)
                demand_factor = battery_behavior["demand_factor_profile"].get(hour, 0.0)

                charge_hours = battery_behavior["charge_hours"]
                discharge_hours = battery_behavior["discharge_hours"]
                idle_hours = battery_behavior["idle_hours"]

                if hour in charge_hours:
                    expected_power = f"{battery_behavior['max_charge_power'] * solar_intensity:.0f}W"
                elif hour in discharge_hours:
                    expected_power = f"{battery_behavior['max_discharge_power'] * demand_factor:.0f}W"
                elif hour in idle_hours:
                    idle_range = battery_behavior["idle_power_range"]
                    expected_power = f"{idle_range[0]:.0f} to {idle_range[1]:.0f}W"
                else:
                    expected_power = "Variable"

                print(f"{hour:2d}:00 {description:15s} │ {behavior:25s} │ {soe_pattern:25s} │ ~{expected_power}")

        print()
        print("🎛️  YAML CONFIGURATION HIGHLIGHTS:")
        print("-" * 40)
        print("⚙️  Charge Hours: 9 AM - 4 PM (solar production)")
        print("⚙️  Discharge Hours: 5 PM - 9 PM (peak demand)")
        print("⚙️  Max Charge Power: -3000W (configurable)")
        print("⚙️  Max Discharge Power: +2500W (configurable)")
        print("⚙️  Solar Intensity Profile: Hour-by-hour (0.2 to 1.0)")
        print("⚙️  Demand Factor Profile: Hour-by-hour (0.6 to 1.0)")
        print("⚙️  Idle Power Range: -100W to +100W (minimal activity)")
        print()
        print("✨ All values are configurable in the YAML file!")
        print("✨ No hardcoded behavior - completely data-driven!")


if __name__ == "__main__":
    asyncio.run(demonstrate_battery_behavior())
