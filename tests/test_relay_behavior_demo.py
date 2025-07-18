"""Test to demonstrate relay state behavior in simulation mode."""

import asyncio
from span_panel_api.client import SpanPanelClient


async def test_relay_behavior_demonstration():
    """Demonstrate how circuit relay states affect panel power and energy.

    This test shows whether:
    1. Opening a circuit relay sets its power to 0
    2. Panel grid power reflects the change
    3. Energy accumulation stops when relay is open
    4. Closing the relay restores normal behavior
    """

    client = SpanPanelClient(
        host="relay-test-demo",
        simulation_mode=True,
        simulation_config_path="examples/simple_test_config.yaml",
        cache_window=0.0,  # Disable caching for real-time data
    )

    async with client:
        # Ensure clean state
        await client.clear_circuit_overrides()

        # Step 1: Get baseline data (all circuits closed)
        print("=== BASELINE: All circuits closed ===")
        circuits_baseline = await client.get_circuits()
        panel_baseline = await client.get_panel_state()

        # Debug: Print all available circuit IDs
        print(f"Available circuit IDs: {list(circuits_baseline.circuits.additional_keys)}")

        # Find a circuit with significant power for testing
        active_circuit_id = None
        for circuit_id in circuits_baseline.circuits.additional_keys:
            circuit = circuits_baseline.circuits[circuit_id]
            if abs(circuit.instant_power_w) > 10:  # Find circuit with meaningful power
                active_circuit_id = circuit_id
                break

        if not active_circuit_id:
            print("No circuits with significant power found")
            return

        baseline_circuit = circuits_baseline.circuits[active_circuit_id]
        print(f"Target circuit: {baseline_circuit.name}")
        print(f"  ID: {active_circuit_id}")
        print(f"  Relay state: {baseline_circuit.relay_state}")
        print(f"  Power: {baseline_circuit.instant_power_w:.1f}W")
        print(f"  Energy consumed: {baseline_circuit.consumed_energy_wh:.1f}Wh")
        print(f"  Energy produced: {baseline_circuit.produced_energy_wh:.1f}Wh")

        baseline_total_power = sum(
            circuits_baseline.circuits[cid].instant_power_w for cid in circuits_baseline.circuits.additional_keys
        )
        print(f"Total circuit power: {baseline_total_power:.1f}W")
        print(f"Panel grid power: {panel_baseline.instant_grid_power_w:.1f}W")
        print(f"Power difference: {abs(panel_baseline.instant_grid_power_w - baseline_total_power):.1f}W")

        # Step 2: Open the circuit relay (turn off the AC)
        print(f"\n=== OPENING RELAY: {baseline_circuit.name} ===")
        print(f"Setting relay_state OPEN for circuit ID: {active_circuit_id}")
        await client.set_circuit_overrides({active_circuit_id: {"relay_state": "OPEN"}})
        circuits_open = await client.get_circuits()
        panel_open = await client.get_panel_state()

        # Check if the circuit still exists in the response
        if active_circuit_id not in circuits_open.circuits.additional_keys:
            print(f"ERROR: Circuit {active_circuit_id} not found in response after override!")
            print(f"Available circuits: {list(circuits_open.circuits.additional_keys)}")
            return

        open_circuit = circuits_open.circuits[active_circuit_id]
        print(f"  Relay state: {open_circuit.relay_state}")
        print(f"  Power: {open_circuit.instant_power_w:.1f}W")
        print(f"  Energy consumed: {open_circuit.consumed_energy_wh:.1f}Wh")
        print(f"  Energy produced: {open_circuit.produced_energy_wh:.1f}Wh")

        open_total_power = sum(circuits_open.circuits[cid].instant_power_w for cid in circuits_open.circuits.additional_keys)
        print(f"Total circuit power: {open_total_power:.1f}W")
        print(f"Panel grid power: {panel_open.instant_grid_power_w:.1f}W")
        print(f"Power difference: {abs(panel_open.instant_grid_power_w - open_total_power):.1f}W")

        # Calculate changes
        circuit_power_change = open_circuit.instant_power_w - baseline_circuit.instant_power_w
        total_power_change = open_total_power - baseline_total_power
        panel_power_change = panel_open.instant_grid_power_w - panel_baseline.instant_grid_power_w

        print(f"\n=== CHANGES WHEN RELAY OPENED ===")
        print(f"Circuit power change: {circuit_power_change:.1f}W")
        print(f"Total circuit power change: {total_power_change:.1f}W")
        print(f"Panel grid power change: {panel_power_change:.1f}W")

        # Step 3: Wait a moment and check energy accumulation
        print(f"\n=== WAITING 2 SECONDS TO CHECK ENERGY ACCUMULATION ===")
        await asyncio.sleep(2)

        circuits_after_wait = await client.get_circuits()
        panel_after_wait = await client.get_panel_state()

        wait_circuit = circuits_after_wait.circuits[active_circuit_id]
        energy_consumed_change = wait_circuit.consumed_energy_wh - open_circuit.consumed_energy_wh
        energy_produced_change = wait_circuit.produced_energy_wh - open_circuit.produced_energy_wh

        print(f"Energy consumed change: {energy_consumed_change:.3f}Wh")
        print(f"Energy produced change: {energy_produced_change:.3f}Wh")
        print(f"Circuit power during wait: {wait_circuit.instant_power_w:.1f}W")

        # Step 4: Close the relay (turn AC back on)
        print(f"\n=== CLOSING RELAY: {baseline_circuit.name} ===")
        await client.set_circuit_overrides({active_circuit_id: {"relay_state": "CLOSED"}})
        circuits_closed = await client.get_circuits()
        panel_closed = await client.get_panel_state()

        closed_circuit = circuits_closed.circuits[active_circuit_id]
        print(f"  Relay state: {closed_circuit.relay_state}")
        print(f"  Power: {closed_circuit.instant_power_w:.1f}W")
        print(f"  Energy consumed: {closed_circuit.consumed_energy_wh:.1f}Wh")
        print(f"  Energy produced: {closed_circuit.produced_energy_wh:.1f}Wh")

        closed_total_power = sum(
            circuits_closed.circuits[cid].instant_power_w for cid in circuits_closed.circuits.additional_keys
        )
        print(f"Total circuit power: {closed_total_power:.1f}W")
        print(f"Panel grid power: {panel_closed.instant_grid_power_w:.1f}W")
        print(f"Power difference: {abs(panel_closed.instant_grid_power_w - closed_total_power):.1f}W")

        # Verify behavior
        print(f"\n=== BEHAVIOR VERIFICATION ===")
        print(f"1. Circuit power when OPEN: {open_circuit.instant_power_w:.1f}W (should be 0)")
        print(f"2. Circuit power when CLOSED: {closed_circuit.instant_power_w:.1f}W (should be non-zero)")
        print(
            f"3. Panel power reflects circuit changes: Panel changed by {panel_power_change:.1f}W when circuit changed by {circuit_power_change:.1f}W"
        )
        print(
            f"4. Energy accumulation when OPEN: {energy_consumed_change:.3f}Wh consumed, {energy_produced_change:.3f}Wh produced"
        )


if __name__ == "__main__":
    asyncio.run(test_relay_behavior_demonstration())
