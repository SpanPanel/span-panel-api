#!/usr/bin/env python3
"""
SPAN Panel API Simulation Mode Demo

This script demonstrates the simulation mode capabilities of the SPAN Panel API client.
It shows how to use various simulation features for testing and development.
"""

import asyncio

from span_panel_api import SpanPanelClient
from span_panel_api.simulation import BranchVariation, CircuitVariation, PanelVariation, StatusVariation


async def demo_basic_simulation() -> None:
    """Demonstrate basic simulation mode functionality."""
    print("=== Basic Simulation Mode Demo ===")

    # Create a simulation mode client
    client = SpanPanelClient(host="localhost", simulation_mode=True)

    async with client:
        # Get basic data from all APIs
        print("\n1. Basic API calls:")

        status = await client.get_status()
        print(f"   Status: Door={status.system.door_state.value}, Network={status.network.eth_0_link}")

        storage = await client.get_storage_soe()
        print(f"   Storage: {storage.soe.percentage}% SOE")

        panel = await client.get_panel_state()
        print(f"   Panel: {panel.main_relay_state.value}, {len(panel.branches)} branches")

        circuits = await client.get_circuits()
        print(f"   Circuits: {len(circuits.circuits.additional_properties)} total")


async def demo_circuit_variations() -> None:
    """Demonstrate circuit-specific variations."""
    print("\n=== Circuit Variations Demo ===")

    client = SpanPanelClient(host="localhost", simulation_mode=True)

    async with client:
        # Get baseline circuits
        baseline = await client.get_circuits()
        circuit_ids = list(baseline.circuits.additional_properties.keys())

        if len(circuit_ids) >= 2:
            circuit_1 = circuit_ids[0]
            circuit_2 = circuit_ids[1]

            print("\n2. Circuit-specific variations:")
            print(f"   Testing circuits: {baseline.circuits.additional_properties[circuit_1].name}")
            print(f"                     {baseline.circuits.additional_properties[circuit_2].name}")

            # Apply specific variations
            variations = {
                circuit_1: CircuitVariation(power_variation=0.5, relay_state="OPEN", priority="NON_ESSENTIAL"),
                circuit_2: CircuitVariation(power_variation=0.8, energy_variation=0.2),
            }

            varied_circuits = await client.get_circuits(variations=variations)

            # Show results
            c1_baseline = baseline.circuits.additional_properties[circuit_1]
            c1_varied = varied_circuits.circuits.additional_properties[circuit_1]

            print("   Circuit 1 changes:")
            print(f"     Relay: {c1_baseline.relay_state.value} -> {c1_varied.relay_state.value}")
            print(f"     Priority: {c1_baseline.priority.value} -> {c1_varied.priority.value}")
            print(f"     Power: {c1_baseline.instant_power_w:.1f}W -> {c1_varied.instant_power_w:.1f}W")


async def demo_global_variations() -> None:
    """Demonstrate global variations affecting all circuits."""
    print("\n=== Global Variations Demo ===")

    client = SpanPanelClient(host="localhost", simulation_mode=True)

    async with client:
        print("\n3. Global power variations:")

        # Test different global variation levels
        for variation in [0.1, 0.3, 0.5]:
            circuits = await client.get_circuits(global_power_variation=variation)

            # Calculate power statistics
            powers = [c.instant_power_w for c in circuits.circuits.additional_properties.values()]
            avg_power = sum(powers) / len(powers)

            print(f"   {variation * 100:3.0f}% variation: avg power = {avg_power:.1f}W")


async def demo_panel_variations() -> None:
    """Demonstrate panel state variations."""
    print("\n=== Panel State Variations Demo ===")

    client = SpanPanelClient(host="localhost", simulation_mode=True)

    async with client:
        print("\n4. Panel state variations:")

        # Test emergency scenarios
        emergency_scenarios = [
            ("Normal Operation", None, None),
            (
                "Grid Outage",
                None,
                PanelVariation(main_relay_state="OPEN", dsm_grid_state="DSM_GRID_DOWN", dsm_state="DSM_OFF_GRID"),
            ),
            ("Branch Failure", {1: BranchVariation(relay_state="OPEN")}, None),
        ]

        for scenario_name, branch_vars, panel_vars in emergency_scenarios:
            panel = await client.get_panel_state(variations=branch_vars, panel_variations=panel_vars)

            branch_1_state = panel.branches[0].relay_state.value if panel.branches else "N/A"

            print(f"   {scenario_name}:")
            print(f"     Main relay: {panel.main_relay_state.value}")
            print(f"     DSM state: {panel.dsm_state}")
            print(f"     Branch 1: {branch_1_state}")


async def demo_status_variations() -> None:
    """Demonstrate status field variations."""
    print("\n=== Status Variations Demo ===")

    client = SpanPanelClient(host="localhost", simulation_mode=True)

    async with client:
        print("\n5. Status field variations:")

        # Test different status scenarios
        scenarios = [
            ("Normal", None),
            ("Door Open", StatusVariation(door_state="OPEN")),
            ("Network Issues", StatusVariation(eth0_link=False, wlan_link=False)),
            ("Maintenance Mode", StatusVariation(door_state="OPEN", proximity_proven=True, eth0_link=False)),
        ]

        for scenario_name, variations in scenarios:
            status = await client.get_status(variations=variations)

            print(f"   {scenario_name}:")
            print(f"     Door: {status.system.door_state.value}")
            print(f"     Ethernet: {status.network.eth_0_link}")
            print(f"     WiFi: {status.network.wlan_link}")
            print(f"     Proximity: {status.system.proximity_proven}")


async def demo_energy_simulation() -> None:
    """Demonstrate energy accumulation over time."""
    print("\n=== Energy Accumulation Demo ===")

    client = SpanPanelClient(host="localhost", simulation_mode=True)

    async with client:
        print("\n6. Energy accumulation over time:")

        # Get initial energy values
        initial_circuits = await client.get_circuits()
        initial_energies = {}

        for circuit_id, circuit in initial_circuits.circuits.additional_properties.items():
            initial_energies[circuit_id] = circuit.consumed_energy_wh

        # Wait a bit and get updated values
        await asyncio.sleep(0.1)

        updated_circuits = await client.get_circuits()

        # Show energy changes
        changes = 0
        for circuit_id, circuit in updated_circuits.circuits.additional_properties.items():
            if circuit_id in initial_energies:
                initial = initial_energies[circuit_id]
                current = circuit.consumed_energy_wh
                if abs(current - initial) > 0.001:  # Small threshold for floating point
                    changes += 1

        print(f"   Energy accumulated in {changes} circuits over 0.1 seconds")
        print("   (Energy accumulation is based on current power consumption)")


async def demo_storage_variations() -> None:
    """Demonstrate storage SOE variations."""
    print("\n=== Storage SOE Variations Demo ===")

    client = SpanPanelClient(host="localhost", simulation_mode=True)

    async with client:
        print("\n7. Storage SOE variations:")

        # Test different battery levels
        baseline = await client.get_storage_soe()
        print(f"   Baseline: {baseline.soe.percentage:.1f}%")

        for variation in [0.1, 0.3, 0.5]:
            storage = await client.get_storage_soe(soe_variation=variation)
            print(f"   {variation * 100:3.0f}% variation: {storage.soe.percentage:.1f}%")


async def demo_live_mode_comparison() -> None:
    """Demonstrate that live mode ignores variations."""
    print("\n=== Live Mode Comparison Demo ===")

    print("\n8. Live mode ignores variations:")

    # Create live mode client
    live_client = SpanPanelClient(host="192.168.1.100", simulation_mode=False)

    try:
        async with live_client:
            # These variations will be completely ignored
            circuits = await live_client.get_circuits(
                variations={"any_id": CircuitVariation(power_variation=999.0)}, global_power_variation=999.0
            )
            print("   Live mode: Variations ignored (would connect to real panel)")
    except Exception as e:
        print(f"   Live mode: Expected connection error - {type(e).__name__}")
        print("   (Variations would be ignored if panel was available)")


async def demo_caching_behavior() -> None:
    """Demonstrate caching behavior in simulation mode."""
    print("\n=== Caching Behavior Demo ===")

    client = SpanPanelClient(host="localhost", simulation_mode=True)

    async with client:
        print("\n9. Caching behavior:")

        # Same parameters should return cached results
        import time

        start_time = time.time()
        circuits1 = await client.get_circuits(global_power_variation=0.2)
        first_call_time = time.time() - start_time

        start_time = time.time()
        circuits2 = await client.get_circuits(global_power_variation=0.2)
        second_call_time = time.time() - start_time

        print(f"   First call: {first_call_time * 1000:.1f}ms")
        print(f"   Cached call: {second_call_time * 1000:.1f}ms")
        print(f"   Same object: {circuits1 is circuits2}")

        # Different parameters create new results
        circuits3 = await client.get_circuits(global_power_variation=0.3)
        print(f"   Different params: {circuits1 is circuits3}")


async def main() -> None:
    """Run all simulation demos."""
    print("SPAN Panel API Simulation Mode Demo")
    print("=" * 40)

    demos = [
        demo_basic_simulation,
        demo_circuit_variations,
        demo_global_variations,
        demo_panel_variations,
        demo_status_variations,
        demo_energy_simulation,
        demo_storage_variations,
        demo_live_mode_comparison,
        demo_caching_behavior,
    ]

    for demo in demos:
        try:
            await demo()
        except Exception as e:
            print(f"   Error in {demo.__name__}: {e}")

    print("\n" + "=" * 40)
    print("Demo completed!")
    print("\nFor more information, see:")
    print("- tests/docs/simulation.md")
    print("- tests/test_simulation_mode.py")


if __name__ == "__main__":
    asyncio.run(main())
